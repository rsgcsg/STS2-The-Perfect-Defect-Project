using System.Text.Json;
using System.Runtime.CompilerServices;
using MegaCrit.Sts2.Core.GameActions;
using STS2Connector.PlayerEnvironment;
using STS2HumanAnnotator.Core;
using STS2Platform.NativeFoundation;
using Xunit;

namespace STS2HumanAnnotator.SourceNative.Tests;

public sealed class SourceNativePublicBasisProducerTests
{
    private static NativeSourceInputInvocation Begin(SourceNativeProducerTests.Fixture f) =>
        Assert.IsType<NativeSourceInputInvocation>(NativeSourceInputProvider.Begin(new("confirm", f.PhysicalOwner, null,
            new Dictionary<string, object>(), "fixture.original_physical_prefix", nameof(PlayCardAction), typeof(PlayCardAction))));
    private static void Pass(SourceNativeProducerTests.Fixture f)
    { var audit = SourceSessionAuditV4.Audit(f.Store.DirectoryPath); Assert.True(audit.Status == "pass", string.Join(",", audit.Errors)); }

    [Theory]
    [InlineData(3)]
    [InlineData(4)]
    public async Task ExplicitPrivateRewardPrefixJoinIsSource4OnlyAndDoesNotRenameTheOriginalNativeLeaf(int version)
    {
        using var f = new SourceNativeProducerTests.Fixture(version);
        f.Invoke(() =>
        {
            f.PhysicalLeafVerb = "claim_reward"; f.PhysicalSourcePrefixVerb = "activate";
            var original = NativeSourceInputProvider.Begin(new("activate", f.PhysicalOwner, null,
                new Dictionary<string, object>(), "NRewardButton.OnRelease", "NRewardButton.OnRelease"));
            NativeSourceInputProvider.Accepted(original); NativeSourceInputProvider.Finish(original);
        });
        await f.Close();
        var row = Assert.Single(f.Rows<SourceNativeInputWitnessV2>("native-input-witnesses.jsonl"));
        Assert.Equal(version == 4 ? "exact" : "unmapped", row.Outcome.MappingStatus);
        if (version == 4) Assert.Equal("claim_reward", row.Outcome.SelectedAction!.Verb);
        Assert.Equal("pass", version == 4 ? SourceSessionAuditV4.Audit(f.Store.DirectoryPath).Status
            : SourceSessionAuditV3.Audit(f.Store.DirectoryPath).Status);
    }

    [Fact]
    public async Task OriginalCancelStageUsesOneOriginalJointPublicationWithoutAddingAnInput()
    {
        using var f = new SourceNativeProducerTests.Fixture(4);
        f.Invoke(() =>
        {
            var anchor = f.Owner.CapturePhysicalSourceStageAnchor(); Assert.NotNull(anchor);
            f.Surface = "cancel-finalizer";
            f.Owner.CapturePhysicalSourceStage(anchor, new("card_play_canceled", f.PhysicalOwner, f.PhysicalOwner,
                new Dictionary<string, object> { ["hand"] = f.PhysicalOwner }, "native_finished_false", null));
            Assert.Equal(2, f.SourceCaptures);
        });
        await f.Close(); Pass(f);
        var canceled = Assert.Single(f.Rows<SourcePublicObservationV2>("public-observations.jsonl"), row => row.Phase == "card_play_canceled");
        Assert.Equal("native_input_callback", canceled.SourceSeam);
        Assert.Equal("ordinary-cancel-finalizer", canceled.CapturedParts!.PublicBasis!.SnapshotId);
        Assert.Equal("native-owner-physical", canceled.CapturedParts.NativeStage!.OwnerRef);
        Assert.Null(canceled.CapturedParts.NativeStage.NoSubmission);
        Assert.Empty(f.Rows<SourceNativeInputWitnessV2>("native-input-witnesses.jsonl"));
    }

    [Fact]
    public async Task OldCancelCallbackAnchorCannotBeAdoptedBySuccessorEpoch()
    {
        using var f = new SourceNativeProducerTests.Fixture(4);
        f.Invoke(() =>
        {
            var old = f.Owner.CapturePhysicalSourceStageAnchor(); Assert.NotNull(old);
            f.Setup(true);
            f.Owner.CapturePhysicalSourceStage(old, new("card_play_canceled", f.PhysicalOwner, f.PhysicalOwner,
                new Dictionary<string, object> { ["hand"] = f.PhysicalOwner }, "native_finished_false", null));
        });
        await Assert.ThrowsAnyAsync<Exception>(() => f.Worker.Completion.WaitAsync(TimeSpan.FromSeconds(4)));
        Assert.False(f.Store.GetSourceStatusV2()!.AccountingComplete);
        Assert.DoesNotContain(f.Rows<SourcePublicObservationV2>("public-observations.jsonl"), row => row.Phase == "card_play_canceled");
    }

