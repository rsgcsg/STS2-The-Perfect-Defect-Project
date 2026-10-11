using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using STS2Connector.PlayerEnvironment.Witness;
using STS2HumanAnnotator.Core;
using STS2HumanAnnotator.Mod;
using STS2Platform.NativeFoundation;
using Xunit;

// The production SourceInput partial is linked unchanged. This test context
// supplies only the profile fields and Human types owned by other Mod partials.
namespace STS2HumanAnnotator.Mod
{
    internal readonly record struct NativeUiScopeEntry(bool Entered, bool DeferredFailure,
        string? ActionWitnessId = null, bool CarrierBindingFailed = false,
        NativeSourceInputInvocation? SourceInvocation = null, bool SourceInvocationBorrowed = false);

    internal static partial class RecorderRuntime
    {
        private static string _activeCaptureProfileId = SourceSessionContractV3.ProfileId;
        private static bool IsSourceRecording => _activeCaptureProfileId is
            SourceSessionContract.ProfileId or SourceSessionContractV2.ProfileId or SourceSessionContractV3.ProfileId;
        internal sealed class SelectorInput { }
        private static IDisposable? StageCardPlay(NHandCardHolder holder) =>
            throw new InvalidOperationException("Human staging was not requested by this Source test");
        internal static void TestProfile(bool source) => _activeCaptureProfileId = source ? SourceSessionContractV3.ProfileId : "human-test";
        internal static SelectorInputHandle TestBegin(object owner, string method, object? control = null, string? callback = null)
        {
            Assert.True(TryBeginSourceSelectorInput(owner, method, null, "confirm", control, callback, out var input));
            return Assert.IsType<SelectorInputHandle>(input);
        }
        internal static NativeUiScopeEntry TestCallback(object owner, string method, object? control = null)
        {
            Assert.True(TryEnterSourceInputScope(method, method, owner,
                new ProcessLocalObservedAction("confirm", null, new Dictionary<string, object>(StringComparer.Ordinal)),
                out var scope, sourceControl: control));
            return scope;
        }
        internal static void TestPreActivationPrefix(object owner)
        {
            TestProfile(false);
            try
            {
                Assert.False(TryEnterSourceInputScope("pre-activation", "pre-activation", owner, null, out var scope));
                Assert.Null(scope.SourceInvocation);
            }
            finally { TestProfile(true); }
        }
        internal static void TestFinish(SelectorInputHandle original, bool accepted) =>
            Assert.True(TryFinishSourceSelectorInput(original, accepted));
    }
}

namespace STS2HumanAnnotator.SourceNative.Tests
{
    public sealed class SourceNativeInputScopeTests
    {
        private static void Passed(SourceNativeProducerTests.Fixture f)
        { var audit = SourceSessionAuditV3.Audit(f.Store.DirectoryPath); Assert.True(audit.Status == "pass", string.Join(",", audit.Errors)); }
        private static async Task Ready(SourceNativeProducerTests.Fixture f) =>
            Assert.True(await Task.Run(() => SpinWait.SpinUntil(() => f.Worker.Status.Observations >= 1, TimeSpan.FromSeconds(3))));

        [Theory]
        [InlineData("MegaCrit.Sts2.Core.Nodes.Screens.CardSelection.NChooseACardSelectionScreen.SelectHolder", "NChooseACardSelectionScreen.SelectHolder", false)]
        [InlineData("MegaCrit.Sts2.Core.Nodes.Screens.CardSelection.NChooseACardSelectionScreen.SkipButton.Released", "NChooseACardSelectionScreen.OnSkipButtonReleased", true)]
        [InlineData("MegaCrit.Sts2.Core.Nodes.Combat.NPlayerHand.SelectModeConfirmButton.Released", "NPlayerHand.OnSelectModeConfirmButtonPressed", true)]
        public async Task ExactOriginalSelectorCallbackBorrowsOnePrefix(string outerMethod, string callback, bool controlInput)
        {
            using var f = new SourceNativeProducerTests.Fixture(3); await Ready(f);
            f.Invoke(() =>
            {
                object? control = controlInput ? new object() : null;
                var original = RecorderRuntime.TestBegin(f.PhysicalOwner, outerMethod, control, controlInput ? callback : null);
                try
                {
                    var inner = RecorderRuntime.TestCallback(f.PhysicalOwner, callback, control);
                    Assert.True(inner.SourceInvocationBorrowed); Assert.Same(original.SourceInvocation, inner.SourceInvocation);
                    NativeSourceInputProvider.Accepted(inner.SourceInvocation);
                }
                finally { RecorderRuntime.TestFinish(original, true); }
            });
            await f.Close(); Passed(f);
            var input = Assert.Single(f.Rows<SourceNativeInputWitnessV2>("native-input-witnesses.jsonl"));
            Assert.Equal("1", input.InputPrefixOrdinal); Assert.Equal("delivered", input.Outcome.Delivery);
        }

