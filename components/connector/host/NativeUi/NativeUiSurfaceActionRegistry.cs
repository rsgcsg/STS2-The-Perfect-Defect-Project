using System;
using System.Collections.Generic;
using System.Linq;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;

namespace STS2Connector.NativeUi;

internal static partial class NativeUiActionRuntime
{
    private interface INativeUiSurfaceActionAdapter
    {
        string SurfaceKind { get; }
        Type SurfaceType { get; }
        IReadOnlyList<NativeUiBoundAction> BuildBindings(LiveObservation draft);
        NativeInputResult Start(
            LiveObservation draft,
            NativeUiInput request,
            NativeUiBoundAction binding);
    }

    private sealed class NativeUiSurfaceActionAdapter<TSurface>(
        string surfaceKind,
        Func<LiveObservation, TSurface, IReadOnlyList<NativeUiBoundAction>> build,
        Func<LiveObservation, NativeUiInput, NativeUiBoundAction, NativeInputResult> start)
        : INativeUiSurfaceActionAdapter
        where TSurface : class, ILiveSurface
    {
        public string SurfaceKind { get; } = surfaceKind;
        public Type SurfaceType => typeof(TSurface);

        public IReadOnlyList<NativeUiBoundAction> BuildBindings(LiveObservation draft) =>
            draft.Surface is TSurface surface
            && string.Equals(surface.Kind, SurfaceKind, StringComparison.Ordinal)
                ? build(draft, surface)
                : Array.Empty<NativeUiBoundAction>();

        public NativeInputResult Start(
            LiveObservation draft,
            NativeUiInput request,
            NativeUiBoundAction binding) =>
            draft.Surface is TSurface surface
            && string.Equals(surface.Kind, SurfaceKind, StringComparison.Ordinal)
                ? start(draft, request, binding)
                : NativeInputResult.Rejected(
                    "native_command_owner_unsupported",
                    "The current surface kind does not match its typed native UI adapter.");
    }

    private static readonly IReadOnlyDictionary<Type, INativeUiSurfaceActionAdapter>
        SurfaceAdapters = CreateSurfaceAdapters();

    internal static IReadOnlyList<string> DeclaredActionSurfaceKinds => SurfaceAdapters.Values
        .Select(adapter => adapter.SurfaceKind)
        .Order(StringComparer.Ordinal)
        .ToArray();

    private static INativeUiSurfaceActionAdapter? FindSurfaceAdapter(ILiveSurface surface) =>
        SurfaceAdapters.GetValueOrDefault(surface.GetType());

    private static IReadOnlyDictionary<Type, INativeUiSurfaceActionAdapter>
        CreateSurfaceAdapters()
    {
        INativeUiSurfaceActionAdapter[] adapters =
        {
            Adapter<PotionPopupSurface>("potion_popup", BuildPotionPopupBindings, StartPotionPopupCommand),
            Adapter<CombatTurnSurface>("combat_turn", BuildCombatBindings,
                StartCombatCommand),
            Adapter<ShopRoomSurface>("shop_room", BuildShopRoomBindings,
                static (draft, request, _) => StartShopRoomCommand(draft, request)),
            Adapter<MapNavigationSurface>("map_navigation", BuildMapBindings,
                static (draft, request, _) => StartMapCommand(draft, request)),
            Adapter<DeckEnchantSelectionSurface>("deck_enchant_selection", BuildDeckEnchantBindings,
                StartDeckEnchantCommand),
            Adapter<EventDialogueSurface>("event_dialogue", BuildEventDialogueBindings,
                StartEventDialogueCommand),
            Adapter<EventOptionSurface>("event_option", BuildEventOptionBindings,
                StartEventOptionCommand),
            Adapter<TreasureRoomSurface>("treasure_room", BuildTreasureRoomBindings,
                StartTreasureRoomCommand),
            Adapter<RewardClaimSurface>("reward_claim", BuildRewardClaimBindings,
                StartRewardClaimCommand),
            Adapter<CardRewardSelectionSurface>("card_reward_selection", BuildCardRewardBindings,
                StartCardRewardCommand),
            Adapter<ShopInventorySurface>("shop_inventory", BuildShopInventoryBindings,
                StartShopInventoryCommand),
            Adapter<MainMenuSurface>("main_menu", BuildMainMenuBindings,
                StartMainMenuCommand),
            Adapter<SingleplayerMenuSurface>("singleplayer_menu", BuildSingleplayerMenuBindings,
                StartSingleplayerMenuCommand),
            Adapter<CharacterSelectSurface>("character_select", BuildCharacterSelectBindings,
                StartCharacterSelectCommand),
            Adapter<TutorialSurface>("tutorial", BuildTutorialBindings,
                StartTutorialCommand),
            Adapter<GameOverSurface>("game_over", BuildGameOverBindings,
                StartGameOverCommand),
            Adapter<CombatHandCardSelectionSurface>(
                "combat_hand_card_selection", BuildCombatHandBindings, StartCombatHandCommand),
            Adapter<CardBundleSelectionSurface>("card_bundle_selection", BuildCardBundleBindings,
                StartCardBundleCommand),
            Adapter<DeckTransformSelectionSurface>(
                "deck_transform_selection", BuildDeckTransformBindings, StartDeckTransformCommand)
        };
        return adapters.ToDictionary(adapter => adapter.SurfaceType);
    }

    private static INativeUiSurfaceActionAdapter Adapter<TSurface>(
        string surfaceKind,
        Func<LiveObservation, TSurface, IReadOnlyList<NativeUiBoundAction>> build,
        Func<LiveObservation, NativeUiInput, NativeUiBoundAction, NativeInputResult> start)
        where TSurface : class, ILiveSurface =>
        new NativeUiSurfaceActionAdapter<TSurface>(surfaceKind, build, start);
}
