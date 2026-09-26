using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.CompilerServices;
using System.Text.Json.Nodes;
using Godot;
using MegaCrit.Sts2.Core.Context;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.Rewards;
using MegaCrit.Sts2.Core.Nodes.Screens;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.Core.Rewards;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Runs;
using STS2Connector.LiveHost;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment.Protocol;
using STS2Platform.NativeFoundation;

namespace STS2Connector.PlayerEnvironment;

/// <summary>
/// The native linked-reward presentation is a group of child reward buttons.
/// Clicking one child selects that reward; the native group then removes and
/// skips its remaining children. This adapter only handles that exact UI.
/// Ordinary rewards and card-reward selection use the unprofiled legacy reader.
/// </summary>
internal static class NativeTextMenuRewardPages
{
    internal static TextMenuFrame? TryCapture(
        SnapshotBuildResult legacy, NativeEntityRegistry entities)
    {
        if (NOverlayStack.Instance?.Peek() is not NRewardsScreen screen
            || !ActiveInputResolver.IsVisibleActiveOverlay(screen))
            return null;

        NLinkedRewardSet[] groups = ConnectorMod.FindAll<NLinkedRewardSet>(screen)
            .Where(ConnectorMod.IsNodeVisible).ToArray();
        if (groups.Length == 0) return null;

        string screenId = entities.GetId(screen, "screen");
        string ownerKey = $"linked_rewards:{RuntimeHelpers.GetHashCode(screen)}:{screenId}";
        NativeRewardDecision decision = NativeRewardDecisionProvider.Capture(screen, entities);
        NRewardButton[] ordinary = ConnectorMod.FindAll<NRewardButton>(screen)
            .Where(button => ConnectorMod.IsNodeVisible(button)
                && !HasLinkedAncestor(button)).ToArray();
        NProceedButton? proceed = ConnectorMod.FindFirst<NProceedButton>(screen);
        Reward[] displayedTop = ordinary.Select(button => button.Reward)
            .Where(reward => reward != null).Cast<Reward>()
            .Concat(groups.Select(group => (Reward)group.LinkedRewardSet)).ToArray();
        bool exact = decision.Status == "captured"
            && proceed != null && ConnectorMod.IsNodeVisible(proceed)
            && ordinary.All(button => button.Reward != null)
            && ExactReferences(decision.Rewards, displayedTop);
        // Validate every group before publishing any enabled referent or leaf.
        exact = exact && groups.All(group =>
        {
            NRewardButton[] children = ConnectorMod.FindAll<NRewardButton>(group)
                .Where(ConnectorMod.IsNodeVisible).ToArray();
            return children.All(button => button.Reward != null)
                && ExactReferences(group.LinkedRewardSet.Rewards,
                    children.Select(button => button.Reward!).ToArray())
                && NativeSemanticActionCatalog.ContainsExactlyOnce(
                    decision.Actions, "claim", group.LinkedRewardSet);
        });

        var visible = new List<PlayerEnvironmentReferent>();
        var content = new JsonArray();
        var leaves = new List<TextMenuLeaf>();
        foreach (NRewardButton button in ordinary)
        {
            Reward? reward = button.Reward;
            if (reward == null) continue;
            string id = entities.GetId(reward, "reward");
            string label = Label(reward);
            bool enabled = exact && button.IsEnabled
                && NativeSemanticActionCatalog.ContainsExactlyOnce(
                    decision.Actions, "claim", reward);
            visible.Add(Referent(id, "reward", "ordinary_reward", label, enabled));
            content.Add(new JsonObject
            {
                ["kind"] = "ordinary_reward", ["referent_id"] = id,
                ["label"] = label, ["enabled"] = enabled
            });
            if (enabled)
            {
                NRewardButton exactButton = button;
                Reward exactReward = reward;
                leaves.Add(Leaf("claim_reward:" + id, "root", "claim_reward",
                    "Claim " + label, id,
                    () => ClaimOrdinary(screen, exactButton, exactReward, entities)));
            }
        }

        foreach (NLinkedRewardSet group in groups)
        {
            LinkedRewardSet linked = group.LinkedRewardSet;
            string groupId = entities.GetId(linked, "reward");
            NRewardButton[] children = ConnectorMod.FindAll<NRewardButton>(group)
                .Where(ConnectorMod.IsNodeVisible).ToArray();
            Reward[] visibleChildren = children.Select(button => button.Reward)
                .Where(reward => reward != null).Cast<Reward>().ToArray();
            bool groupExact = exact && children.All(button => button.Reward != null)
                && ExactReferences(linked.Rewards, visibleChildren)
                && NativeSemanticActionCatalog.ContainsExactlyOnce(
                    decision.Actions, "claim", linked);
            visible.Add(Referent(groupId, "reward_group", "linked_reward_set",
                Label(linked), false));
            var options = new JsonArray();
            foreach (NRewardButton child in children)
            {
                Reward? reward = child.Reward;
                if (reward == null) continue;
                string id = entities.GetId(reward, "reward");
                string label = Label(reward);
                bool enabled = groupExact && child.IsEnabled
                    && !reward.SuccessfullySelected
                    && !PotionCapacityBlocks(reward);
                visible.Add(Referent(id, "reward", "linked_reward_choice", label, enabled));
                options.Add(new JsonObject
                {
                    ["referent_id"] = id, ["label"] = label, ["enabled"] = enabled
                });
                if (enabled)
                {
                    NRewardButton exactButton = child;
                    Reward exactReward = reward;
                    leaves.Add(Leaf("claim_linked:" + groupId + ":" + id,
                        "root", "claim_linked_reward", "Choose " + label,
                        id, () => ClaimLinked(screen, group, exactButton,
                            linked, exactReward, entities)));
                }
            }
            content.Add(new JsonObject
            {
                ["kind"] = "linked_reward_set",
                ["referent_id"] = groupId,
                ["label"] = Label(linked),
                ["choices"] = options
            });
        }

        bool canProceed = exact && proceed is { IsEnabled: true }
            && NativeSemanticActionCatalog.ContainsExactlyOnce(
                decision.Actions, "proceed");
        if (!exact) leaves.Clear();
        if (exact && RunManager.Instance.DebugOnlyGetState() is { } run
            && LocalContext.GetMe(run) is { } player)
        {
            foreach (TextMenuLeaf opener in NativeTextMenuPotions.Openers(entities))
            {
                if (opener.SubjectReferentId is not { } potionId) continue;
                PotionModel? potion = Enumerable.Range(0, player.PotionSlots.Count)
                    .Select(player.GetPotionAtSlotIndex)
                    .FirstOrDefault(candidate => candidate != null
                        && entities.GetId(candidate, "potion") == potionId);
                if (potion == null) continue;
                visible.Add(Referent(potionId, "potion", "visible_potion_holder",
                    ConnectorMod.SafeGetText(() => potion.Title)
                        ?? "Potion", true));
                leaves.Add(opener);
            }
        }
        if (canProceed)
            leaves.Add(Leaf("proceed_rewards:" + screenId, "root",
                proceed!.IsSkip ? "skip_rewards" : "proceed_rewards",
                proceed.IsSkip ? "Skip rewards" : "Proceed",
                screenId, () => RewardClaimSurfaceReader.StartProceed(entities, screenId)));
        visible.Add(Referent(screenId, "screen", "rewards_screen",
            "Rewards", canProceed));

        PlayerEnvironmentSnapshot page = legacy.Snapshot with
        {
            Status = exact ? leaves.Count > 0 ? "interactive" : "settling" : "settling",
            Referents = visible,
            Completeness = new PlayerEnvironmentCompleteness(
                exact ? "complete" : "partial",
                exact ? "current_visible_native_reward_controls"
                    : "native_reward_controls_mounting",
                exact ? "exact_native_linked_child_buttons_and_reward_owner"
                    : "native_reward_binding_unresolved",
                exact ? Array.Empty<string>()
                    : new[] { "native_linked_reward_presentation_bijection" },
                Array.Empty<string>()),
            Interaction = legacy.Snapshot.Interaction with
            {
                Kind = "reward_claim",
                Stage = "native_linked_reward_page",
                Prompt = "Choose visible rewards",
                ContentSchema = "sts2.player-environment/surface/linked_rewards_text_menu-1",
                Content = new PlayerEnvironmentInteractionContent(
                    new JsonObject { ["kind"] = "reward_claim",
                        ["entries"] = content,
                        ["proceed_is_skip"] = proceed?.IsSkip,
                        ["proceed_enabled"] = canProceed },
                    new JsonObject { ["kind"] = "reward_claim" }),
                Capabilities = Array.Empty<PlayerEnvironmentInteractionCapability>()
            }
        };
        return new TextMenuFrame(page, ownerKey, leaves);
    }