        [Theory]
        [InlineData("different-owner")]
        [InlineData("different-control")]
        [InlineData("different-callback")]
        [InlineData("same-callback-after-claim")]
        public async Task IndependentNestedControlInputsKeepTheirOwnOriginalPrefix(string relation)
        {
            using var f = new SourceNativeProducerTests.Fixture(3); await Ready(f);
            f.Invoke(() =>
            {
                object control = new(); const string callback = "NChooseACardSelectionScreen.OnSkipButtonReleased";
                var original = RecorderRuntime.TestBegin(f.PhysicalOwner, "SkipButton.Released", control, callback);
                try
                {
                    if (relation == "same-callback-after-claim")
                        Assert.True(RecorderRuntime.TestCallback(f.PhysicalOwner, callback, control).SourceInvocationBorrowed);
                    var independent = RecorderRuntime.TestCallback(relation == "different-owner" ? new object() : f.PhysicalOwner,
                        relation == "different-callback" ? "DifferentTypedCallback" : callback,
                        relation == "different-control" ? new object() : control);
                    Assert.False(independent.SourceInvocationBorrowed); Assert.NotSame(original.SourceInvocation, independent.SourceInvocation);
                    NativeSourceInputProvider.Accepted(independent.SourceInvocation); NativeSourceInputProvider.Finish(independent.SourceInvocation);
                }
                finally { RecorderRuntime.TestFinish(original, true); }
            });
            await f.Close(); Passed(f);
            var inputs = f.Rows<SourceNativeInputWitnessV2>("native-input-witnesses.jsonl");
            Assert.Equal(new[] { "1", "2" }, inputs.Select(row => row.InputPrefixOrdinal).Order());
            Assert.All(inputs, row => Assert.Equal("delivered", row.Outcome.Delivery));
        }

        [Theory]
        [InlineData(true, "delivered")]
        [InlineData(false, "unknown")]
        public async Task OriginalControlNormalReleaseProofAndThrowingFinalizerKeepDistinctTerminals(bool normalReturn, string delivery)
        {
            using var f = new SourceNativeProducerTests.Fixture(3); await Ready(f);
            f.Invoke(() =>
            {
                var original = RecorderRuntime.TestBegin(f.PhysicalOwner, "ExactControl.Released", new object());
                RecorderRuntime.TestFinish(original, normalReturn);
            });
            await f.Close(); Passed(f);
            Assert.Equal(delivery, Assert.Single(f.Rows<SourceNativeInputWitnessV2>("native-input-witnesses.jsonl")).Outcome.Delivery);
        }

        [Theory]
        [InlineData(false)]
        [InlineData(true)]
        public async Task OriginalBootstrapCaptureCannotBorrowPostPrefixFactsBeforeProfileOrSinkActivation(bool prefixReentry)
        {
            using var f = new SourceNativeProducerTests.Fixture(3, initial =>
            {
                initial.OnCapture = null;
                if (prefixReentry) { RecorderRuntime.TestPreActivationPrefix(initial.PhysicalOwner); initial.Surface = "after-pre-activation-input"; }
            });
            await f.Close(); Passed(f);
            Assert.Empty(f.Rows<SourceNativeInputWitnessV2>("native-input-witnesses.jsonl"));
            var initial = Assert.Single(f.Rows<SourcePublicObservationV2>("public-observations.jsonl"), row => row.Position.PublicationIndex == "1");
            if (prefixReentry) { Assert.Null(initial.Capture); Assert.Equal(SourceSessionContractV3.OrderUnprovenReason, initial.MissingReason); }
            else { Assert.NotNull(initial.Capture); Assert.Null(initial.MissingReason); }
        }

        [Fact]
        public async Task ActiveBorrowedCallbackInsidePublicationPrepareInvalidatesItsOriginalAcquisitionWithoutAnotherOrdinal()
        {
            using var f = new SourceNativeProducerTests.Fixture(3); await Ready(f);
            f.Invoke(() =>
            {
                object control = new(); const string callback = "NChooseACardSelectionScreen.OnSkipButtonReleased";
                var original = RecorderRuntime.TestBegin(f.PhysicalOwner, "SkipButton.Released", control, callback);
                try
                {
                    f.OnCapture = () =>
                    {
                        f.OnCapture = null;
                        var borrowed = RecorderRuntime.TestCallback(f.PhysicalOwner, callback, control);
                        Assert.True(borrowed.SourceInvocationBorrowed); Assert.Same(original.SourceInvocation, borrowed.SourceInvocation);
                        f.Surface = "after-borrowed-callback"; NativeSourceInputProvider.Accepted(borrowed.SourceInvocation);
                    };
                    f.Owner.Publish("native_owner_ready", "publication-reentered-by-original-callback");
                }
                finally { RecorderRuntime.TestFinish(original, true); }
            });
            await f.Close(); Passed(f);
            var input = Assert.Single(f.Rows<SourceNativeInputWitnessV2>("native-input-witnesses.jsonl"));
            Assert.Equal("1", input.InputPrefixOrdinal); Assert.Equal("native_prefix_frozen", input.BasisOrder!.Status);
            Assert.Equal("delivered", input.Outcome.Delivery);
            var publication = Assert.Single(f.Rows<SourcePublicObservationV2>("public-observations.jsonl"), row => row.Position.PublicationIndex == "2");
            Assert.Null(publication.Capture); Assert.Equal(SourceSessionContractV3.OrderUnprovenReason, publication.MissingReason);
        }
    }
}
