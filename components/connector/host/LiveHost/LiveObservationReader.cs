using STS2Connector.NativeUi;
using STS2Connector.Authority;
using System;
using System.Collections.Generic;
using System.Linq;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Runs;
using STS2Connector.LiveHost.Contracts;

namespace STS2Connector.LiveHost;

internal enum CombatNoInputPhase
{
    None,
    Setup,
    Resolution
}

internal enum KnownRoomNoInputKind
{
    None,
    Rest,
    Shop,
    Treasure
}

internal static class LiveObservationReader
{
    private static readonly BoundedSettlingWindow MissingPersistentStateWindow =
        new(TimeSpan.FromSeconds(20));
    private static readonly BoundedSettlingWindow MenuOrRunEntryNoInputWindow =
        new(TimeSpan.FromSeconds(20));
    private static readonly BoundedSettlingWindow CombatNoInputWindow =
        new(TimeSpan.FromSeconds(20));
    private sealed record ReaderRegistration(string Kind, Func<ILiveSurfaceReader> Create);

    private static readonly ReaderRegistration[] ReaderRegistrations =
    {
        new(TutorialModalSurfaceReader.SurfaceKind, static () => new TutorialModalSurfaceReader()),
        new("deck_enchant_selection", static () => new DeckEnchantSurfaceReader()),
        new("combat_hand_card_selection", static () => new CombatHandCardSelectionSurfaceReader()),
        new("card_bundle_selection", static () => new CardBundleSelectionSurfaceReader()),
        new("card_reward_selection", static () => new CardRewardSurfaceReader()),
        new("reward_claim", static () => new RewardClaimSurfaceReader()),
        new("map_navigation", static () => new MapNavigationSurfaceReader()),
        new("combat_turn", static () => new CombatTurnSurfaceReader()),
        new("shop_inventory", static () => new ShopInventorySurfaceReader()),
        new("shop_room", static () => new ShopRoomSurfaceReader()),
        new("treasure_room", static () => new TreasureRoomSurfaceReader()),
        new("game_over", static () => new GameOverSurfaceReader()),
        new("character_select", static () => new CharacterSelectSurfaceReader()),
        new("main_menu", static () => new MainMenuSurfaceReader()),
        new("singleplayer_menu", static () => new SingleplayerMenuSurfaceReader()),
        new("event_dialogue", static () => new EventDialogueSurfaceReader()),
        new("event_option", static () => new EventOptionSurfaceReader())
    };

    internal static IReadOnlyList<string> DeclaredReaderKinds =>
        ReaderRegistrations.Select(provider => provider.Kind).ToArray();

    private static IReadOnlyList<ILiveSurfaceReader> CreateReaders(bool rewardPageProfile = false) =>
        ReaderRegistrations.Select(registration =>
            rewardPageProfile && registration.Kind == "card_reward_selection"
                ? (ILiveSurfaceReader)new CardRewardSurfaceReader(captureProfileFacts: true)
                : registration.Create()).ToArray();

    public static LiveObservation Build(NativeEntityRegistry entities)
    {
        return Build(entities, EnvironmentIdentityRuntime.ReadGame());
    }

