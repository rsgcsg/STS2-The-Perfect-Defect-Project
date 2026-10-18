using STS2Connector.NativeUi;
using System;
using System.Collections.Generic;
using System.Linq;
using Godot;
using MegaCrit.Sts2.Core.Context;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Nodes.Screens.Map;
using MegaCrit.Sts2.Core.Nodes.Screens.TreasureRoomRelic;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Runs;
using STS2Connector.LiveHost.Contracts;
using STS2Platform.NativeFoundation;

namespace STS2Connector.LiveHost;

/// <summary>
/// Exact-build single-player treasure-room adapter. The four UI stages remain
/// distinct because they expose different native controls and current owners.
/// </summary>
internal sealed class TreasureRoomSurfaceReader : ILiveSurfaceReader
{
    private const string SurfaceKind = "treasure_room";
    internal const string OpenChestDeliveryEvidence = "native_treasure_chest_clicked";
    internal const string ChooseRelicDeliveryEvidence = "native_treasure_relic_holder_clicked";
    internal const string SkipRelicDeliveryEvidence = "native_treasure_skip_button_clicked";
    internal const string ProceedDeliveryEvidence = "native_treasure_proceed_button_clicked";
    public string Kind => SurfaceKind;

    public InputOwnerLayer Layer => InputOwnerLayer.Room;

