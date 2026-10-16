using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

/// <summary>
/// Projects Host observations into player-visible wire facts. Exact native
/// owner, slot and control bindings deliberately stay outside this module.
/// </summary>
internal static partial class PlayerEnvironmentService
{
    private static Dictionary<string, PlayerEnvironmentReferent> BuildFactReferents(
        PlayerEnvironmentInteractionContent content)
    {
        var referents = ProjectFactReferents(content.Surface);
        CollectReferents(content.Context, null, referents);
        return referents;
    }

    internal static Dictionary<string, PlayerEnvironmentReferent> ProjectFactReferents(
        JsonNode surfaceContent)
    {
        var referents = new Dictionary<string, PlayerEnvironmentReferent>(StringComparer.Ordinal);
        CollectReferents(surfaceContent, null, referents);
        return referents;
    }

    private static void CollectReferents(
        JsonNode? node,
        string? parentKey,
        Dictionary<string, PlayerEnvironmentReferent> referents)
    {
        if (node is JsonObject obj)
        {
            foreach ((string key, JsonNode? value) in obj)
            {
                if (value?.GetValueKind() == JsonValueKind.String
                    && IsVisibleReferentField(key))
                {
                    AddReferent(
                        referents,
                        value.GetValue<string>(),
                        ReferentRole(key, parentKey),
                        ReadFirstString(obj, "name", "label", "title", "definition_id"),
                        ReadOptionalBool(obj, "is_selected", "selected"),
                        obj.DeepClone(),
                        ReadOptionalBool(obj, "enabled", "is_enabled"));
                }
                else if (value is JsonArray ids && IsVisibleReferentArray(key))
                {
                    foreach (JsonNode? id in ids)
                    {
                        if (id?.GetValueKind() == JsonValueKind.String)
                        {
                            AddReferent(
                                referents,
                                id.GetValue<string>(),
                                ReferentRole(key, parentKey),
                                null,
                                null,
                                null);
                        }
                    }
                }
                CollectReferents(value, key, referents);
            }
        }
        else if (node is JsonArray array)
        {
            foreach (JsonNode? value in array)
                CollectReferents(value, parentKey, referents);
        }
    }

    private static void AddReferent(
        Dictionary<string, PlayerEnvironmentReferent> referents,
        string referentId,
        string role,
        string? label,
        bool? selected,
        JsonNode? properties,
        bool? enabled = null)
    {
        if (string.IsNullOrWhiteSpace(referentId))
            return;
        bool hasProperties = properties is JsonObject { Count: > 1 };
        if (referents.TryGetValue(referentId, out PlayerEnvironmentReferent? existing)
            && (!hasProperties || existing.Properties != null))
            return;
        string safeRole = SchemaToken(role);
        referents[referentId] = new PlayerEnvironmentReferent(
            referentId,
            safeRole,
            "entity",
            label ?? existing?.Label,
            new PlayerEnvironmentReferentState(
                Visible: true,
                Enabled: enabled ?? existing?.State.Enabled,
                Selected: selected ?? existing?.State.Selected,
                Focused: existing?.State.Focused,
                ObservationBasis: "native_visible_fact"),
            hasProperties ? ReferentPropertiesSchema(safeRole) : null,
            hasProperties ? properties : null);
    }

    private static bool IsVisibleReferentField(string key) =>
        (key == "entity_id" || key.EndsWith("_entity_id", StringComparison.Ordinal))
        && !key.Contains("screen", StringComparison.Ordinal)
        && !key.Contains("room", StringComparison.Ordinal)
        && !key.Contains("source", StringComparison.Ordinal)
        && !key.Contains("owner", StringComparison.Ordinal)
        && !key.Contains("hand", StringComparison.Ordinal);

    private static bool IsVisibleReferentArray(string key) =>
        key.EndsWith("_entity_ids", StringComparison.Ordinal)
        && !key.Contains("screen", StringComparison.Ordinal)
        && !key.Contains("source", StringComparison.Ordinal)
        && !key.Contains("owner", StringComparison.Ordinal);

