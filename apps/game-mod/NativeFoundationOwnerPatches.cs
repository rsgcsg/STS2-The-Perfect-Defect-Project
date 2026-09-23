using Godot;
using HarmonyLib;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Commands;
using MegaCrit.Sts2.Core.Entities.CardRewardAlternatives;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Multiplayer.Game;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.Screens;
using MegaCrit.Sts2.Core.Nodes.Screens.GameOverScreen;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Rewards;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Runs;
using STS2Platform.NativeFoundation;

namespace STS2Platform.GameMod;

/// <summary>
/// Composition-only read seams. These patches record the exact native owner
/// objects already passed by STS2; they never suppress, replace, or invoke
/// gameplay behavior.
/// </summary>
internal static class NativeFoundationOwnerPatches
{
    private static bool _initialized;

    internal static void Initialize()
    {
        if (_initialized)
            return;

        var harmony = new Harmony("rsgcsg.sts2-platform.native-foundation");
        PatchPostfix(
            harmony,
            AccessTools.Method(typeof(NRewardsScreen), nameof(NRewardsScreen.ShowScreen)),
            AccessTools.Method(typeof(NativeRewardOwnerPatch), nameof(NativeRewardOwnerPatch.Postfix)));
        var cardRewardOnSelect = AccessTools.Method(typeof(CardReward), "OnSelect", Type.EmptyTypes);
        var cardRewardPrefix = AccessTools.Method(
            typeof(NativeCardRewardSelectionScopePatch),
            nameof(NativeCardRewardSelectionScopePatch.Prefix));
        var cardRewardFinalizer = AccessTools.Method(
            typeof(NativeCardRewardSelectionScopePatch),
            nameof(NativeCardRewardSelectionScopePatch.Finalizer));
        if (cardRewardOnSelect == null || cardRewardPrefix == null || cardRewardFinalizer == null)
            throw new MissingMethodException("The exact CardReward.OnSelect owner scope is unavailable.");
        harmony.Patch(
            cardRewardOnSelect,
            prefix: new HarmonyMethod(cardRewardPrefix),
            finalizer: new HarmonyMethod(cardRewardFinalizer));
        PatchPostfix(
            harmony,
            AccessTools.Method(
                typeof(NCardRewardSelectionScreen),
                nameof(NCardRewardSelectionScreen.ShowScreen)),
            AccessTools.Method(
                typeof(NativeCardRewardOwnerPatch),
                nameof(NativeCardRewardOwnerPatch.Postfix)));
        PatchPostfix(
            harmony,
            AccessTools.Method(
                typeof(NCardRewardSelectionScreen),
                nameof(NCardRewardSelectionScreen.RefreshOptions)),
            AccessTools.Method(
                typeof(NativeCardRewardRefreshPatch),
                nameof(NativeCardRewardRefreshPatch.Postfix)));
        PatchPostfix(
            harmony,
            AccessTools.Method(
                typeof(NTreasureRoom),
                nameof(NTreasureRoom.Create),
                new[] { typeof(TreasureRoom), typeof(IRunState) }),
            AccessTools.Method(
                typeof(NativeTreasureOwnerPatch),
                nameof(NativeTreasureOwnerPatch.Postfix)));
        PatchPostfix(
            harmony,
            AccessTools.Method(
                typeof(NTreasureRoom),
                "OnChestButtonReleased",
                new[] { typeof(NButton) }),
            AccessTools.Method(
                typeof(NativeTreasureChestPatch),
                nameof(NativeTreasureChestPatch.Postfix)));
        PatchPostfix(
            harmony,
            AccessTools.Method(
                typeof(TreasureRoomRelicSynchronizer),
                nameof(TreasureRoomRelicSynchronizer.OnPicked),
                new[] { typeof(Player), typeof(int?) }),
            AccessTools.Method(
                typeof(NativeTreasureRelicCommitPatch),
                nameof(NativeTreasureRelicCommitPatch.Postfix)));
        PatchPostfix(
            harmony,
            AccessTools.Method(
                typeof(NEndTurnButton),
                "OnTurnStarted",
                new[] { typeof(CombatState) }),
            AccessTools.Method(
                typeof(NativeCombatDecisionOwnerReadyPatch),
                nameof(NativeCombatDecisionOwnerReadyPatch.Postfix)));
        PatchBefore(
            harmony,
            AccessTools.Method(
                typeof(RelicSelectCmd),
                nameof(RelicSelectCmd.FromChooseARelicScreen),
                new[] { typeof(Player), typeof(IReadOnlyList<RelicModel>) }),
            AccessTools.Method(
                typeof(NativeBossRelicCommandPatch),
                nameof(NativeBossRelicCommandPatch.Before)));
        PatchPostfix(
            harmony,
            AccessTools.Method(typeof(NGameOverScreen), nameof(NGameOverScreen.Create)),
            AccessTools.Method(typeof(NativeGameOverOwnerPatch), nameof(NativeGameOverOwnerPatch.Postfix)));
        PatchPostfix(
            harmony,
            AccessTools.Method(typeof(NGameOverContinueButton), "OnEnable"),
            AccessTools.Method(typeof(NativeGameOverReadyPatch), nameof(NativeGameOverReadyPatch.Postfix)));
        _initialized = true;
    }

    private static void PatchPostfix(
        Harmony harmony,
        System.Reflection.MethodInfo? original,
        System.Reflection.MethodInfo? postfix)
    {
        if (original == null || postfix == null)
            throw new MissingMethodException("A Native Foundation owner seam is unavailable.");
        harmony.Patch(original, postfix: new HarmonyMethod(postfix));
    }

