using System.Reflection;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using STS2Connector.Authority;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.NativeLogical;
using STS2Connector.PlayerEnvironment.Protocol;
using STS2Platform.NativeFoundation;
using Xunit;

namespace STS2Connector.Host.Tests;

public sealed class SourceProducerRegressionTests
{
    private static TextMenuFrame Frame() => new(new("1.0.0", PlayerEnvironmentContract.SnapshotSchema,
        "original", 1, DateTimeOffset.UnixEpoch, "interactive", null,
        new("owner", "event_option", "ready", null, "surface", new(new JsonObject { ["kind"] = "event_option" }, new JsonObject()),
            Array.Empty<PlayerEnvironmentInteractionCapability>()), new[] { new PlayerEnvironmentReferent("subject", "option", "entity", "Original option",
            new(true, true, false, true, "native_visible_fact"), null, null) },
        new("sts2.player-environment/bound-actions-1", "complete", 0, 0, 512, "finite", Array.Empty<PlayerEnvironmentBoundAction>()),
        Array.Empty<PlayerEnvironmentReadOpportunity>(), new("complete", "public", "complete", Array.Empty<string>(), Array.Empty<string>()),
        new("runtime", "environment"), new("player_visible_v1", "current_page", false, "omit")), "owner", new[] {
        new TextMenuLeaf("original-option", "root", "activate", "Original option", "subject",
            Array.Empty<PlayerEnvironmentBoundActionArgument>(), () => throw new Exception("must not dispatch")) }) { GameContinuityId = "run" };

    [Fact]
    public void LegacySourceWitnessUsesExactOriginalObjectsWithoutChangingPublicActionOrDispatch()
    {
        var original = Frame().Leaves[0]; object room = new(), option = new();
        var joined = NativeTextMenuFrameBuilder.WithLegacySourceWitness(original, room, option);
        Assert.Same(original.Dispatch, joined.Dispatch);
        Assert.Equal(JsonSerializer.Serialize(original, ConnectorMod._jsonOptions), JsonSerializer.Serialize(joined, ConnectorMod._jsonOptions));
        var prefix = new NativeSourceInputPrefix("activate", room, option, new Dictionary<string, object>(), "native", "native");
        Assert.True(NativeLogicalService.MatchesPhysicalSourceLeaf(joined, prefix, true));
        Assert.False(NativeLogicalService.MatchesPhysicalSourceLeaf(original, prefix, true));
        Assert.False(NativeLogicalService.MatchesPhysicalSourceLeaf(joined, prefix with { Owner = new() }, true));
        Assert.False(NativeLogicalService.MatchesPhysicalSourceLeaf(joined, prefix with { Subject = new() }, true));
        Assert.False(NativeLogicalService.MatchesPhysicalSourceLeaf(joined, prefix with { Verb = "claim" }, true));
    }

    [Fact]
    public void ExactNativeShowScreenAssignsRewardOwnerFieldsBeforePushCanInvokeReady()
    {
        var method = typeof(MegaCrit.Sts2.Core.Nodes.Screens.NRewardsScreen).GetMethod("ShowScreen")!;
        var instructions = HarmonyLib.PatchProcessor.GetOriginalInstructions(method);
        int Field(string name) => instructions.FindIndex(code => code.opcode == System.Reflection.Emit.OpCodes.Stfld
            && code.operand is FieldInfo field && field.Name == name);
        int push = instructions.FindIndex(code => code.operand is MethodInfo called
            && called.DeclaringType == typeof(MegaCrit.Sts2.Core.Nodes.Screens.Overlays.NOverlayStack) && called.Name == "Push");
        Assert.True(Field("_rewardsSet") >= 0 && Field("_rewardsSet") < push);
        Assert.True(Field("_isTerminal") >= 0 && Field("_isTerminal") < push);
        Assert.True(Field("_runState") >= 0 && Field("_runState") < push);
        Assert.Equal(typeof(void), typeof(MegaCrit.Sts2.Core.Nodes.Screens.NRewardsScreen).GetMethod("_Ready")!.ReturnType);
    }