    public LiveObservation? TryBuild(
        ActiveSurfaceSnapshot snapshot,
        NativeEntityRegistry entities,
        GameBuildIdentity game)
    {
        RunState? runState = RunManager.Instance.DebugOnlyGetState();
        if (runState?.CurrentRoom is not TreasureRoom room)
            return null;

        string step = "scene_root";
        try
        {
        step = "screen_authority";
        NTreasureRoom? uiRoom = NRun.Instance?.TreasureRoom;
        if (uiRoom == null || !ConnectorMod.IsLiveNode(uiRoom))
            return null;
        bool roomLayerOwnsInput = ActiveInputResolver.IsActiveLayer(
            InputOwnerLayer.Room,
            snapshot.TopOverlay != null,
            snapshot.MapIsOpen,
            snapshot.MenuSubmenu != null || snapshot.MenuRoot != null,
            snapshot.OpenModal != null);
        if (ClassifyScreenHandoff(
                RunManager.Instance.IsInProgress,
                currentRoomIsTreasure: true,
                uiRoomIsLive: true,
                roomLayerOwnsInput))
        {
            return ScreenHandoff(game, runState);
        }

        step = "native_decision";
        NativeTreasureDecision nativeDecision =
            NativeTreasureDecisionProvider.Capture(uiRoom, entities);
        if (nativeDecision.Status != "captured")
        {
            return BindingUnavailable(
                game,
                nativeDecision.Detail
                ?? "The exact game-owned treasure decision is unavailable.");
        }

        step = "exact_controls";
        NButton? chest = uiRoom.GetNodeOrNull<NButton>("%Chest");
        NTreasureRoomRelicCollection? collection =
            uiRoom.GetNodeOrNull<NTreasureRoomRelicCollection>("%RelicCollection");
        NProceedButton proceed = uiRoom.ProceedButton;
        Player? player = LocalContext.GetMe(runState);
        if (chest == null || collection == null || proceed == null || player == null)
            return BindingUnavailable(game, "Treasure controls, relic collection, or local player are unavailable.");

        step = "relic_collection";
        RelicModel[] currentRelics = nativeDecision.Relics.ToArray();
        NTreasureRoomRelicHolder? holder = collection.SingleplayerRelicHolder;
        bool holderMatches = TreasureVisibilityFacts.CanReadSingleplayerRelic(
                                 nativeDecision.Stage is "relic_choice" or "resolving",
                                 currentRelics.Length)
                             && holder != null
                             && TryReadHolderRelic(holder, out RelicModel? holderRelic)
                             && ReferenceEquals(holderRelic, currentRelics[0]);
        bool holderVisible = holderMatches
                             && ConnectorMod.IsLiveNode(holder!)
                             && ConnectorMod.IsNodeVisible(holder!)
                             && (nativeDecision.Stage is "relic_choice" or "resolving");
        bool holderActionable = holderVisible
                                && holder!.IsEnabled
                                && holder.MouseFilter != Control.MouseFilterEnum.Ignore;

        step = "surface_projection";
        string stage = nativeDecision.Stage;
        VisibleTreasureRelic[] visibleRelics = holderVisible
            ? new[] { BuildRelic(currentRelics[0], entities) }
            : Array.Empty<VisibleTreasureRelic>();
        bool canSkip = stage == "relic_choice"
                       && proceed.IsSkip
                       && proceed.IsEnabled
                       && ConnectorMod.IsNodeVisible(proceed)
                       && NativeSemanticActionCatalog.ContainsExactlyOnce(
                           nativeDecision.Actions,
                           "skip",
                           room);
        bool canProceed = stage == "completed"
                          && !proceed.IsSkip
                          && proceed.IsEnabled
                          && ConnectorMod.IsNodeVisible(proceed)
                          && NativeSemanticActionCatalog.ContainsExactlyOnce(
                              nativeDecision.Actions,
                              "proceed",
                              room);

        string roomId = entities.GetId(room, "treasure_room");
        bool canOpenChest = stage == "closed"
                            && chest.IsEnabled
                            && ConnectorMod.IsNodeVisible(chest)
                            && chest.MouseFilter != Control.MouseFilterEnum.Ignore
                            && NativeSemanticActionCatalog.ContainsExactlyOnce(
                                nativeDecision.Actions,
                                "open",
                                room);
        bool canChoose = stage == "relic_choice"
                         && holderActionable
                         && currentRelics.Length == 1
                         && NativeSemanticActionCatalog.ContainsExactlyOnce(
                             nativeDecision.Actions,
                             "select",
                             currentRelics[0]);

        var surface = new TreasureRoomSurface(
            SurfaceKind,
            stage,
            roomId,
            nativeDecision.ChestOpened,
            visibleRelics,
            canChoose,
            canSkip,
            canProceed);
        bool hasActionableControl = canOpenChest
                                    || stage == "relic_choice" && holderActionable
                                    || canSkip
                                    || canProceed;
        // Select exists logically before the relic arrival animation enables its
        // holder. Skip alone cannot certify that the complete decision is ready.
        bool nativeSelectAwaitingPresentation = stage == "relic_choice"
            && currentRelics.Length == 1
            && NativeSemanticActionCatalog.ContainsExactlyOnce(nativeDecision.Actions, "select", currentRelics[0])
            && !canChoose;
        string readiness = IsDecisionReady(hasActionableControl, nativeSelectAwaitingPresentation)
            ? "ready" : "settling";
        var completeness = new StateCompleteness(
            "contract_complete_for_single_player_treasure_room_lifecycle",
            hasActionableControl
                ? "derived_from_same_exact_current_controls_as_execution"
                : "temporarily_empty_while_chest_or_relic_award_animation_settles",
            new[]
            {
                "NTreasureRoom.Create exact TreasureRoom+IRunState owner",
                "NativeTreasureDecisionProvider exact room lifecycle catalog",
                "TreasureRoomRelicSynchronizer.CurrentRelics+GetPlayerVote",
                "NTreasureRoomRelicCollection.SingleplayerRelicHolder presentation binding",
                "RelicModel visible title+description+rarity+hover keywords",
                "NProceedButton.IsSkip+IsEnabled"
            },
            Array.Empty<string>());
        step = "state_signature";
        string signature = StableIdentityHash.Object(new
        {
            game.Version,
            context = new TreasureLiveContext("treasure"),
            surface
        });
        return new LiveObservation(
            signature,
            readiness,
            new TreasureLiveContext("treasure"),
            surface,
            completeness,
            game,
            Array.Empty<string>());
        }
        catch (Exception ex)
        {
            return BindingUnavailable(
                game,
                $"Treasure projection failed at {step}: {ex.GetType().Name}.");
        }
    }

    internal static bool IsDecisionReady(bool hasActionableControl, bool nativeSelectAwaitingPresentation) =>
        hasActionableControl && !nativeSelectAwaitingPresentation;

    internal static NativeInputResult StartOpen(
        NativeEntityRegistry entities,
        string expectedRoomId)
    {
        RunState? runState = RunManager.Instance.DebugOnlyGetState();
        if (runState?.CurrentRoom is not TreasureRoom room
            || NRun.Instance?.TreasureRoom is not { } uiRoom
            || !string.Equals(
                entities.GetId(room, "treasure_room"),
                expectedRoomId,
                StringComparison.Ordinal)
            || uiRoom.GetNodeOrNull<NButton>("%Chest") is not { } chest)
        {
            return NativeInputResult.Rejected(
                "treasure_chest_changed",
                "The exact treasure room or chest control is no longer current.");
        }
        return StartOpen(entities, room, uiRoom, chest);
    }