    [Fact]
    public async Task NoAgentSource4UsesOneCaptureFactoryAndPersistsOrdinaryBasisThroughOriginalWorker()
    {
        using var f = new SourceNativeProducerTests.Fixture(4);
        f.Invoke(() =>
        {
            Assert.Equal(1, f.SourceCaptures); Assert.Equal(0, f.LegacyCaptures);
            f.Surface = "input-original"; var original = Begin(f);
            NativeSourceInputProvider.Accepted(original); NativeSourceInputProvider.Finish(original);
            Assert.Equal(2, f.SourceCaptures); Assert.Equal(0, f.LegacyCaptures);
        });
        await f.Close(); Pass(f);
        var observation = Assert.Single(f.Rows<SourcePublicObservationV2>("public-observations.jsonl"));
        var input = Assert.Single(f.Rows<SourceNativeInputWitnessV2>("native-input-witnesses.jsonl"));
        Assert.NotNull(observation.CapturedParts!.PublicBasis); Assert.NotNull(input.CapturedParts!.PublicBasis);
        Assert.Equal("ordinary-input-original", input.CapturedParts.PublicBasis.SnapshotId);
        Assert.NotEqual(input.PreCapture!.SnapshotId, input.CapturedParts.PublicBasis.SnapshotId);
        Assert.Equal("native_prefix_frozen", input.BasisOrder!.Status);
        Assert.Equal("native-owner-physical", input.Outcome.NativeFacts!.OwnerRef);
        Assert.Equal("native_input", input.Outcome.NativeFacts.Origin);
        Assert.Equal(2, f.SourceCaptures); Assert.Equal(0, f.LegacyCaptures);
        var bytes = File.ReadAllBytes(Path.Combine(f.Store.DirectoryPath, input.CapturedParts.PublicBasis.PayloadRef));
        Assert.Equal(input.CapturedParts.PublicBasis.Sha256, SourceSessionContract.Sha256(bytes));
        using var body = JsonDocument.Parse(bytes); Assert.Equal("ordinary-input-original", body.RootElement.GetProperty("snapshot").GetProperty("snapshot_id").GetString());
        Assert.Empty(body.RootElement.GetProperty("reads").EnumerateArray());
    }

    [Theory]
    [InlineData(true, false)]
    [InlineData(false, true)]
    public async Task IndependentMissingPartNeverDiscardsTheOtherOriginal(bool nativeMissing, bool neutralMissing)
    {
        using var f = new SourceNativeProducerTests.Fixture(4, initial => { initial.PartialNative = nativeMissing; initial.MissingNeutral = neutralMissing; initial.OnCapture = null; });
        f.Invoke(() => { var original = Begin(f); NativeSourceInputProvider.Accepted(original); NativeSourceInputProvider.Finish(original); });
        await f.Close(); Pass(f);
        var observation = Assert.Single(f.Rows<SourcePublicObservationV2>("public-observations.jsonl"));
        var input = Assert.Single(f.Rows<SourceNativeInputWitnessV2>("native-input-witnesses.jsonl"));
        Assert.Equal(nativeMissing, observation.Capture == null); Assert.Equal(nativeMissing, input.PreCapture == null);
        Assert.Equal(neutralMissing, observation.CapturedParts!.PublicBasis == null); Assert.Equal(neutralMissing, input.CapturedParts!.PublicBasis == null);
        Assert.Equal(nativeMissing ? "capture_missing" : "exact", input.Outcome.MappingStatus); Assert.Equal("delivered", input.Outcome.Delivery);
        if (neutralMissing) Assert.Equal("synthetic_neutral_capture_failed", input.CapturedParts.PublicBasisMissingReason);
    }

    [Fact]
    public async Task ReentrantPublicationRefusesBothOriginalNeutralAcquisitionsInsteadOfBackfilling()
    {
        using var f = new SourceNativeProducerTests.Fixture(4);
        f.Invoke(() =>
        {
            f.OnCapture = () => { f.OnCapture = null; f.Owner.Publish("native_owner_ready", "nested_original_capture"); };
            var original = Begin(f); NativeSourceInputProvider.Accepted(original); NativeSourceInputProvider.Finish(original);
        });
        await f.Close(); Pass(f);
        var input = Assert.Single(f.Rows<SourceNativeInputWitnessV2>("native-input-witnesses.jsonl"));
        Assert.Equal("unproven", input.BasisOrder!.Status); Assert.Null(input.CapturedParts!.PublicBasis);
        var nested = Assert.Single(f.Rows<SourcePublicObservationV2>("public-observations.jsonl"), row => row.Position.PublicationIndex == "2");
        Assert.Null(nested.CapturedParts!.PublicBasis); Assert.NotNull(nested.CapturedParts.PublicBasisMissingReason);
    }
    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task ActualOriginalCarrierCallbacksPersistExactAliasesOrAnHonestGapWithoutChangingDelivery(bool observerFailure)
    {
        using var f = new SourceNativeProducerTests.Fixture(4);
        var carrier = (GameAction)RuntimeHelpers.GetUninitializedObject(typeof(PlayCardAction));
        f.Invoke(() =>
        {
            f.ThrowCarrierAlias = observerFailure;
            var original = Begin(f); NativeSourceInputProvider.BindSubmitted(carrier); NativeSourceInputProvider.Finish(original);
            Assert.True(NativeSourceInputProvider.ObserveAccepted(carrier));
        });
        await f.Close(); Pass(f);
        var input = Assert.Single(f.Rows<SourceNativeInputWitnessV2>("native-input-witnesses.jsonl"));
        Assert.Equal("delivered", input.Outcome.Delivery);
        if (observerFailure)
        {
            Assert.Contains("native_source_carrier_observer_failed", input.Outcome.ReasonCode!);
            Assert.Empty(input.Outcome.NativeFacts!.Carriers);
        }
        else
        {
            Assert.Null(input.Outcome.ReasonCode);
            Assert.Equal(new[] { "game_action_submitted", "game_action_accepted" }, input.Outcome.NativeFacts!.Carriers.Select(item => item.Kind));
            Assert.Single(input.Outcome.NativeFacts.Carriers.Select(item => item.CarrierRef).Distinct());
        }
    }

}
