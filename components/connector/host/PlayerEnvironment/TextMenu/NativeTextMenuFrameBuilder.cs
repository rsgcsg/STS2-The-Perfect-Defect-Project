using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.CompilerServices;
using System.Text.Json.Nodes;
using Godot;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Context;
using MegaCrit.Sts2.Core.ControllerInput;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.addons.mega_text;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

/// <summary>Read-only current native capture and exact native leaves for the
/// opt-in text menu. The session owns only the synthetic information cursor.</summary>
internal static class NativeTextMenuFrameBuilder
{
    internal static TextMenuFrame Capture(
        SnapshotBuildResult legacy,
        NativeEntityRegistry entities,
        Func<PlayerEnvironmentNativeBinding, NativeInputResult> executeLegacy)
    {
        TextMenuFrame frame = CaptureCore(legacy, entities, executeLegacy);
        return frame with
        {
            Page = NativeTextMenuInformation.AttachCurrentPassiveHoverFacts(frame.Page)
        };
    }

    private static TextMenuFrame CaptureCore(
        SnapshotBuildResult legacy,
        NativeEntityRegistry entities,
        Func<PlayerEnvironmentNativeBinding, NativeInputResult> executeLegacy)
    {
        NativeTextMenuInformationCapture information =
            NativeTextMenuInformation.Capture(legacy, entities);
        var leaves = information.Leaves.Select(item => new TextMenuLeaf(
            item.Key, item.Group, item.Verb, item.Label,
            item.SubjectReferentId, item.Arguments, item.Dispatch)).ToList();
        PlayerEnvironmentSnapshot page = information.Page;
        string owner = information.OwnerKey;

        // A native information screen opened from a linked reward still owns
        // input. Otherwise retain reward children and add only the currently
        // enabled top-bar information controls discovered from that screen.
        if (NativeTextMenuRewardPages.TryCapture(legacy, entities) is { } rewardPage)
        {
            if (page.Interaction.Stage == "native_information_page"
                || page.Interaction.Kind == "native_information_unresolved")
                return new TextMenuFrame(page, owner, leaves);
            return rewardPage with
            {
                Page = NativeTextMenuInformation.SanitizePage(rewardPage.Page),
                Leaves = rewardPage.Leaves.Concat(leaves.Where(leaf =>
                    leaf.Group != "root")).ToArray()
            };
        }

        RunState? run = RunManager.Instance.DebugOnlyGetState();
        if (run == null || !RunManager.Instance.IsInProgress)
            return new TextMenuFrame(page with { Status = "observed" },
                owner, Array.Empty<TextMenuLeaf>());

        // An entered native information page owns input over the room. Do not
        // accidentally append combat actions from the legacy underlying room.
        if (page.Interaction.Kind == "native_map")
        {
            if (legacy.HostObservation.Surface is MapNavigationSurface
                && legacy.Snapshot.BoundActions.Status == "complete")
            {
                var referents = page.Referents.ToList();
                foreach (PlayerEnvironmentBoundAction action in
                         OrderLegacyTextActions(legacy.HostObservation.Surface,
                             legacy.Snapshot.BoundActions.Actions))
                    if (legacy.Bindings.TryGetValue(action.BoundActionId,
                            out PlayerEnvironmentNativeBinding? binding))
                    {
                        leaves.Add(FromLegacy(action, binding, executeLegacy));
                        foreach (string id in action.Arguments.Select(value => value.ReferentId)
                                     .Concat(action.SubjectReferentId is { } subject
                                         ? new[] { subject } : Array.Empty<string>()))
                            if (!referents.Any(value => value.ReferentId == id)
                                && legacy.Snapshot.Referents.FirstOrDefault(value =>
                                    value.ReferentId == id && value.State.Visible)
                                    is { } native)
                                referents.Add(native);
                    }
                page = page with { Referents = referents };
            }
            return new TextMenuFrame(page, owner, leaves);
        }
        if (page.Interaction.Stage == "native_information_page"
            || page.Interaction.Kind == "native_information_unresolved")
            return new TextMenuFrame(page, owner, leaves);

        if (legacy.HostObservation.Surface is PotionPopupSurface popup)
        {
            leaves.Clear();
            leaves.AddRange(NativeTextMenuPotions.Popup(popup, entities));
            return new TextMenuFrame(page with
            {
                Status = leaves.Count > 0 ? "interactive" : "settling"
            }, owner, leaves);
        }

        if (NativeTextMenuPotions.PendingPotion is { } pendingPotion)
        {
            leaves.Clear();
            owner = $"potion_targeting:{RuntimeHelpers.GetHashCode(run)}:{entities.GetId(pendingPotion, "potion")}";
            var targets = NativeTextMenuPotions.Targets();
            string potionId = entities.GetId(pendingPotion, "potion");
            var referents = page.Referents.ToList();
            if (!referents.Any(value => value.ReferentId == potionId))
                referents.Add(new PlayerEnvironmentReferent(potionId, "potion",
                    "entity", pendingPotion.Title.GetFormattedText(),
                    new PlayerEnvironmentReferentState(true, true, false, true,
                        "native_visible_fact"), null, null));
            foreach (NCreature target in targets)
            {
                string targetId = entities.GetId(target.Entity, "creature");
                if (!referents.Any(value => value.ReferentId == targetId))
                    referents.Add(new PlayerEnvironmentReferent(targetId,
                        "creature", "entity", target.Entity.Name,
                        new PlayerEnvironmentReferentState(true, true, false, true,
                            "native_visible_fact"), null, null));
                var exactTarget = target;
                leaves.Add(Leaf("select_potion_target:" + targetId,
                    "select_potion_target", "Select " + target.Entity.Name,
                    targetId, () => NativeTextMenuPotions.SelectTarget(exactTarget)));
            }
            if (NativeTextMenuPotions.MerchantTarget() is { } merchant)
            {
                string merchantId = entities.GetId(merchant, "merchant_button");
                if (!referents.Any(value => value.ReferentId == merchantId))
                    referents.Add(new PlayerEnvironmentReferent(merchantId,
                        "merchant_target", "control", "Merchant",
                        new PlayerEnvironmentReferentState(true, true, false, false,
                            "native_visible_fact"), null, null));
                leaves.Add(Leaf("select_foul_potion_merchant:" + merchantId,
                    "select_potion_target", "Select merchant", merchantId,
                    () => NativeTextMenuPotions.SelectMerchant(merchant)));
            }
            leaves.Add(Leaf("cancel_potion_target:" + potionId,
                "cancel_potion_target", "Cancel potion targeting", potionId,
                NativeTextMenuPotions.CancelTargeting));
            page = page with
            {
                Status = "interactive",
                Referents = referents,
                Completeness = new PlayerEnvironmentCompleteness(
                    "complete", "current_native_potion_targeting",
                    "exact_native_potion_target_controls", Array.Empty<string>(),
                    Array.Empty<string>()),
                Interaction = page.Interaction with
                {
                    Kind = "potion_targeting", Stage = "native_targeting",
                    Prompt = "Choose potion target",
                    ContentSchema = "sts2.player-environment/surface/potion_targeting_text_menu-1",
                    Content = new PlayerEnvironmentInteractionContent(new JsonObject
                    {
                        ["kind"] = "potion_targeting",
                        ["potion_referent_id"] = potionId,
                        ["target_count"] = targets.Count
                            + (NativeTextMenuPotions.MerchantTarget() == null ? 0 : 1)
                    }, ValidContext(page.Interaction.Content.Context,
                        "combat_potion_targeting")),
                    Capabilities = Array.Empty<PlayerEnvironmentInteractionCapability>()
                }
            };
            return new TextMenuFrame(page, owner, leaves);
        }

        NPlayerHand? hand = NPlayerHand.Instance;
        NCombatRoom? room = NCombatRoom.Instance;
        NCardPlay? play = hand == null ? null : NativeTextMenuCombat.CurrentCardPlay(hand);
        if (hand != null && room != null && hand.InCardPlay)
        {
            // A native held-card operation owns input. The old combat reader
            // deliberately goes settling here; only this exact operation may
            // authorize a new stage. Never inherit old play(card,target).
            leaves.Clear();
            owner = $"card_play:{RuntimeHelpers.GetHashCode(run)}:{entities.GetId((object?)play ?? hand, "card_play")}";
            if (play is NControllerCardPlay controller
                && controller.Holder.CardModel is { } card
                && NativeTextMenuCombat.Owns(hand, controller, card))
            {
                string cardId = entities.GetId(card, "card");
                page = CardOperationPage(page, hand, controller, card, cardId, entities,
                    NTargetManager.Instance.IsInSelection
                        ? "card_targeting" : "card_confirm");
                leaves.Add(Leaf("cancel_card:" + cardId, "cancel_card_play",
                    "Cancel held card", cardId,
                    () => NativeTextMenuCombat.Cancel(hand, controller, card)));
                if (NTargetManager.Instance.IsInSelection)
                {
                    foreach (NCreature target in NativeTextMenuCombat.CurrentTargets(
                                 hand, controller, card))
                    {
                        var exactTarget = target;
                        string targetId = entities.GetId(target.Entity, "creature");
                        leaves.Add(Leaf("focus_target:" + targetId,
                            "focus_target", "Focus " + target.Entity.Name,
                            targetId,
                            () => NativeTextMenuCombat.FocusTarget(
                                hand, controller, card, exactTarget)));
                        leaves.Add(Leaf("confirm_target:" + targetId,
                            "confirm_target", "Confirm " + target.Entity.Name,
                            targetId,
                            () => NativeTextMenuCombat.ConfirmTarget(
                                hand, controller, card, exactTarget)));
                    }
                }
                else if (card.TargetType is not (TargetType.AnyEnemy or TargetType.AnyAlly))
                {
                    leaves.Add(Leaf("confirm_card:" + cardId,
                        "confirm_card", "Confirm held card", cardId,
                        () => NativeTextMenuCombat.ConfirmUntargeted(
                            hand, controller, card)));
                }
                return new TextMenuFrame(page, owner, leaves);
            }

            // Mouse drag or an unbound card operation is not a deterministic
            // text stage. No card-play claim is made from legacy settling data.
            return new TextMenuFrame(page with { Status = "settling" },
                owner, Array.Empty<TextMenuLeaf>());
        }

        if (legacy.HostObservation.Surface is CombatTurnSurface
            && hand != null && room != null)
        {
            // The opt-in text profile replaces the old final play/use pair.
            // Preserve only exact legacy end-turn, then enumerate current UI
            // entry controls and native information leaves.
            foreach (PlayerEnvironmentBoundAction action in
                     legacy.Snapshot.BoundActions.Actions.Where(
                         action => action.Verb == "end_turn"))
                if (legacy.Bindings.TryGetValue(action.BoundActionId,
                        out PlayerEnvironmentNativeBinding? binding))
                    leaves.Add(FromLegacy(action, binding, executeLegacy));

            foreach (var holder in hand.ActiveHolders)
            {
                CardModel? card = holder.CardModel;
                if (card == null || !NativeTextMenuCombat.CanBegin(hand, holder))
                    continue;
                var exactHolder = holder;
                var exactCard = card;
                string id = entities.GetId(card, "card");
                leaves.Add(Leaf("begin_card:" + id, "begin_card_play",
                    "Begin " + card.Title, id,
                    () => NativeTextMenuCombat.Begin(hand, exactHolder, exactCard)));
            }
            leaves.AddRange(NativeTextMenuPotions.Openers(entities));
        }
        else if (page.Status == "interactive"
                 && page.Completeness.Status == "complete"
                 && legacy.Snapshot.BoundActions.Status == "complete")
        {
            foreach (PlayerEnvironmentBoundAction action in OrderLegacyTextActions(
                         legacy.HostObservation.Surface,
                         legacy.Snapshot.BoundActions.Actions))
                if (legacy.Bindings.TryGetValue(action.BoundActionId,
                        out PlayerEnvironmentNativeBinding? binding))
                    leaves.Add(FromLegacy(action, binding, executeLegacy));
            // The persistent top-bar belt may still be an enabled native
            // control in shops/events outside combat. In particular this is
            // how Foul Potion's real merchant-targeting flow is entered.
            leaves.AddRange(NativeTextMenuPotions.Openers(entities));
        }

        if (leaves.Any(leaf => leaf.Verb == "open_potion_popup"))
        {
            var referents = page.Referents.ToList();
            foreach (TextMenuLeaf opener in leaves.Where(leaf =>
                         leaf.Verb == "open_potion_popup"))
                if (opener.SubjectReferentId is { } potionId
                    && !referents.Any(value => value.ReferentId == potionId))
                    referents.Add(new PlayerEnvironmentReferent(potionId,
                        "potion", "entity", opener.Label,
                        new PlayerEnvironmentReferentState(true, true, false,
                            false, "native_visible_fact"), null, null));
            page = page with { Referents = referents };
        }
        return new TextMenuFrame(page, owner, leaves);
    }