    internal static NativeInputResult StartChoose(
        NativeEntityRegistry entities,
        string expectedRoomId,
        string expectedRelicId)
    {
        RunState? runState = RunManager.Instance.DebugOnlyGetState();
        RelicModel[] currentRelics =
            RunManager.Instance.TreasureRoomRelicSynchronizer.CurrentRelics?.ToArray()
            ?? Array.Empty<RelicModel>();
        if (runState?.CurrentRoom is not TreasureRoom room
            || LocalContext.GetMe(runState) == null
            || NRun.Instance?.TreasureRoom is not { } uiRoom
            || !string.Equals(
                entities.GetId(room, "treasure_room"),
                expectedRoomId,
                StringComparison.Ordinal)
            || !entities.TryResolve(expectedRelicId, out RelicModel? relic)
            || relic == null
            || currentRelics.Length != 1
            || !ReferenceEquals(currentRelics[0], relic)
            || uiRoom.GetNodeOrNull<NTreasureRoomRelicCollection>("%RelicCollection")
                is not { } collection
            || collection.SingleplayerRelicHolder is not { } holder)
        {
            return NativeInputResult.Rejected(
                "treasure_relic_changed",
                "The exact treasure room or relic entity is no longer current.");
        }
        return StartChoose(entities, room, uiRoom, collection, holder, relic);
    }

    internal static NativeInputResult StartSkip(
        NativeEntityRegistry entities,
        string expectedRoomId)
    {
        RunState? runState = RunManager.Instance.DebugOnlyGetState();
        if (runState?.CurrentRoom is not TreasureRoom room
            || LocalContext.GetMe(runState) == null
            || NRun.Instance?.TreasureRoom is not { } uiRoom
            || !string.Equals(
                entities.GetId(room, "treasure_room"),
                expectedRoomId,
                StringComparison.Ordinal)
            || uiRoom.GetNodeOrNull<NTreasureRoomRelicCollection>("%RelicCollection")
                is not { } collection)
        {
            return NativeInputResult.Rejected(
                "treasure_skip_changed",
                "The exact treasure room or skip owner is no longer current.");
        }
        return StartSkip(entities, room, uiRoom, collection, uiRoom.ProceedButton);
    }

    internal static NativeInputResult StartProceed(
        NativeEntityRegistry entities,
        string expectedRoomId)
    {
        if (RunManager.Instance.DebugOnlyGetState()?.CurrentRoom is not TreasureRoom room
            || NRun.Instance?.TreasureRoom is not { } uiRoom
            || !string.Equals(
                entities.GetId(room, "treasure_room"),
                expectedRoomId,
                StringComparison.Ordinal))
        {
            return NativeInputResult.Rejected(
                "treasure_proceed_changed",
                "The exact treasure room or proceed owner is no longer current.");
        }
        return StartProceed(entities, room, uiRoom, uiRoom.ProceedButton);
    }

    private static VisibleTreasureRelic BuildRelic(
        RelicModel relic,
        NativeEntityRegistry entities) =>
        BuildVisibleRelic(relic, entities);

    private static VisibleTreasureRelic BuildVisibleRelic(
        RelicModel relic,
        NativeEntityRegistry entities)
    {
        string entityId = entities.GetId(relic, "treasure_relic");
        VisibleEntityFacts.HoverFacts hover =
            VisibleEntityFacts.BuildHoverFacts(relic.HoverTipsExcludingRelic, entityId);
        return new VisibleTreasureRelic(
            entityId,
            relic.Id.Entry,
            ConnectorMod.SafeGetText(() => relic.Title),
            ConnectorMod.SafeGetText(() => relic.DynamicDescription),
            relic.Rarity.ToString(),
            hover.Keywords,
            hover.CardPreviews);
    }