    internal static bool ExactReferences<T>(IReadOnlyList<T> owned,
        IReadOnlyList<T> presented) where T : class =>
        owned.Count == presented.Count
        && owned.Distinct(ReferenceEqualityComparer.Instance).Count() == owned.Count
        && presented.Distinct(ReferenceEqualityComparer.Instance).Count() == presented.Count
        && owned.All(item => presented.Any(other => ReferenceEquals(item, other)));

    private static bool HasLinkedAncestor(Node node)
    {
        for (Node? parent = node.GetParent(); parent != null; parent = parent.GetParent())
            if (parent is NLinkedRewardSet) return true;
        return false;
    }

    private static bool IsCurrent(NRewardsScreen screen) =>
        ReferenceEquals(NOverlayStack.Instance?.Peek(), screen)
        && ActiveInputResolver.IsVisibleActiveOverlay(screen);

    private static bool PotionCapacityBlocks(Reward reward)
    {
        if (reward is not PotionReward
            || RunManager.Instance.DebugOnlyGetState() is not { } run
            || LocalContext.GetMe(run) is not { } player)
            return false;
        return Enumerable.Range(0, player.PotionSlots.Count)
            .All(slot => player.GetPotionAtSlotIndex(slot) != null);
    }

    private static NativeInputResult ClaimOrdinary(NRewardsScreen screen,
        NRewardButton button, Reward reward, NativeEntityRegistry entities)
    {
        if (!IsCurrent(screen)
            || !ConnectorMod.FindAll<NRewardButton>(screen).Any(current =>
                ReferenceEquals(current, button) && !HasLinkedAncestor(current))
            || !ConnectorMod.IsNodeVisible(button) || !button.IsEnabled
            || !ReferenceEquals(button.Reward, reward))
            return NativeInputResult.Rejected("reward_binding_changed",
                "The current native reward button changed.");
        return RewardClaimSurfaceReader.StartClaim(entities,
            entities.GetId(screen, "screen"), entities.GetId(reward, "reward"));
    }