    public static LiveObservation Build(
        NativeEntityRegistry entities,
        GameBuildIdentity game,
        Func<LiveObservation?>? specializedSurface = null,
        bool rewardPageProfile = false)
    {
        IReadOnlyList<ILiveSurfaceReader> providers = CreateReaders(rewardPageProfile);
        ActiveSurfaceSnapshot snapshot;
        try
        {
            snapshot = ActiveInputResolver.Capture();
        }
        catch (Exception ex)
        {
            return Unsupported(
                game,
                "surface_capture_failed",
                $"Active surface capture failed closed: {ex.GetType().Name}.",
                new[] { $"active_surface_capture_failed:{ex.GetType().Name}" },
                context: null,
                HostDiagnostics.Create(
                    "host.surface.capture_failed",
                    "error",
                    "surface",
                    "actions_suppressed",
                    "restart",
                    ex.GetType().Name));
        }
        if (!game.Compatibility.StateObservationAllowed)
        {
            return Unsupported(
                game,
                snapshot.SourceType,
                game.Compatibility.Detail,
                new[] { "game_build_identity_not_exact" },
                context: null,
                HostDiagnostics.Create(
                    "host.identity.observation_not_allowed",
                    "error",
                    "identity",
                    "actions_suppressed",
                    "update_host",
                    game.Compatibility.Detail));
        }

        if (ConnectorMod.IsMultiplayerRun())
        {
            return Unsupported(
                game,
                "multiplayer_run",
                "Multiplayer semantics are not implemented by the current Player Environment Host.",
                new[] { "multiplayer_player_environment_not_implemented" },
                context: null,
                HostDiagnostics.Create(
                    "host.compatibility.multiplayer_not_implemented",
                    "error",
                    "compatibility",
                    "surface_unsupported",
                    "unknown"));
        }

        ActiveSurfaceResolution resolution = ActiveInputResolver.Resolve(
            snapshot,
            providers,
            entities,
            game,
            specializedSurface);
        if (resolution.Failure != null)
        {
            string provider = resolution.FailedProvider ?? "unknown";
            return Unsupported(
                game,
                snapshot.SourceType,
                $"The {provider} provider failed closed: {resolution.Failure.GetType().Name}.",
                new[] { $"surface_provider_failed:{provider}:{resolution.Failure.GetType().Name}" },
                LiveContextReader.Build(entities),
                HostDiagnostics.Create(
                    "host.surface.reader_failed",
                    "error",
                    "runtime",
                    "surface_unsupported",
                    "restart",
                    $"{provider}:{resolution.Failure.GetType().Name}"));
        }

        if (resolution.Draft != null)
        {
            MenuOrRunEntryNoInputWindow.Observe(condition: false, DateTimeOffset.UtcNow);
            CombatNoInputWindow.Observe(condition: false, DateTimeOffset.UtcNow);
            return resolution.Draft;
        }
        if (resolution.MatchedKinds.Count > 1)
        {
            return Unsupported(
                game,
                snapshot.SourceType,
                $"Multiple semantic surface providers matched: {string.Join(", ", resolution.MatchedKinds)}.",
                new[] { "ambiguous_surface_provider_match" },
                LiveContextReader.Build(entities),
                HostDiagnostics.Create(
                    "host.surface.ambiguous_owner",
                    "error",
                    "surface",
                    "actions_suppressed",
                    "restart"));
        }

        if (TryBuildMenuOrRunEntryNoInputTransition(snapshot, game) is { } menuTransition)
            return menuTransition;

        if (TryBuildCombatNoInputTransition(snapshot, entities, game) is { } transition)
            return transition;

        if (TryBuildRunMountNoInputTransition(snapshot, entities, game) is { } runTransition)
            return runTransition;

        if (TryBuildEventRoomMountNoInputTransition(snapshot, game) is { } eventMountTransition)
            return eventMountTransition;

        if (TryBuildEventNoInputTransition(snapshot, game) is { } eventTransition)
            return eventTransition;

        if (TryBuildKnownRoomNoInputTransition(snapshot, game) is { } roomTransition)
            return roomTransition;

        return Unsupported(
            game,
            snapshot.SourceType,
            "The current player-visible input owner is not represented by the Player Environment.",
            new[] { "surface_not_implemented" },
            LiveContextReader.Build(entities),
            HostDiagnostics.Create(
                "host.surface.not_implemented",
                "warning",
                "surface",
                "surface_unsupported",
                "change_surface"),
            new InputOwnership(
                "none_fail_closed",
                null,
                "No Player Environment interaction owns the current input state."));
    }

