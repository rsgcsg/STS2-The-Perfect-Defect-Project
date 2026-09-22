using System;
using System.Collections.Generic;
using Godot;
using HarmonyLib;
using MegaCrit.Sts2.Core.Entities.CardRewardAlternatives;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using STS2Connector.NativeUi;

namespace STS2Platform.GameMod;

/// <summary>
/// Composition-only observation of exact native button creation. Connector
/// owns the resulting presentation binding and input validation.
/// </summary>
internal static class ConnectorCardRewardPresentationPatches
{
    private static bool _initialized;

    internal static void Initialize()
    {
        if (_initialized)
            return;

        var refresh = AccessTools.Method(
            typeof(NCardRewardSelectionScreen),
            nameof(NCardRewardSelectionScreen.RefreshOptions),
            new[]
            {
                typeof(IReadOnlyList<CardCreationResult>),
                typeof(IReadOnlyList<CardRewardAlternative>)
            });
        var create = AccessTools.Method(
            typeof(NCardRewardAlternativeButton),
            nameof(NCardRewardAlternativeButton.Create),
            new[] { typeof(string), typeof(string[]) });
        var prefix = AccessTools.Method(
            typeof(ConnectorCardRewardRefreshObservation),
            nameof(ConnectorCardRewardRefreshObservation.Prefix));
        var finalizer = AccessTools.Method(
            typeof(ConnectorCardRewardRefreshObservation),
            nameof(ConnectorCardRewardRefreshObservation.Finalizer));
        var created = AccessTools.Method(
            typeof(ConnectorCardRewardButtonCreatedObservation),
            nameof(ConnectorCardRewardButtonCreatedObservation.Postfix));
        if (refresh == null || create == null || prefix == null
            || finalizer == null || created == null)
            throw new MissingMethodException(
                "An exact Connector card-reward presentation seam is unavailable.");

        var harmony = new Harmony("rsgcsg.sts2-platform.connector.card-reward-presentation");
        harmony.Patch(refresh,
            prefix: new HarmonyMethod(prefix),
            finalizer: new HarmonyMethod(finalizer));
        harmony.Patch(create, postfix: new HarmonyMethod(created));
        _initialized = true;
    }
}

internal static class ConnectorCardRewardRefreshObservation
{
    internal static void Prefix(
        NCardRewardSelectionScreen __instance,
        IReadOnlyList<CardRewardAlternative> extraOptions,
        out CardRewardAlternativePresentationBindings.RefreshScope? __state)
    {
        __state = null;
        try
        {
            __state = CardRewardAlternativePresentationBindings.BeginRefresh(
                __instance, extraOptions);
        }
        catch (Exception exception)
        {
            CardRewardAlternativePresentationBindings.Invalidate(__instance);
            GD.PrintErr($"[STS2 Platform] card-reward presentation begin failed: {exception}");
        }
    }

    internal static Exception? Finalizer(
        CardRewardAlternativePresentationBindings.RefreshScope? __state,
        Exception? __exception)
    {
        try
        {
            __state?.Finish(__exception == null);
        }
        catch (Exception exception)
        {
            CardRewardAlternativePresentationBindings.InvalidateCurrent();
            GD.PrintErr($"[STS2 Platform] card-reward presentation cleanup failed: {exception}");
        }
        return __exception;
    }
}

internal static class ConnectorCardRewardButtonCreatedObservation
{
    internal static void Postfix(NCardRewardAlternativeButton? __result)
    {
        try
        {
            CardRewardAlternativePresentationBindings.ObserveCreated(__result);
        }
        catch (Exception exception)
        {
            CardRewardAlternativePresentationBindings.InvalidateCurrent();
            GD.PrintErr($"[STS2 Platform] card-reward button observation failed: {exception}");
        }
    }
}