    private static void PatchBefore(
        Harmony harmony,
        System.Reflection.MethodInfo? original,
        System.Reflection.MethodInfo? before)
    {
        if (original == null || before == null)
            throw new MissingMethodException("A Native Foundation input seam is unavailable.");
        harmony.Patch(original, new HarmonyMethod(before));
    }
}

internal static class NativeCardRewardSelectionScopePatch
{
    internal static void Prefix(CardReward __instance, out IDisposable? __state)
    {
        __state = null;
        try
        {
            __state = NativeCardRewardDecisionProvider.BeginSynchronousOnSelect(__instance);
        }
        catch (Exception exception)
        {
            GD.PrintErr($"[STS2 Platform] native card reward parent scope failed: {exception}");
        }
    }

    internal static Exception? Finalizer(IDisposable? __state, Exception? __exception)
    {
        try
        {
            __state?.Dispose();
        }
        catch (Exception exception)
        {
            GD.PrintErr($"[STS2 Platform] native card reward parent scope cleanup failed: {exception}");
        }
        return __exception;
    }
}

internal static class NativeBossRelicCommandPatch
{
    internal static void Before(Player player, IReadOnlyList<RelicModel> relics)
    {
        try
        {
            NativeBossRelicDecisionProvider.RegisterFromChooseARelicScreen(player, relics);
        }
        catch (Exception exception)
        {
            GD.PrintErr($"[STS2 Platform] native boss relic command observation failed: {exception}");
        }
    }
}

internal static class NativeCombatDecisionOwnerReadyPatch
{
    internal static void Postfix(CombatState state)
    {
        try
        {
            NativeDecisionOwnerReadyProvider.ObservePlayerCombatTurnReady(state);
        }
        catch (Exception exception)
        {
            GD.PrintErr($"[STS2 Platform] native combat decision-owner observation failed: {exception}");
        }
    }
}

internal static class NativeTreasureOwnerPatch
{
    internal static void Postfix(
        TreasureRoom room,
        IRunState runState,
        NTreasureRoom? __result)
    {
        if (__result == null)
            return;
        try
        {
            NativeTreasureDecisionProvider.Register(__result, room, runState);
        }
        catch (Exception exception)
        {
            GD.PrintErr($"[STS2 Platform] native treasure owner observation failed: {exception}");
        }
    }
}

internal static class NativeTreasureChestPatch
{
    internal static void Postfix(NTreasureRoom __instance)
    {
        try
        {
            NativeTreasureDecisionProvider.ObserveChestOpening(__instance);
        }
        catch (Exception exception)
        {
            GD.PrintErr($"[STS2 Platform] native treasure lifecycle observation failed: {exception}");
        }
    }
}

internal static class NativeTreasureRelicCommitPatch
{
    internal static void Postfix(Player player)
    {
        if (NRun.Instance?.TreasureRoom is not { } screen)
            return;
        try
        {
            NativeTreasureDecisionProvider.ObserveRelicPickCommitted(screen, player);
        }
        catch (Exception exception)
        {
            GD.PrintErr($"[STS2 Platform] native treasure relic Commit observation failed: {exception}");
        }
    }
}

internal static class NativeRewardOwnerPatch
{
    internal static void Postfix(
        RewardsSet set,
        bool isTerminal,
        NRewardsScreen __result)
    {
        TryRegister(() =>
            NativeRewardDecisionProvider.Register(__result, set, isTerminal));
    }

    private static void TryRegister(Action register)
    {
        try
        {
            register();
        }
        catch (Exception exception)
        {
            GD.PrintErr($"[STS2 Platform] native reward owner observation failed: {exception}");
        }
    }
}

internal static class NativeCardRewardOwnerPatch
{
    internal static void Postfix(
        IReadOnlyList<CardCreationResult> options,
        IReadOnlyList<CardRewardAlternative> extraOptions,
        NCardRewardSelectionScreen? __result)
    {
        if (__result != null)
            TryRegister(__result, options, extraOptions, fromShowScreen: true);
    }

    internal static void TryRegister(
        NCardRewardSelectionScreen screen,
        IReadOnlyList<CardCreationResult> options,
        IReadOnlyList<CardRewardAlternative> alternatives,
        bool fromShowScreen)
    {
        try
        {
            if (fromShowScreen)
                NativeCardRewardDecisionProvider.RegisterFromShowScreen(screen, options, alternatives);
            else
                NativeCardRewardDecisionProvider.Refresh(screen, options, alternatives);
        }
        catch (Exception exception)
        {
            GD.PrintErr($"[STS2 Platform] native card reward owner observation failed: {exception}");
        }
    }
}

internal static class NativeCardRewardRefreshPatch
{
    internal static void Postfix(
        NCardRewardSelectionScreen __instance,
        IReadOnlyList<CardCreationResult> options,
        IReadOnlyList<CardRewardAlternative> extraOptions)
    {
        NativeCardRewardOwnerPatch.TryRegister(__instance, options, extraOptions, fromShowScreen: false);
    }
}

internal static class NativeGameOverOwnerPatch
{
    internal static void Postfix(RunState runState, NGameOverScreen? __result)
    {
        if (__result == null)
            return;
        try { NativeDecisionOwnerReadyProvider.RegisterGameOver(__result, runState); }
        catch (Exception exception)
        {
            GD.PrintErr($"[STS2 Platform] native game-over owner observation failed: {exception}");
        }
    }
}

internal static class NativeGameOverReadyPatch
{
    internal static void Postfix(NGameOverContinueButton __instance)
    {
        try { NativeDecisionOwnerReadyProvider.ObserveGameOverReady(__instance); }
        catch (Exception exception)
        {
            GD.PrintErr($"[STS2 Platform] native game-over ready observation failed: {exception}");
        }
    }
}