    private static string ReferentRole(string key, string? parentKey)
    {
        string role = key == "entity_id"
            ? "entity"
            : key.EndsWith("_entity_ids", StringComparison.Ordinal)
                ? key[..^11]
                : key.EndsWith("_entity_id", StringComparison.Ordinal)
                    ? key[..^10]
                    : key;
        if (role is "selectable_card" or "deselectable_card" or "selected_card")
            return "card";
        if (role is "target" or "targetable_enemy")
            return "target";
        if (role is "entity" && !string.IsNullOrWhiteSpace(parentKey))
            role = SingularRole(parentKey);
        return PublicRole(role);
    }

    private static string SingularRole(string value) => value switch
    {
        "enemies" => "enemy",
        "characters" => "character",
        "cards" => "card",
        "options" or "choices" or "next_options" => "option",
        "nodes" => "node",
        "rewards" => "reward",
        "offers" => "offer",
        "potions" => "potion",
        "relics" => "relic",
        _ => value.EndsWith('s') ? value[..^1] : value
    };

    private static bool? ReadOptionalBool(JsonObject obj, params string[] keys)
    {
        foreach (string key in keys)
        {
            if (obj[key]?.GetValueKind() == JsonValueKind.True)
                return true;
            if (obj[key]?.GetValueKind() == JsonValueKind.False)
                return false;
        }
        return null;
    }

    private static string? ReadFirstString(JsonNode node, params string[] keys)
    {
        if (node is not JsonObject obj)
            return null;
        foreach (string key in keys)
        {
            if (obj[key]?.GetValueKind() == JsonValueKind.String)
                return obj[key]!.GetValue<string>();
        }
        return null;
    }