    private static LiveObservation? TryBuildMenuOrRunEntryNoInputTransition(
        ActiveSurfaceSnapshot snapshot,
        GameBuildIdentity game)
    {
        bool condition = ClassifyMenuOrRunEntryNoInputTransition(
            RunManager.Instance.IsInProgress,
            snapshot.HasBlockingSurface,
            snapshot.SourceType);
        if (!MenuOrRunEntryNoInputWindow.Observe(condition, DateTimeOffset.UtcNow))
            return null;

        var context = new RunTransitionLiveContext(
            "run_transition",
            "setup",
            "awaiting_menu_or_run_mount");
        var surface = new NoActionSurface(
            "no_action",
            "settling",
            "No native input owner is mounted while the main menu or a standard run is loading.");
        var completeness = new StateCompleteness(
            "complete_for_bounded_menu_or_run_entry_transition",
            "none_no_input_owner",
            new[]
            {
                "RunManager.IsInProgress",
                "NGame.MainMenu",
                "NGame.MainMenu.SubmenuStack",
                "ActiveInputResolver"
            },
            Array.Empty<string>());
        string signature = StableIdentityHash.Object(new
        {
            game.Version,
            game.Commit,
            context,
            surface
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
                "The bounded menu/run-entry handoff has no current input owner; the Host observes without publishing actions."),
            Diagnostics = new[]
            {
                HostDiagnostics.Create(
                    "host.lifecycle.menu_or_run_entry_settling",
                    "info",
                    "runtime",
                    "none",
                    "settle",
                    snapshot.SourceType)
            }
        };
    }

    internal static bool ClassifyMenuOrRunEntryNoInputTransition(
        bool runInProgress,
        bool hasBlockingSurface,
        string sourceType) =>
        !runInProgress
        && !hasBlockingSurface
        && string.Equals(sourceType, "menu_or_no_run", StringComparison.Ordinal);

    private static LiveObservation? TryBuildCombatNoInputTransition(
        ActiveSurfaceSnapshot snapshot,
        NativeEntityRegistry entities,
        GameBuildIdentity game)
    {
        RunState? runState = RunManager.Instance.DebugOnlyGetState();
        CombatState? combatState = CombatManager.Instance.DebugOnlyGetState();
        NCombatRoom? room = NCombatRoom.Instance;
        NPlayerHand? hand = NPlayerHand.Instance;
        CombatNoInputPhase phase = ClassifyCombatNoInputTransition(
            RunManager.Instance.IsInProgress,
            runState?.CurrentRoom is CombatRoom,
            CombatManager.Instance.IsStarting,
            CombatManager.Instance.IsInProgress,
            combatState != null,
            snapshot.HasBlockingSurface,
            room != null && ConnectorMod.IsLiveNode(room),
            hand != null && ConnectorMod.IsLiveNode(hand));
        if (phase == CombatNoInputPhase.None)
        {
            CombatNoInputWindow.Observe(condition: false, DateTimeOffset.UtcNow);
            return null;
        }
        if (!CombatNoInputWindow.Observe(condition: true, DateTimeOffset.UtcNow))
            return null;

        bool isSetup = phase == CombatNoInputPhase.Setup;
        var context = new CombatTransitionLiveContext(
            "combat_transition",
            isSetup ? "setup" : "resolution",
            isSetup ? "awaiting_combat_start" : "awaiting_room_resolution");
        var surface = new NoActionSurface(
            "no_action",
            "settling",
            isSetup
                ? "The combat room is initializing; no player input owner exists yet."
                : "Combat has ended; the game is resolving room rewards or the next player-visible surface.");
        var completeness = new StateCompleteness(
            "complete_for_bounded_no_input_transition",
            "none_no_input_owner",
            new[]
            {
                "RunState.CurrentRoom",
                "CombatManager.IsStarting",
                "CombatManager.IsInProgress",
                "CombatManager.DebugOnlyGetState",
                "NCombatRoom",
                "ActiveInputResolver"
            },
            Array.Empty<string>());
        string signature = StableIdentityHash.Object(new
        {
            game.Version,
            game.Commit,
            context,
            surface,
            runState!.CurrentActIndex,
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
                "The exact combat transition has no player input owner; the Host will only observe and poll."),
            Diagnostics = new[]
            {
                HostDiagnostics.Create(
                    "host.lifecycle.no_input_transition",
                    "info",
                    "runtime",
                    "none",
                    "settle",
                    isSetup
                        ? "CombatRoom:combat_setup_before_input_surface"
                        : "CombatRoom:combat_ended_before_reward_surface")
            }
        };
    }