    private static NativeInputResult StartOpen(
        NativeEntityRegistry entities,
        TreasureRoom expectedRoom,
        NTreasureRoom expectedUi,
        NButton expectedChest)
    {
        NativeTreasureDecision decision =
            NativeTreasureDecisionProvider.Capture(expectedUi, entities);
        if (!IsCurrent(expectedRoom, expectedUi)
            || !NativeSemanticActionCatalog.ContainsExactlyOnce(
                decision.Actions,
                "open",
                expectedRoom)
            || !ReferenceEquals(expectedUi.GetNodeOrNull<NButton>("%Chest"), expectedChest)
            || !expectedChest.IsEnabled
            || !ConnectorMod.IsNodeVisible(expectedChest)
            || expectedChest.MouseFilter == Control.MouseFilterEnum.Ignore)
        {
            return NativeInputResult.Rejected(
                "treasure_chest_changed",
                "The advertised unopened treasure chest is no longer current and clickable.");
        }

        expectedChest.ForceClick();
        return NativeInputResult.Delivered(OpenChestDeliveryEvidence);
    }

    private static NativeInputResult StartChoose(
        NativeEntityRegistry entities,
        TreasureRoom expectedRoom,
        NTreasureRoom expectedUi,
        NTreasureRoomRelicCollection expectedCollection,
        NTreasureRoomRelicHolder expectedHolder,
        RelicModel expectedRelic)
    {
        NativeTreasureDecision decision =
            NativeTreasureDecisionProvider.Capture(expectedUi, entities);
        if (!IsCurrent(expectedRoom, expectedUi)
            || !NativeSemanticActionCatalog.ContainsExactlyOnce(
                decision.Actions,
                "select",
                expectedRelic)
            || !ReferenceEquals(expectedUi.GetNodeOrNull<NTreasureRoomRelicCollection>("%RelicCollection"), expectedCollection)
            || !ReferenceEquals(expectedCollection.SingleplayerRelicHolder, expectedHolder)
            || decision.Relics.Count != 1
            || !ReferenceEquals(decision.Relics[0], expectedRelic)
            || !TryReadHolderRelic(expectedHolder, out RelicModel? holderRelic)
            || !ReferenceEquals(holderRelic, expectedRelic)
            || !expectedHolder.IsEnabled
            || !ConnectorMod.IsNodeVisible(expectedHolder)
            || expectedHolder.MouseFilter == Control.MouseFilterEnum.Ignore)
        {
            return NativeInputResult.Rejected(
                "treasure_relic_changed",
                "The advertised treasure relic is no longer the current selectable offer.");
        }

        expectedHolder.ForceClick();
        return NativeInputResult.Delivered(ChooseRelicDeliveryEvidence);
    }

    private static NativeInputResult StartSkip(
        NativeEntityRegistry entities,
        TreasureRoom expectedRoom,
        NTreasureRoom expectedUi,
        NTreasureRoomRelicCollection expectedCollection,
        NProceedButton expectedProceed)
    {
        NativeTreasureDecision decision =
            NativeTreasureDecisionProvider.Capture(expectedUi, entities);
        if (!IsCurrent(expectedRoom, expectedUi)
            || !NativeSemanticActionCatalog.ContainsExactlyOnce(
                decision.Actions,
                "skip",
                expectedRoom)
            || !ReferenceEquals(expectedUi.GetNodeOrNull<NTreasureRoomRelicCollection>("%RelicCollection"), expectedCollection)
            || !ReferenceEquals(expectedUi.ProceedButton, expectedProceed)
            || !expectedProceed.IsSkip
            || !expectedProceed.IsEnabled
            || !ConnectorMod.IsNodeVisible(expectedProceed))
        {
            return NativeInputResult.Rejected(
                "treasure_skip_changed",
                "The advertised treasure skip control is no longer current and enabled.");
        }

        expectedProceed.ForceClick();
        return NativeInputResult.Delivered(SkipRelicDeliveryEvidence);
    }

    private static NativeInputResult StartProceed(
        NativeEntityRegistry entities,
        TreasureRoom expectedRoom,
        NTreasureRoom expectedUi,
        NProceedButton expectedProceed)
    {
        NativeTreasureDecision decision =
            NativeTreasureDecisionProvider.Capture(expectedUi, entities);
        if (!IsCurrent(expectedRoom, expectedUi)
            || !NativeSemanticActionCatalog.ContainsExactlyOnce(
                decision.Actions,
                "proceed",
                expectedRoom)
            || !ReferenceEquals(expectedUi.ProceedButton, expectedProceed)
            || expectedProceed.IsSkip
            || !expectedProceed.IsEnabled
            || !ConnectorMod.IsNodeVisible(expectedProceed))
        {
            return NativeInputResult.Rejected(
                "treasure_proceed_changed",
                "The advertised treasure proceed control is no longer current and enabled.");
        }

        expectedProceed.ForceClick();
        return NativeInputResult.Delivered(ProceedDeliveryEvidence);
    }