    private static NativeInputResult ClaimLinked(NRewardsScreen screen,
        NLinkedRewardSet group, NRewardButton child, LinkedRewardSet linked,
        Reward reward, NativeEntityRegistry entities)
    {
        NativeRewardDecision decision = NativeRewardDecisionProvider.Capture(screen, entities);
        if (!IsCurrent(screen)
            || !ConnectorMod.FindAll<NLinkedRewardSet>(screen).Any(current =>
                ReferenceEquals(current, group))
            || !ConnectorMod.IsNodeVisible(group)
            || !ReferenceEquals(group.LinkedRewardSet, linked)
            || !NativeSemanticActionCatalog.ContainsExactlyOnce(
                decision.Actions, "claim", linked)
            || !linked.Rewards.Any(current => ReferenceEquals(current, reward))
            || !ConnectorMod.FindAll<NRewardButton>(group).Any(current =>
                ReferenceEquals(current, child))
            || !ConnectorMod.IsNodeVisible(child) || !child.IsEnabled
            || !ReferenceEquals(child.Reward, reward)
            || reward.SuccessfullySelected || PotionCapacityBlocks(reward))
            return NativeInputResult.Rejected("linked_reward_binding_changed",
                "The exact linked reward child is no longer selectable.");
        child.ForceClick();
        return NativeInputResult.Delivered("native_linked_reward_child_clicked");
    }

    private static string Label(Reward reward) =>
        ConnectorMod.SafeGetText(() => reward.Description) ?? reward.GetType().Name;

    private static PlayerEnvironmentReferent Referent(string id, string role,
        string kind, string label, bool enabled) =>
        new(id, role, kind == "rewards_screen" ? "control" : "entity", label,
            new PlayerEnvironmentReferentState(true, enabled, false, false,
                "native_visible_fact"), null, null);

    private static TextMenuLeaf Leaf(string key, string group, string verb,
        string label, string subject, Func<NativeInputResult> dispatch) =>
        new(key, group, verb, label, subject,
            Array.Empty<PlayerEnvironmentBoundActionArgument>(), dispatch);
}