    private static LiveObservation? TryBuildRunMountNoInputTransition(
        ActiveSurfaceSnapshot snapshot,
        NativeEntityRegistry entities,
        GameBuildIdentity game)
    {
        RunState? runState = RunManager.Instance.DebugOnlyGetState();
        if (!ClassifyRunMountNoInputTransition(
                RunManager.Instance.IsInProgress,
                runState != null,
                runState?.CurrentRoom != null,
                snapshot.HasBlockingSurface,
                snapshot.SourceType))
        {
            return null;
        }

        var context = new RunTransitionLiveContext(
            "run_transition",
            "setup",
            "awaiting_run_state");
        var surface = new NoActionSurface(
            "no_action",
            "settling",
            "The standard run is starting or resuming, but its player-visible run state is still mounting.");
        var completeness = new StateCompleteness(
            "complete_for_bounded_run_mount_transition",
            "none_no_input_owner",
            new[]
            {
                "RunManager.IsInProgress",
                "RunManager.DebugOnlyGetState",
                "RunState.CurrentRoom",
                "RunState.TotalFloor",
                "ActiveInputResolver"
            },
            Array.Empty<string>());
        string signature = StableIdentityHash.Object(new
        {
            game.Version,
            game.Commit,
            context,
            surface
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
                "The native run-mount transition has no current input owner; the Host observes without publishing actions."),
            Diagnostics = new[]
            {
                HostDiagnostics.Create(
                    "host.lifecycle.run_mount_settling",
                    "info",
                    "runtime",
                    "none",
                    "settle")
            }
        };
    }

    internal static bool ClassifyRunMountNoInputTransition(
        bool runInProgress,
        bool runStatePresent,
        bool currentRoomPresent,
        bool hasBlockingSurface,
        string sourceType) =>
        runInProgress
        && (!runStatePresent || !currentRoomPresent)
        && !hasBlockingSurface
        && string.Equals(sourceType, "run_without_visible_overlay", StringComparison.Ordinal);

    private static LiveObservation? TryBuildEventRoomMountNoInputTransition(
        ActiveSurfaceSnapshot snapshot,
        GameBuildIdentity game)
    {
        RunState? runState = RunManager.Instance.DebugOnlyGetState();
        bool eventRoomNodePresent = NEventRoom.Instance is { } uiRoom
                                    && ConnectorMod.IsLiveNode(uiRoom);
        if (!ClassifyEventRoomMountNoInputTransition(
                RunManager.Instance.IsInProgress,
                runState?.CurrentRoom is EventRoom,
                snapshot.HasBlockingSurface,
                snapshot.SourceType,
                eventRoomNodePresent))
        {
            return null;
        }

        var context = new RunTransitionLiveContext(
            "run_transition",
            "setup",
            "awaiting_event_room_mount");
        var surface = new NoActionSurface(
            "no_action",
            "settling",
            "The native event room model is current, but its player-visible room node has not mounted yet.");
        var completeness = new StateCompleteness(
            "complete_for_event_room_mount_transition",
            "none_no_input_owner",
            new[]
            {
                "RunState.CurrentRoom exact EventRoom",
                "NEventRoom lifecycle",
                "ActiveInputResolver"
            },
            Array.Empty<string>());
        string signature = StableIdentityHash.Object(new
        {
            game.Version,
            game.Commit,
            context,
            surface,
            runState!.CurrentActIndex,
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
                "The exact event room model has no mounted player-input owner; the Host observes without publishing actions."),
            Diagnostics = new[]
            {
                HostDiagnostics.Create(
                    "host.lifecycle.event_room_mount_settling",
                    "info",
                    "runtime",
                    "none",
                    "settle")
            }
        };
    }