    internal static PlayerEnvironmentInteractionContent ProjectVisibleFacts(
        ILiveSurface surface,
        ILiveContext context)
    {
        JsonNode visibleSurface = surface switch
        {
            DeckEnchantSelectionSurface value => ToNode(new
            {
                value.Kind, value.Stage, value.Prompt,
                value.MinSelect, value.MaxSelect, value.SelectedCount,
                value.SelectedCardEntityIds, value.SelectableCardEntityIds,
                value.DeselectableCardEntityIds, value.Cancelable,
                value.Enchantment, value.Cards, value.CanPreview,
                value.CanCloseSelection, value.CanConfirm, value.CanCancelPreview
            }),
            DeckTransformSelectionSurface value => ToNode(new
            {
                value.Kind, value.Stage, value.Prompt,
                value.MinSelect, value.MaxSelect, value.SelectedCount,
                value.SelectedCardEntityIds, value.SelectableCardEntityIds,
                value.DeselectableCardEntityIds, value.Cancelable,
                value.UpgradeToggleVisible, value.ShowingUpgradePreviews,
                value.PreviewKind, value.ReplacementKnown, value.Cards,
                value.CanPreview, value.CanCancelSelection,
                value.CanCancelPreview, value.CanConfirm, value.CanToggleUpgradeView
            }),
            NativeDeckCardSelectionSurface value => ToNode(new
            {
                value.Kind, value.Stage, value.Prompt,
                value.MinSelect, value.MaxSelect, value.SelectedCount,
                value.SelectedCardEntityIds, value.SelectableCardEntityIds,
                value.DeselectableCardEntityIds, value.Cancelable,
                value.CanPreview, value.CanCancelSelection,
                value.CanCancelPreview, value.CanConfirm, value.Cards
            }),
            NativeDeckUpgradeSelectionSurface value => ToNode(new
            {
                value.Kind, value.Stage, value.Prompt,
                value.MinSelect, value.MaxSelect, value.SelectedCount,
                value.SelectedCardEntityIds, value.SelectableCardEntityIds,
                value.DeselectableCardEntityIds, value.Cancelable,
                value.ShowingUpgradePreviews, value.CanToggleUpgradeView,
                value.CanCancelSelection, value.CanCancelPreview,
                value.CanConfirm, value.Cards, value.PreviewCards
            }),
            NativeCombatPileSelectionSurface value => ToNode(new
            {
                value.Kind, value.Stage, value.Prompt, value.PileType,
                value.MinSelect, value.MaxSelect, value.SelectedCount,
                value.SelectedCardEntityIds, value.SelectableCardEntityIds,
                value.DeselectableCardEntityIds, value.Cancelable,
                value.CanCancel, value.CanConfirm, value.Cards
            }),
            NativeGeneratedCardChoiceSurface value => ToNode(new
            {
                value.Kind, value.Stage, value.Prompt,
                value.SelectableCardEntityIds, value.CanSkip, value.IsPeeking,
                value.Cards
            }),
            NativeBossRelicSelectionSurface value => ToNode(new
            {
                value.Kind, value.Relics, value.SelectableRelicEntityIds,
                value.CanSkip
            }),
            NativeSimpleCardSelectionSurface value => ToNode(new
            {
                value.Kind, value.Stage, value.Prompt,
                value.MinSelect, value.MaxSelect, value.SelectedCount,
                value.SelectedCardEntityIds, value.SelectableCardEntityIds,
                value.DeselectableCardEntityIds, value.Cancelable,
                value.RequireManualConfirmation, value.CanCancel,
                value.CanConfirm, value.Cards
            }),
            EventOptionSurface value => ToNode(new { value.Kind, value.Options }),
            PotionPopupSurface value => ToNode(new { value.Kind, value.PotionEntityId, value.DefinitionId,
                value.Name, value.Slot, value.CanUse, value.CanDiscard, value.DirectCombatUse, value.UseTargetEntityIds }),
            EventDialogueSurface value => ToNode(new
            {
                value.Kind, value.CurrentLineIndex, value.RevealedLines,
                value.AdvanceLabel, value.CanAdvance
            }),
            RestSiteSurface value => ToNode(new
            {
                value.Kind, value.Options, value.CanProceed
            }),
            ShopInventorySurface value => ToNode(new
            {
                value.Kind,
                Cards = value.Cards.Select(ProjectShopCardOffer).ToArray(),
                Relics = value.Relics.Select(ProjectShopRelicOffer).ToArray(),
                Potions = value.Potions.Select(ProjectShopPotionOffer).ToArray(),
                CardRemoval = value.CardRemoval == null
                    ? null
                    : ProjectShopCardRemovalOffer(value.CardRemoval),
                value.CanClose
            }),
            ShopRoomSurface value => ToNode(new
            {
                value.Kind, value.CanOpenInventory, value.CanProceed
            }),
            TreasureRoomSurface value => ToNode(new
            {
                value.Kind, value.Stage, value.ChestOpened,
                value.Relics, value.CanChoose, value.CanSkip, value.CanProceed
            }),
            GameOverSurface value => ToNode(new
            {
                value.Kind, value.Stage, value.ReturnDestination,
                value.CanAdvanceSummary, value.CanReturn, value.OtherControls
            }),
            CharacterSelectSurface value => ToNode(new
            {
                value.Kind, value.Stage, value.Characters, value.SelectedDetails,
                value.Ascension, value.AscensionTitle, value.AscensionDescription,
                value.CanDecreaseAscension, value.CanIncreaseAscension,
                value.CanEmbark, value.CanGoBack
            }),
            TutorialSurface value => ToNode(new
            {
                value.Kind, value.Stage, value.TutorialId,
                value.CurrentPage, value.TotalPages,
                value.Title, value.Body, value.PageLabel, value.Options
            }),
            MainMenuSurface value => ToNode(new
            {
                value.Kind, value.Stage, value.Options, value.ContinueRun
            }),
            SingleplayerMenuSurface value => ToNode(new
            {
                value.Kind, value.Stage, value.Options
            }),
            CombatTurnSurface value => ToNode(new
            {
                value.Kind, value.CanEndTurn, value.PlayableCards, value.UsablePotions, value.DiscardablePotions
            }),
            CombatHandCardSelectionSurface value => ToNode(new
            {
                value.Kind, value.Prompt, value.SelectionMode,
                value.MinSelect, value.MaxSelect, value.SelectedCount,
                value.SelectedCardEntityIds, value.RequireManualConfirmation,
                value.IsPeeking, value.SelectableCardEntityIds,
                value.DeselectableCardEntityIds, value.CanConfirm,
                value.CanClosePeek, value.Cards
            }),
            CardRewardSelectionSurface value => ToNode(new
            {
                value.Kind, value.Cards, value.Alternatives,
                value.ParentGroupId, value.ParentStatus, value.RewardSetId, value.SelectionCompletesReward,
                value.NativeRelicRefreshSubscription,
                value.SelectableCardEntityIds
            }),
            CardBundleSelectionSurface value => ToNode(new
            {
                value.Kind, value.Stage, value.Prompt, value.SelectedBundleEntityId,
                value.SelectableBundleEntityIds, value.CanConfirm,
                value.CanCancelPreview, value.Bundles
            }),
            RewardClaimSurface value => ToNode(new
            {
                value.Kind, value.Rewards, value.PotionSlotsFull,
                value.DiscardablePotions, value.CanProceed,
                value.ProceedSkipsRemainingRewards, value.CanLeaveWithUnclaimedRewards, value.RewardSetId
            }),
            MapNavigationSurface value => ToNode(new
            {
                value.Kind, value.TravelEnabled, value.Traveling,
                value.DrawingMode, value.NextOptions, value.CanExitAnnotation
            }),
            NoActionSurface value => ToNode(new
            {
                value.Kind, value.Reason, value.Message
            }),
            UnsupportedSurface value => ToNode(new
            {
                value.Kind, value.Reason
            }),
            _ => new JsonObject
            {
                ["kind"] = surface.Kind,
                ["projection_status"] = "visible_surface_shape_not_projected"
            }
        };
        JsonNode visibleContext = context switch
        {
            UnknownLiveContext value => ToNode(new { value.Kind, value.Reason }),
            EventLiveContext or CombatLiveContext or RewardFlowLiveContext
                or RestLiveContext or TreasureLiveContext or GameOverLiveContext
                or MenuLiveContext or ShopLiveContext or MapLiveContext
                or CombatTransitionLiveContext or RunTransitionLiveContext => ToNode(context),
            _ => new JsonObject { ["kind"] = context.Kind }
        };
        return new PlayerEnvironmentInteractionContent(visibleSurface, visibleContext);
    }

