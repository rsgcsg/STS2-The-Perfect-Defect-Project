import { readFileSync } from "node:fs";
// Synthetic native-shaped pair. Native producer/renderer qualification is a
// separate required gate; these exercise the actual strict decoder and mapper.
export function rewardForwardPair() {
    const outer = JSON.parse(readFileSync(new URL("./p1-global/synthetic-complete-combat.json", import.meta.url), "utf8"));
    const snap = outer.snapshot;
    const template = snap.bound_actions.actions[0];
    const cards = structuredClone(snap.interaction.content.context.player.hand.slice(0, 2));
    cards.forEach((card, i) => { card.entity_id = `offered-${i}`; card.index = i; card.is_selected = false; });
    const alternatives = [
        { option_id: "SKIP", title: "Skip", after_selected: "end_selection_and_do_not_complete_reward", is_native_skip: true },
        { option_id: "REROLL", title: "Reroll", after_selected: "do_nothing", is_native_skip: false }
    ];
    const group = { entity_id: "reward", group_id: "reward", group_relation: "independent", kind: "card",
        label: "Card reward", description: null, enabled: true, card_options: cards,
        card_alternatives: alternatives, card_alternatives_complete: true, can_skip_selection: true,
        can_reroll: true, selection_completes_reward: true, native_relic_refresh_subscription: "absent" };
    const action = (id, verb, subject) => ({ ...template,
        bound_action_id: id, verb, subject_referent_id: subject, arguments: [], label: id });
    const reference = (id, role) => ({ referent_id: id, role, kind: "entity", label: id,
        state: { visible: true, observation_basis: "native_visible_fact" },
        properties_schema: `sts2.player-environment/referent/${role}-1`, properties: {} });
    Object.assign(snap.interaction, { kind: "reward_claim", stage: "ready",
        content_schema: "sts2.player-environment/surface/reward_claim-1",
        content: { context: { kind: "reward_flow", stage: "rewards" }, surface: {
                kind: "reward_claim", reward_set_id: "set-a", rewards: [group], can_proceed: true,
                can_leave_with_unclaimed_rewards: true, proceed_skips_remaining_rewards: true,
                potion_slots_full: false, discardable_potions: []
            } } });
    snap.referents = [reference("reward", "reward")];
    snap.bound_actions.actions = [action("open", "activate", "reward"), action("leave", "activate", null)];
    snap.bound_actions.total_count = snap.bound_actions.materialized_count = 2;
    snap.reads = snap.reads.filter((r) => r.kind !== "combat_piles");
    outer.reads = outer.reads.filter((r) => r.kind !== "combat_piles");
    const child = structuredClone(outer);
    child.snapshot.snapshot_id += "_opened";
    child.snapshot.sequence++;
    child.reads.forEach((r) => { r.response.expected_snapshot_id = r.response.observed_snapshot_id = child.snapshot.snapshot_id; });
    Object.assign(child.snapshot.interaction, { kind: "card_reward_selection", stage: "ready",
        content_schema: "sts2.player-environment/surface/card_reward_selection-1",
        content: { context: { kind: "reward_flow", stage: "card_reward" }, surface: {
                kind: "card_reward_selection", parent_status: "bound", parent_group_id: "reward", reward_set_id: "set-a",
                selection_completes_reward: true, native_relic_refresh_subscription: "absent", cards: structuredClone(cards),
                selectable_card_entity_ids: cards.map((c) => c.entity_id),
                alternatives: alternatives.map((a, i) => ({ ...a, entity_id: `alt-${i}`, index: i, label: a.title, enabled: true }))
            } } });
    child.snapshot.referents = [...cards.map((c) => reference(c.entity_id, "card")),
        ...alternatives.map((_, i) => reference(`alt-${i}`, "card_reward_alternative"))];
    child.snapshot.bound_actions.actions = [...cards.map((c, i) => action(`select-${i}`, "select", c.entity_id)),
        action("skip", "skip", "alt-0"), action("reroll", "activate", "alt-1")];
    child.snapshot.bound_actions.total_count = child.snapshot.bound_actions.materialized_count = child.snapshot.bound_actions.actions.length;
    return { outer, child, group, cards };
}