    internal static bool ClassifyEventRoomMountNoInputTransition(
        bool runInProgress,
        bool currentRoomIsEvent,
        bool hasBlockingSurface,
        string sourceType,
        bool eventRoomNodePresent) =>
        runInProgress
        && currentRoomIsEvent
        && !hasBlockingSurface
        && !eventRoomNodePresent
        && string.Equals(sourceType, "run_without_visible_overlay", StringComparison.Ordinal);

    private static LiveObservation? TryBuildEventNoInputTransition(
        ActiveSurfaceSnapshot snapshot,
        GameBuildIdentity game)
    {
        RunState? runState = RunManager.Instance.DebugOnlyGetState();
        if (runState?.CurrentRoom is not EventRoom eventRoom
            || NEventRoom.Instance is not { } uiRoom
            || !ConnectorMod.IsLiveNode(uiRoom))
        {
            return null;
        }

        EventLiveContext context = LiveContextReader.BuildEvent(eventRoom);
        if (!ClassifyEventNoInputTransition(
                RunManager.Instance.IsInProgress,
                currentRoomIsEvent: true,
                snapshot.HasBlockingSurface,
                snapshot.SourceType,
                eventRoomNodePresent: true,
                context.InDialogue))
        {
            return null;
        }

        var surface = new NoActionSurface(
            "no_action",
            "settling",
            "The current event text remains visible after its input owner completed; the game is handing off to the next surface.");
        var completeness = new StateCompleteness(
            "complete_for_bounded_event_no_input_transition",
            "none_no_input_owner",
            new[]
            {
                "EventRoom exact current room",
                "NEventRoom live node",
                "rendered event title and body",
                "ActiveInputResolver"
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
                "The completed event presentation has no current player input owner; the Host observes and polls without publishing actions."),
            Diagnostics = new[]
            {
                HostDiagnostics.Create(
                    "host.lifecycle.event_no_input_transition",
                    "info",
                    "runtime",
                    "none",
                    "settle",
                    $"{context.EventId}:visible_text_after_input_owner")
            }
        };
    }

    internal static bool ClassifyEventNoInputTransition(
        bool runInProgress,
        bool currentRoomIsEvent,
        bool hasBlockingSurface,
        string sourceType,
        bool eventRoomNodePresent,
        bool inDialogue) =>
        runInProgress
        && currentRoomIsEvent
        && eventRoomNodePresent
        && !inDialogue
        && !hasBlockingSurface
        && string.Equals(sourceType, "run_without_visible_overlay", StringComparison.Ordinal);

    private static LiveObservation? TryBuildKnownRoomNoInputTransition(
        ActiveSurfaceSnapshot snapshot,
        GameBuildIdentity game)
    {
        RunState? runState = RunManager.Instance.DebugOnlyGetState();
        string roomType = runState?.CurrentRoom?.GetType().Name ?? string.Empty;
        bool expectedRoomNodePresent = runState?.CurrentRoom switch
        {
            RestSiteRoom => NRestSiteRoom.Instance is { } rest && ConnectorMod.IsLiveNode(rest),
            MerchantRoom => NMerchantRoom.Instance is { } shop && ConnectorMod.IsLiveNode(shop),
            TreasureRoom => NRun.Instance?.TreasureRoom is { } treasure && ConnectorMod.IsLiveNode(treasure),
            _ => false
        };
        KnownRoomNoInputKind kind = ClassifyKnownRoomNoInputTransition(
            RunManager.Instance.IsInProgress,
            roomType,
            snapshot.HasBlockingSurface,
            snapshot.SourceType,
            expectedRoomNodePresent);
        if (kind == KnownRoomNoInputKind.None)
            return null;

        ILiveContext context = kind switch
        {
            KnownRoomNoInputKind.Rest => new RestLiveContext("rest"),
            KnownRoomNoInputKind.Shop => new ShopLiveContext("shop"),
            KnownRoomNoInputKind.Treasure => new TreasureLiveContext("treasure"),
            _ => throw new InvalidOperationException("Unsupported known-room transition kind.")
        };
        var surface = new NoActionSurface(
            "no_action",
            "settling",
            $"The native {kind.ToString().ToLowerInvariant()} room model is current, but its player-input owner is mounting or handing off.");
        var completeness = new StateCompleteness(
            "complete_for_bounded_known_room_no_input_transition",
            "none_no_input_owner",
            new[]
            {
                "RunState.CurrentRoom exact native type",
                "expected native room singleton lifecycle",
                "ActiveInputResolver"
            },
            Array.Empty<string>());
        string signature = StableIdentityHash.Object(new
        {
            game.Version,
            game.Commit,
            roomType,
            context,
            surface,
            runState!.CurrentActIndex,
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
                "The exact room model has no mounted player-input owner; the Host observes and polls without publishing actions."),
            Diagnostics = new[]
            {
                HostDiagnostics.Create(
                    "host.lifecycle.known_room_no_input_transition",
                    "info",
                    "runtime",
                    "none",
                    "settle",
                    $"{roomType}:model_current_before_or_after_input_owner")
            }
        };
    }