    internal static IReadOnlyList<PlayerEnvironmentBoundAction> OrderLegacyTextActions(
        ILiveSurface surface,
        IReadOnlyList<PlayerEnvironmentBoundAction> actions)
    {
        var rank = new Dictionary<string, int>(StringComparer.Ordinal);
        void Add(IEnumerable<string> ids)
        {
            foreach (string id in ids) rank.TryAdd(id, rank.Count);
        }
        bool selectionOnly = false;
        bool proceedLast = false;
        switch (surface)
        {
            case EventOptionSurface eventOptions:
                Add(eventOptions.Options.OrderBy(option => option.Index)
                    .Select(option => option.EntityId));
                break;
            case RestSiteSurface rest:
                Add(rest.Options.OrderBy(option => option.Index)
                    .Select(option => option.EntityId));
                break;
            case MapNavigationSurface map:
                Add(map.NextOptions.Select(option => option.EntityId));
                break;
            case ShopInventorySurface shop:
                Add(shop.Cards.OrderBy(offer => offer.InventoryIndex)
                    .Select(offer => offer.EntityId));
                Add(shop.Relics.OrderBy(offer => offer.InventoryIndex)
                    .Select(offer => offer.EntityId));
                Add(shop.Potions.OrderBy(offer => offer.InventoryIndex)
                    .Select(offer => offer.EntityId));
                if (shop.CardRemoval is { } removal) Add(new[] { removal.EntityId });
                break;
            case TreasureRoomSurface treasure:
                Add(treasure.Relics.Select(relic => relic.EntityId));
                break;
            case GameOverSurface gameOver:
                Add(gameOver.OtherControls.Select(control => control.EntityId));
                break;
            case CardRewardSelectionSurface cardReward:
                Add(cardReward.Cards.Select(card => card.EntityId));
                Add(cardReward.Alternatives.OrderBy(item => item.Index)
                    .Select(item => item.EntityId));
                break;
            case RewardClaimSurface reward:
                Add(reward.Rewards.Select(item => item.EntityId));
                Add(reward.DiscardablePotions.Select(potion => potion.EntityId));
                proceedLast = true;
                break;
            case CardBundleSelectionSurface bundles:
                Add(bundles.Bundles.Select(bundle => bundle.EntityId));
                selectionOnly = true;
                break;
            case NativeBossRelicSelectionSurface bossRelics:
                Add(bossRelics.Relics.Select(relic => relic.EntityId));
                selectionOnly = true;
                break;
            case NativeGeneratedCardChoiceSurface generated:
                Add(generated.Cards.Select(card => card.EntityId));
                selectionOnly = true;
                break;
            case NativeSimpleCardSelectionSurface simple:
                Add(simple.Cards.Select(card => card.EntityId));
                selectionOnly = true;
                break;
            case NativeCombatPileSelectionSurface pile:
                Add(pile.Cards.Select(card => card.EntityId));
                selectionOnly = true;
                break;
            case NativeDeckCardSelectionSurface deck:
                Add(deck.Cards.Select(card => card.EntityId));
                selectionOnly = true;
                break;
            case NativeDeckUpgradeSelectionSurface upgrade:
                Add(upgrade.Cards.Select(card => card.EntityId));
                selectionOnly = true;
                break;
            case DeckTransformSelectionSurface transform:
                Add(transform.Cards.Select(card => card.EntityId));
                selectionOnly = true;
                break;
            case DeckEnchantSelectionSurface enchant:
                Add(enchant.Cards.Select(card => card.EntityId));
                selectionOnly = true;
                break;
            case CombatHandCardSelectionSurface hand:
                Add(hand.Cards.Select(card => card.EntityId));
                selectionOnly = true;
                break;
            default:
                // No authoritative native ordinal is present for this scene.
                return actions;
        }
        return actions.OrderBy(action => (!selectionOnly
                || action.Verb is "select" or "deselect")
                && action.SubjectReferentId is { } id
                && rank.TryGetValue(id, out int index) ? index
                : proceedLast && action.Verb == "activate"
                    && action.SubjectReferentId == null ? rank.Count
                    : int.MaxValue)
            .ToArray();
    }