    private static bool IsCurrent(TreasureRoom expectedRoom, NTreasureRoom expectedUi) =>
        ReferenceEquals(RunManager.Instance.DebugOnlyGetState()?.CurrentRoom, expectedRoom)
        && ConnectorMod.IsLiveNode(expectedUi)
        && IsRoomLayerCurrent();

    private static bool IsRoomLayerCurrent()
    {
        ActiveSurfaceSnapshot snapshot = ActiveInputResolver.Capture();
        return ActiveInputResolver.IsActiveLayer(
            InputOwnerLayer.Room,
            snapshot.TopOverlay != null,
            snapshot.MapIsOpen,
            snapshot.MenuSubmenu != null || snapshot.MenuRoot != null,
            snapshot.OpenModal != null);
    }

    private static bool TryReadHolderRelic(
        NTreasureRoomRelicHolder holder,
        out RelicModel? relic)
    {
        relic = null;
        try
        {
            relic = holder.Relic?.Model;
            return relic != null;
        }
        catch (InvalidOperationException)
        {
            // The synchronizer generates relics when the room is entered, but
            // the player-visible holder is not initialized until the chest is
            // opened. Treat that interval as non-visible, never as evidence.
            return false;
        }
    }

    private static LiveObservation BindingUnavailable(GameBuildIdentity game, string reason)
    {
        var context = new TreasureLiveContext("treasure");
        var surface = new UnsupportedSurface("unsupported", SurfaceKind, reason);
        var completeness = new StateCompleteness(
            "partial",
            "empty_fail_closed",
            new[] { "TreasureRoom+NTreasureRoom exact-version binding" },
            new[] { "treasure_stage", "visible_relics", "legal_actions" });
        string signature = StableIdentityHash.Object(new { game.Version, reason });
        return new LiveObservation(
            signature,
            "degraded",
            context,
            surface,
            completeness,
            game,
            new[] { "treasure_room_binding_unavailable" })
        {
            Diagnostics = new[]
            {
                HostDiagnostics.Create(
                    "host.surface.treasure_room.binding_unavailable",
                    "error",
                    "surface",
                    "actions_suppressed",
                    "update_host_adapter",
                    reason)
            }
        };
    }

    private static LiveObservation ScreenHandoff(GameBuildIdentity game, RunState runState)
    {
        var context = new TreasureLiveContext("treasure");
        var surface = new NoActionSurface(
            "no_action",
            "settling",
            "The treasure room node is still live while native screen ownership is handing off; no player input owner is current.");
        var completeness = new StateCompleteness(
            "complete_for_bounded_treasure_screen_handoff",
            "none_no_input_owner",
            new[]
            {
                "TreasureRoom exact current room",
                "NTreasureRoom live node",
                "ActiveInputResolver room-layer current-owner check"
            },
            Array.Empty<string>());
        string signature = StableIdentityHash.Object(new
        {
            game.Version,
            game.Commit,
            context,
            surface,
            runState.CurrentActIndex,
            runState.TotalFloor
        });
        return new LiveObservation(
            signature,
            "settling",
            context,
            surface,
            completeness,
            game,
            Array.Empty<string>())
        {
            InputOwnership = new InputOwnership(
                "none_fail_closed",
                null,
                "The treasure room exists but does not own the current native input layer; the Host publishes no actions."),
            Diagnostics = new[]
            {
                HostDiagnostics.Create(
                    "host.lifecycle.treasure_screen_handoff",
                    "info",
                    "runtime",
                    "none",
                    "settle")
            }
        };
    }

    internal static bool ClassifyScreenHandoff(
        bool runInProgress,
        bool currentRoomIsTreasure,
        bool uiRoomIsLive,
        bool ownsCurrentScreen) =>
        runInProgress
        && currentRoomIsTreasure
        && uiRoomIsLive
        && !ownsCurrentScreen;
}

internal static class TreasureVisibilityFacts
{
    public static bool CanReadSingleplayerRelic(bool collectionOpen, int currentRelicCount) =>
        collectionOpen && currentRelicCount == 1;
}
