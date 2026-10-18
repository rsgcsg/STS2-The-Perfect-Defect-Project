using System.Text.Json.Nodes;
using STS2HumanAnnotator.Core;
using Xunit;

namespace STS2HumanAnnotator.Mod
{
    // Production diagnostic partial compiles against the actual referenced native API.
    // Only the unrelated Human context and session fields are supplied by this test host.
    internal static class HumanActionScope
    {
        internal sealed class Context
        {
            internal bool NativeInputBinding { get; init; }
            internal JsonObject? P1CaptureDiagnostics { get; set; }
        }
        internal static Context? Current { get; set; }
    }
    internal static partial class RecorderRuntime
    {
        private static long _cardStageGeneration;
        private static bool IsP1Recording => _activeCaptureProfileId == P1NativeRecordingContract.ProfileId;
        internal static JsonObject BindTestP1Diagnostics(JsonObject original)
        {
            _activeCaptureProfileId = P1NativeRecordingContract.ProfileId;
            _cardStageGeneration = 12;
            HumanActionScope.Current = new HumanActionScope.Context { NativeInputBinding = true };
            try
            {
                BindP1CardCaptureDiagnostics(original, "staged", "ineligible_at_card_start");
                return HumanActionScope.Current.P1CaptureDiagnostics!;
            }
            finally { HumanActionScope.Current = null; _activeCaptureProfileId = SourceSessionContractV3.ProfileId; }
        }
    }
}

namespace STS2HumanAnnotator.SourceNative.Tests
{
    public sealed class P1CaptureDiagnosticsTests
    {
        [Fact]
        public void ScopeCarriesFrozenPrefixFactsAndSeparateSelectionGenerationWithoutLateNativeRead()
        {
            var original = new JsonObject { ["capture_snapshot_id"] = "original-H", ["capture_stage_generation"] = 10,
                ["native_action_executing"] = true, ["exact_holder_witness_id"] = "holder-1" };
            var bound = Mod.RecorderRuntime.BindTestP1Diagnostics(original);
            original["native_action_executing"] = false; original["capture_snapshot_id"] = "later-state";
            Assert.Equal("original-H", bound["capture_snapshot_id"]!.GetValue<string>());
            Assert.True(bound["native_action_executing"]!.GetValue<bool>());
            Assert.Equal(10, bound["capture_stage_generation"]!.GetValue<int>());
            Assert.Equal(12, bound["scope_stage_generation"]!.GetValue<long>());
            Assert.Equal("staged", bound["capture_source"]!.GetValue<string>());
            Assert.True(bound["native_input_binding"]!.GetValue<bool>());
            Assert.Equal("holder-1", bound["exact_holder_witness_id"]!.GetValue<string>());
        }
    }
}