    private static object ProjectShopCardOffer(VisibleShopCardOffer value) => new
    {
        value.EntityId, value.InventoryIndex, value.Price, value.Stocked,
        value.Visible, value.Affordable, value.CanPurchase, value.BlockedReason,
        value.OnSale, value.Card
    };

    private static object ProjectShopRelicOffer(VisibleShopRelicOffer value) => new
    {
        value.EntityId, value.InventoryIndex, value.Price, value.Stocked,
        value.Visible, value.Affordable, value.CanPurchase, value.BlockedReason,
        value.Relic
    };

    private static object ProjectShopPotionOffer(VisibleShopPotionOffer value) => new
    {
        value.EntityId, value.InventoryIndex, value.Price, value.Stocked,
        value.Visible, value.Affordable, value.CanPurchase, value.BlockedReason,
        value.DefinitionId, value.Name, value.Description, value.Rarity
    };

    private static object ProjectShopCardRemovalOffer(VisibleShopCardRemovalOffer value) => new
    {
        value.EntityId, value.InventoryIndex, value.Price, value.NextPriceIncrease,
        value.Stocked, value.Visible, value.Affordable, value.CanPurchase,
        value.BlockedReason
    };

    private static JsonNode ToNode(object value) =>
        JsonSerializer.SerializeToNode(value, value.GetType(), ConnectorMod._jsonOptions)
        ?? new JsonObject();
}