    internal static KnownRoomNoInputKind ClassifyKnownRoomNoInputTransition(
        bool runInProgress,
        string currentRoomType,
        bool hasBlockingSurface,
        string sourceType,
        bool expectedRoomNodePresent)
    {
        if (!runInProgress
            || hasBlockingSurface
            || expectedRoomNodePresent
            || !string.Equals(sourceType, "run_without_visible_overlay", StringComparison.Ordinal))
        {
            return KnownRoomNoInputKind.None;
        }

        return currentRoomType switch
        {
            nameof(RestSiteRoom) => KnownRoomNoInputKind.Rest,
            nameof(MerchantRoom) => KnownRoomNoInputKind.Shop,
            nameof(TreasureRoom) => KnownRoomNoInputKind.Treasure,
            _ => KnownRoomNoInputKind.None
        };
    }

    internal static CombatNoInputPhase ClassifyCombatNoInputTransition(
        bool runInProgress,
        bool currentRoomIsCombat,
        bool combatIsStarting,
        bool combatInProgress,
        bool combatStatePresent,
        bool hasBlockingSurface,
        bool liveCombatRoomPresent,
        bool liveCombatHandPresent)
    {
        if (!runInProgress || !currentRoomIsCombat || hasBlockingSurface)
            return CombatNoInputPhase.None;
        if (combatInProgress)
        {
            return liveCombatRoomPresent && liveCombatHandPresent
                ? CombatNoInputPhase.None
                : CombatNoInputPhase.Setup;
        }
        if (combatIsStarting || !combatStatePresent)
            return CombatNoInputPhase.Setup;
        return liveCombatRoomPresent
            ? CombatNoInputPhase.Resolution
            : CombatNoInputPhase.None;
    }

    private static LiveObservation Unsupported(
        GameBuildIdentity game,
        string sourceType,
        string reason,
        IReadOnlyList<string> warnings,
        ILiveContext? context,
        HostDiagnostic diagnostic,
        InputOwnership? inputOwnership = null)
    {
        var surface = new UnsupportedSurface("unsupported", sourceType, reason);
        context ??= new UnknownLiveContext(
            "unknown",
            sourceType,
            "No complete player-visible context projection is safe for this unsupported surface.");
        var completeness = new StateCompleteness(
            "not_implemented",
            "empty_fail_closed",
            new[] { "scene_tree_surface_identity" },
            new[] { "player_visible_semantics", "legal_actions" });
        string signature = StableIdentityHash.Object(new { game.Version, context, surface, actionKeys = Array.Empty<string>() });

        return new LiveObservation(
            signature,
            "unsupported",
            context,
            surface,
            completeness,
            game,
            warnings)
        {
            InputOwnership = inputOwnership ?? new InputOwnership(
                "none_fail_closed",
                null,
                "The Host could not identify one safe current input owner for this state."),
            Diagnostics = new[] { diagnostic }
        };
    }