    private static TextMenuLeaf FromLegacy(
        PlayerEnvironmentBoundAction action,
        PlayerEnvironmentNativeBinding binding,
        Func<PlayerEnvironmentNativeBinding, NativeInputResult> executeLegacy) =>
        new(action.BoundActionId, "root", action.Verb, action.Label,
            action.SubjectReferentId, action.Arguments,
            () => executeLegacy(binding));

    private static TextMenuLeaf Leaf(
        string key, string verb, string label, string? subject,
        Func<NativeInputResult> dispatch) =>
        new(key, "root", verb, label, subject,
            Array.Empty<PlayerEnvironmentBoundActionArgument>(), dispatch);

    private static PlayerEnvironmentSnapshot CardOperationPage(
        PlayerEnvironmentSnapshot source, NPlayerHand hand, NControllerCardPlay play,
        CardModel card, string cardId, NativeEntityRegistry entities, string stage)
    {
        var visible = source.Referents.ToList();
        if (!visible.Any(value => value.ReferentId == cardId))
            visible.Add(new PlayerEnvironmentReferent(cardId, "card", "entity",
                card.Title, new PlayerEnvironmentReferentState(
                    true, true, false, true, "native_visible_fact"),
                null, null));

        foreach (NCreature target in NativeTextMenuCombat.CurrentTargets(
                     hand, play, card))
        {
            string targetId = entities.GetId(target.Entity, "creature");
            if (!visible.Any(value => value.ReferentId == targetId))
                visible.Add(new PlayerEnvironmentReferent(targetId,
                    "creature", "entity", target.Entity.Name,
                    new PlayerEnvironmentReferentState(true, true, false, true,
                        "native_visible_fact"), null, null));
        }

        var cardNode = play.Holder.CardNode;
        string? title = cardNode?.GetNodeOrNull<MegaLabel>("%TitleLabel")?.Text;
        string? cost = cardNode?.GetNodeOrNull<MegaLabel>("%EnergyLabel")?.Text;
        string? description = cardNode?.GetNodeOrNull<MegaRichTextLabel>("%DescriptionLabel")?.Text;
        bool displayComplete = title != null && cost != null && description != null;
        var surface = new JsonObject
        {
            ["kind"] = "combat_card_operation",
            ["stage"] = stage,
            ["held_card_referent_id"] = cardId,
            ["displayed_title"] = title,
            ["displayed_cost"] = cost,
            ["displayed_description"] = description
        };
        return source with
        {
            Status = displayComplete ? "interactive" : "settling",
            Referents = visible,
            Completeness = new PlayerEnvironmentCompleteness(
                displayComplete ? "complete" : "partial",
                "native_held_card_display", "exact_native_card_operation",
                displayComplete ? Array.Empty<string>()
                    : new[] { "current_native_card_display" },
                Array.Empty<string>()),
            Interaction = source.Interaction with
            {
                Kind = "combat_card_operation",
                Stage = stage,
                Prompt = "Held card native operation",
                ContentSchema = "sts2.player-environment/surface/combat_card_operation_text_menu-1",
                Content = new PlayerEnvironmentInteractionContent(surface,
                    ValidContext(source.Interaction.Content.Context,
                        "combat_card_operation")),
                Capabilities = Array.Empty<PlayerEnvironmentInteractionCapability>()
            }
        };
    }

    private static JsonNode ValidContext(JsonNode source, string fallbackKind)
    {
        if (source is JsonObject obj
            && obj["kind"] is JsonValue kind
            && kind.TryGetValue<string>(out string? value)
            && !string.IsNullOrWhiteSpace(value))
            return source;
        return new JsonObject { ["kind"] = fallbackKind };
    }
}
