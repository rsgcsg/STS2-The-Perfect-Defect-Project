using System.Runtime.CompilerServices;
using System.Reflection;
using System.Security.Cryptography;
using HarmonyLib;
using MegaCrit.Sts2.Core.Entities.Actions;
using MegaCrit.Sts2.Core.Entities.Multiplayer;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.GameActions;
using MegaCrit.Sts2.Core.GameActions.Multiplayer;
using MegaCrit.Sts2.Core.Logging;
using STS2Platform.GameMod;
using Xunit;

namespace STS2Connector;

// The actual pinned native queue and native EndPlayerTurnAction are used.
// Held tasks represent asynchronous action work; no queue or legality rules are
// copied. The native End Turn action is inspected, not executed without a game.
[Collection("Native queue logger isolation")]
public sealed class NativeEndTurnQueueTests
{
    private sealed class LoggerIsolation : IDisposable
    {
        private readonly Harmony harmony = new("sts2-end-turn-queue-test-" + Guid.NewGuid().ToString("N"));
        private readonly MethodInfo original;
        internal LoggerIsolation()
        {
            Assert.Equal("ef1898322c9f5c86dc1b0758b272a9c440823b4a41ca9a0b82a3aa6b3d206387",
                Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(typeof(Harmony).Assembly.Location))).ToLowerInvariant());
            foreach (var type in new[] { typeof(Logger), typeof(GameAction), typeof(ActionQueueSet), typeof(EndPlayerTurnAction) })
            {
                Assert.Equal(ConnectorNativeLogicalInspectionDeparture.GameModuleVersionId, type.Assembly.ManifestModule.ModuleVersionId.ToString("D"));
                Assert.Equal(ConnectorNativeLogicalInspectionDeparture.GameAssemblySha256,
                    Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(type.Assembly.Location))).ToLowerInvariant());
            }
            original = typeof(Logger).GetMethod("GetIsRunningFromGodotEditor", BindingFlags.Static | BindingFlags.NonPublic)!;
            try
            {
                harmony.Patch(original, prefix: new HarmonyMethod(typeof(LoggerIsolation).GetMethod(nameof(OutsideEditor), BindingFlags.Static | BindingFlags.NonPublic)!));
                Assert.Contains(Harmony.GetPatchInfo(original)!.Prefixes, patch => patch.owner == harmony.Id);
            }
            catch { Dispose(); throw; }
        }
        private static bool OutsideEditor(ref bool __result) { __result = false; return false; }
        public void Dispose()
        {
            harmony.Unpatch(original, HarmonyPatchType.Prefix, harmony.Id);
            if (Harmony.GetPatchInfo(original) is { } remaining)
                Assert.DoesNotContain(remaining.Prefixes, patch => patch.owner == harmony.Id);
        }
    }
    private sealed class HeldInput : GameAction
    {
        internal readonly TaskCompletionSource Finish = new(TaskCreationOptions.RunContinuationsAsynchronously);
        internal readonly TaskCompletionSource Started = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public override ulong OwnerId => 0;
        public override GameActionType ActionType => GameActionType.CombatPlayPhaseOnly;
        protected override Task ExecuteAction() { Started.TrySetResult(); return Finish.Task; }
        public override INetAction ToNetAction() => throw new InvalidOperationException("No transport is requested by this queue test.");
        public override string ToString() => "held-native-queue-fixture";
    }
    private static (Player Player, ActionQueueSet Queue) Queue()
    {
        // Queue construction only consumes this scalar player's default NetId.
        // No model, saves, Godot node or run is initialized.
        var player = (Player)RuntimeHelpers.GetUninitializedObject(typeof(Player));
        var queue = new ActionQueueSet(new[] { player });
        queue.CombatStarted(); queue.UnpauseAllPlayerQueues();
        return (player, queue);
    }

    [Fact]
    public async Task NativeQueuedEndTurnRemainsAfterEarlierAcceptedBWhileAExecutes()
    {
        using var logging = new LoggerIsolation();
        var (player, queue) = Queue(); var a = new HeldInput(); var b = new HeldInput();
        queue.EnqueueWithoutSynchronizing(a); queue.EnqueueWithoutSynchronizing(b);
        Task execution = a.Execute();
        try
        {
            await a.Started.Task.WaitAsync(TimeSpan.FromSeconds(2));
            var end = new EndPlayerTurnAction(player, 7);
            queue.EnqueueWithoutSynchronizing(end);
            Assert.Equal(GameActionState.WaitingForExecution, b.State);
            Assert.Equal(GameActionState.WaitingForExecution, end.State);
            Assert.True(a.Id < b.Id && b.Id < end.Id);
            a.Finish.TrySetResult(); await execution;
            Assert.Same(b, queue.GetReadyAction());
            b.Finish.TrySetResult(); await b.Execute();
            Assert.Same(end, queue.GetReadyAction());
        }
        finally { a.Finish.TrySetResult(); await execution; }
    }
}

[CollectionDefinition("Native queue logger isolation", DisableParallelization = true)]
public sealed class NativeQueueLoggerIsolationCollection { }
