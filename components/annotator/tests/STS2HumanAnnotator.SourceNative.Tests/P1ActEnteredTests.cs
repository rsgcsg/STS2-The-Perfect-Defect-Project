using STS2HumanAnnotator.Core;
using Xunit;

namespace STS2HumanAnnotator.Mod
{
    // The production ActEntered branch runs unchanged. Only frame/store effects
    // are substituted; the exact owner table is the actual production core.
    internal static class NativeUiCompletionRootBindings
    {
        internal static readonly ExactOwnerWitnessBindingTable<object> Bindings = new();
        internal static bool TakeIfMatches(object owner, string root) => Bindings.TakeIfMatches(owner, root);
    }
    internal static partial class RecorderRuntime
    {
        private static string? _testPendingAct;
        private static bool _testObservationDurable;
        private static bool _testAppendThrows;
        private static int _testActFinished;
        private static int _testActObserved;
        private static bool CompleteP1Ui(string id, string phase, object evidence)
        {
            if (_testPendingAct != id) return false;
            if (_testAppendThrows) throw new IOException("append failed");
            _testPendingAct = null; _testActFinished++; return true;
        }
        private static bool ObserveP1Boundary(string reason, object? witness = null)
        {
            _testActObserved++; return _testObservationDurable;
        }
        internal static (int Finished, int Observed) TestActEntered(object owner, string root, bool pending,
            bool observationDurable = true, bool appendThrows = false)
        {
            _testPendingAct = pending ? root : null; _testObservationDurable = observationDurable;
            _testAppendThrows = appendThrows; _testActFinished = _testActObserved = 0;
            CompleteP1ActEntered(owner, root);
            return (_testActFinished, _testActObserved);
        }
    }
}
namespace STS2HumanAnnotator.SourceNative.Tests
{
    public sealed class P1ActEnteredTests
    {
        [Fact]
        public void AlreadyFinishedVoteCleansCarrierBeforeSecondActOnSameRunManager()
        {
            var manager = new object(); var vote1 = new object(); var vote2 = new object();
            var table = Mod.NativeUiCompletionRootBindings.Bindings;
            Assert.True(table.TryBind(vote1, "act-one"));
            Assert.True(table.TryTransfer(vote1, manager, "act-one"));
            Assert.Equal((0, 1), Mod.RecorderRuntime.TestActEntered(manager, "act-one", false));
            Assert.False(table.Contains(manager));
            Assert.True(table.TryBind(vote2, "act-two"));
            Assert.True(table.TryTransfer(vote2, manager, "act-two"));
            Assert.Equal((1, 0), Mod.RecorderRuntime.TestActEntered(manager, "act-two", true));
            Assert.False(table.Contains(manager));
        }
        [Fact]
        public void MissingDurableObservationAndForeignRootDoNotConsumeCarrier()
        {
            var owner = new object(); var table = Mod.NativeUiCompletionRootBindings.Bindings;
            Assert.True(table.TryBind(owner, "real-root"));
            Mod.RecorderRuntime.TestActEntered(owner, "real-root", false, observationDurable: false);
            Assert.True(table.Contains(owner));
            Mod.RecorderRuntime.TestActEntered(owner, "foreign-root", false);
            Assert.True(table.TryGetWitness(owner, out var root)); Assert.Equal("real-root", root);
        }
        [Fact]
        public void PendingCompletionAppendFailureKeepsExactCarrier()
        {
            var owner = new object(); var table = Mod.NativeUiCompletionRootBindings.Bindings;
            Assert.True(table.TryBind(owner, "pending-root"));
            Assert.Throws<IOException>(() => Mod.RecorderRuntime.TestActEntered(owner, "pending-root", true, appendThrows: true));
            Assert.True(table.Contains(owner));
        }
    }
}