    internal static LiveObservation ApplyMissingPersistentStatePolicy(
        LiveObservation observation,
        PersistentVisibleStateBuildResult shared)
    {
        bool withinTransientWindow = MissingPersistentStateWindow.Observe(
            shared.RunActive && shared.State == null,
            DateTimeOffset.UtcNow);
        if (!shared.RunActive || shared.State != null)
            return observation;
        return CanDeferMissingPersistentState(observation) || withinTransientWindow
            ? SettleForMissingPersistentState(observation, shared.Failure)
            : FailClosedForMissingPersistentState(observation, shared.Failure);
    }

    private static bool CanDeferMissingPersistentState(LiveObservation observation) =>
        string.Equals(observation.Readiness, "settling", StringComparison.Ordinal)
        && observation.Context is RunTransitionLiveContext
        {
            Kind: "run_transition",
            Phase: "setup",
            Transition: "awaiting_run_state"
        }
        && observation.Surface is NoActionSurface
        {
            Kind: "no_action",
            Reason: "settling"
        }
        && string.Equals(
            observation.InputOwnership.Status,
            "none_fail_closed",
            StringComparison.Ordinal)
        && observation.InputOwnership.SurfaceKind == null
        && string.Equals(
            observation.Completeness.InteractionDiscovery,
            "none_no_input_owner",
            StringComparison.Ordinal);

    private static LiveObservation SettleForMissingPersistentState(
        LiveObservation observation,
        HostDiagnostic? failure)
    {
        var surface = new NoActionSurface(
            "no_action",
            "settling",
            "The active run is mounting, but its player-visible persistent HUD is not stable yet.");
        var completeness = observation.Completeness with
        {
            PlayerVisibleSemantics = "bounded_run_mount_transition_with_persistent_state_pending",
            Missing = observation.Completeness.Missing
                .Append("persistent_visible_state")
                .Distinct()
                .ToArray()
        };
        var diagnostic = new HostDiagnostic(
            "host.persistent_state.deferred_during_run_mount_transition",
            "warning",
            "visibility",
            "field_omitted",
            "settle",
            Path: "persistent_state",
            VisibilityClass: "on_screen",
            RequiredForAction: false,
            SafeDetail: failure?.SafeDetail);
        return observation with
        {
            Signature = StableIdentityHash.Object(new
            {
                observation.Signature,
                boundedPersistentStateSettling = true
            }),
            Readiness = "settling",
            Surface = surface,
            Completeness = completeness,
            InputOwnership = new InputOwnership(
                "none_fail_closed",
                null,
                "Persistent decision state is incomplete; no Player Environment action owns input."),
            Diagnostics = observation.Diagnostics.Append(diagnostic).ToArray()
        };
    }

    private static LiveObservation FailClosedForMissingPersistentState(
        LiveObservation observation,
        HostDiagnostic? failure)
    {
        var surface = new UnsupportedSurface(
            "unsupported",
            "persistent_visible_state",
            "The active run HUD could not be projected completely; actions are suppressed.");
        var completeness = new StateCompleteness(
            "incomplete_active_run_persistent_state",
            "empty_fail_closed",
            observation.Completeness.Sources,
            observation.Completeness.Missing
                .Append("persistent_visible_state")
                .Distinct()
                .ToArray());
        return new LiveObservation(
            StableIdentityHash.Object(new
            {
                observation.Signature,
                persistentStateFailure = true
            }),
            "unsupported",
            observation.Context,
            surface,
            completeness,
            observation.Game,
            observation.Warnings.Append("active_run_persistent_visible_state_unavailable").ToArray())
        {
            InputOwnership = new InputOwnership(
                "none_fail_closed",
                null,
                "The Host cannot expose executable actions without the decision-relevant persistent run HUD."),
            Diagnostics = failure == null
                ? observation.Diagnostics
                : observation.Diagnostics.Append(failure).ToArray()
        };
    }
}