    [Theory]
    [InlineData("burst")]
    [InlineData("byte_full")]
    [InlineData("snapshot_throw")]
    public void NativeRewardBurstUsesMeasuredBytesAndKeepsActualCapacityOrCaptureFailures(string scenario)
    {
        using var requests = RequestTestDriver.Namespace();
        var authority = new MutationControllerCoordinator("runtime", enableDeadlineTimer: false);
        var frame = Frame(); byte[] publicBytes = Encoding.UTF8.GetBytes("{\"source\":\"small\"}");
        bool throwCapture = false; int captures = 0;
        using var owner = new NativeLogicalService(() => frame, () => "run", requests, executionAllowed: () => true,
            clientActive: authority.IsActiveClient, registerSourceClient: authority.Register,
            actualRunState: () => null, sourceIdentity: () => (null!, "fixture"),
            captureSource: () => {
                captures++;
                if (throwCapture) throw new NativeLogicalSourceCaptureException("source_snapshot", new InvalidOperationException("original fixture failure"));
                return new(frame, new("original", publicBytes, DateTimeOffset.UnixEpoch), null);
            });
        owner.Initialize();
        owner.InstallPublicationProfile(NativeLogicalPublicationProfile.ProfileId, NativeLogicalPublicationProfile.DefinitionSha256,
            NativeLogicalPublicationProfile.RequiredCoverage);
        // Only the detached Store encoder is blocked. Native capture, source
        // admission, Hub reservations and completion all use production code.
        object gate = typeof(NativeLogicalCaptureStore).GetField("gate", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(owner.Store)!;
        NativeLogicalSourceRecordingAttachment source;
        Monitor.Enter(gate);
        try {
            source = owner.AttachSource(requireOrderedBasis: true, requirePublicBasis: true);
            if (scenario == "byte_full") publicBytes = new byte[2 * 1024 * 1024];
            if (scenario == "snapshot_throw") throwCapture = true;
            for (int i = 0; i < (scenario == "burst" ? 6 : 1); i++) owner.Publish("native_reward_catalog", "UpdateScreenState_returned");
        }
        finally { Monitor.Exit(gate); }
        NativeLogicalEventBatch? batch = null;
        Assert.True(SpinWait.SpinUntil(() => {
            batch = owner.SourceEvents(source, source.Current, source.Current.Subscription.StartingCursor);
            return batch.Events.Count == (scenario == "burst" ? 7 : 2);
        }, 3000));
        Assert.Equal(scenario == "burst" ? 7 : 2, captures); // No automatic recapture.
        Assert.Equal(Enumerable.Range(1, batch!.Events.Count).Select(i => i.ToString()), batch.Events.Select(row => row.Event.PublicationIndex));
        if (scenario == "burst") Assert.All(batch.Events, row => { Assert.NotNull(row.Event.SourcePayloadRef); Assert.Null(row.Event.MissingReason); });
        else {
            var failure = batch.Events[1].Event;
            Assert.Null(failure.SourcePayloadRef);
            Assert.Equal("native_capture_failed:" + (scenario == "byte_full" ? "source_copied_payload_capacity"
                : "source_snapshot:InvalidOperationException"), failure.MissingReason);
        }
    }
}

[Collection("Native source carrier observer isolation")]
public sealed class SourceEventAcceptanceTests
{
    [Fact]
    public async Task ActualNativeChosenSetsAcceptanceBeforeAwaitingTheOriginalEventEffect()
    {
        var effect = new TaskCompletionSource();
        var eventModel = (MegaCrit.Sts2.Core.Models.Events.Neow)System.Runtime.CompilerServices.RuntimeHelpers.GetUninitializedObject(typeof(MegaCrit.Sts2.Core.Models.Events.Neow));
        var option = new MegaCrit.Sts2.Core.Events.EventOption(eventModel, () => effect.Task,
            new MegaCrit.Sts2.Core.Localization.LocString("events", "FIXTURE.title"),
            new MegaCrit.Sts2.Core.Localization.LocString("events", "FIXTURE.description"), "FIXTURE",
            Array.Empty<MegaCrit.Sts2.Core.HoverTips.IHoverTip>());
        var terminal = new List<string>();
        using var registration = NativeSourceInputProvider.Register(_ => new(), (_, delivery, _) => terminal.Add(delivery),
            observeOriginalEventChoices: true);
        var original = NativeSourceInputProvider.Begin(new("activate", new(), option, new Dictionary<string, object>(),
            "NEventRoom.OptionButtonClicked", "NEventRoom.OptionButtonClicked"));
        Assert.False(option.WasChosen);
        var actualTask = option.Chosen();
        Assert.True(option.WasChosen); Assert.False(actualTask.IsCompleted);
        Assert.True(NativeSourceInputProvider.ObserveOriginalEventChosen(option, actualTask));
        NativeSourceInputProvider.Finish(original);
        Assert.Equal("delivered", Assert.Single(terminal));
        effect.SetResult(); await actualTask; Assert.Single(terminal);
    }

    [Theory]
    [InlineData("valid")]
    [InlineData("other_subject")]
    [InlineData("not_chosen")]
    [InlineData("other_callback")]
    [InlineData("not_opted_in")]
    public void OriginalChosenReceiptRequiresExactLexicalOptionAndNativeWasChosen(string scenario)
    {
        object option = new(), token = new(); var terminal = new List<string>();
        using var registration = NativeSourceInputProvider.Register(_ => token, (actual, delivery, _) => {
            Assert.Same(token, actual); terminal.Add(delivery);
        }, observeOriginalEventChoices: scenario != "not_opted_in");
        var original = NativeSourceInputProvider.Begin(new("activate", new(), option, new Dictionary<string, object>(),
            scenario == "other_callback" ? "other" : "NEventRoom.OptionButtonClicked", "NEventRoom.OptionButtonClicked"));
        var pendingNativeTask = new TaskCompletionSource();
        bool accepted = NativeSourceInputProvider.ObserveOriginalEventChosenCore(scenario == "other_subject" ? new() : option,
            scenario != "not_chosen", pendingNativeTask.Task);
        Assert.Equal(scenario == "valid", accepted);
        NativeSourceInputProvider.Finish(original);
        Assert.Equal(scenario == "valid" ? "delivered" : "unknown", Assert.Single(terminal));
        // Task is deliberately unfinished: acceptance is not an effect/Commit proof.
        Assert.False(pendingNativeTask.Task.IsCompleted);
    }
}
