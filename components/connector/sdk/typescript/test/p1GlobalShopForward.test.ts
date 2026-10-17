import { describe, expect, it } from "vitest";
import { createSession, reduce, resolveFollowBasis, type P1Basis } from "../src/p1Global/index.js";
import { selectorForwardPair } from "./fixtures/selector-forward.mjs";

function shopPair() {
  const { outer } = selectorForwardPair();
  const snapshot = outer.snapshot;
  const card = structuredClone(snapshot.interaction.content.surface.cards[0]);
  snapshot.interaction.kind = "shop_inventory";
  snapshot.interaction.stage = "ready";
  snapshot.interaction.content_schema = "sts2.player-environment/surface/shop_inventory-1";
  snapshot.interaction.content.context = { kind: "shop" };
  snapshot.interaction.content.surface = { kind: "shop_inventory", shop_session_entity_id: "merchant-session",
    cards: [], relics: [], potions: [], card_removal: null, can_close: true };
  const action = { ...snapshot.bound_actions.actions[0], bound_action_id: "close-inventory",
    verb: "close", subject_referent_id: null, arguments: [], label: "Close inventory" };
  snapshot.bound_actions.actions = [action];
  snapshot.bound_actions.total_count = snapshot.bound_actions.materialized_count = 1;
  const read = { ...outer.reads[0], read_id: "shop-catalog", kind: "shop_catalog",
    response: { ...outer.reads[0].response, read_id: "shop-catalog", kind: "shop_catalog",
      content_schema: "sts2.player-environment/read/shop_catalog-1", target_referent_id: null,
      content: { kind: "shop_catalog", access_state: "inventory_open", cards: [{
        entity_id: "card-offer", slot_entity_id: "slot-one", inventory_index: 0, price: 75, stocked: true,
        visible: true, affordable: true, can_purchase: true, blocked_reason: null, on_sale: false,
        card,
        unknown_model_extension: { value: 1 }
      }], relics: [], potions: [], card_removal: null } }, error: null };
  outer.reads.push(read);
  snapshot.reads.push({ ...snapshot.reads[0], read_id: read.read_id, kind: read.kind,
    content_schema: read.response.content_schema, target_referent_id: null });
  const room = structuredClone(outer);
  room.snapshot.interaction.kind = "shop_room";
  room.snapshot.interaction.content_schema = "sts2.player-environment/surface/shop_room-1";
  room.snapshot.interaction.content.surface = { kind: "shop_room", shop_session_entity_id: "merchant-session",
    can_open_inventory: true, can_proceed: true };
  room.snapshot.bound_actions.actions = [
    { ...action, bound_action_id: "reopen-inventory", verb: "open", label: "Open inventory" },
    { ...action, bound_action_id: "proceed-same-room", verb: "activate", label: "Leave shop" }
  ];
  room.snapshot.bound_actions.total_count = room.snapshot.bound_actions.materialized_count = 2;
  const catalog = room.reads.at(-1).response.content;
  catalog.access_state = "inventory_closed_open_to_inspect";
  catalog.cards[0].can_purchase = false;
  catalog.cards[0].blocked_reason = "ui_control_disabled";
  return { outer, room };
}
const project = (basis: P1Basis) => reduce(createSession("shop-forward", "reference"), basis);

describe("forward shop entry and complete departure", () => {
  it("keeps open as a normal forward choice, and leaves through close plus same-room proceed", () => {
    const { outer, room } = shopPair();
    const initial = project(outer); const low = initial.lowering[initial.observation.catalog[0]!.choice_id]!;
    expect(initial.complete).toBe(true);
    expect(initial.observation.catalog.map(c => c.kind)).toEqual(["shop.leave"]);
    expect(low.follow).toEqual([{ rule_id: "shop_leave", expect_native_kind: "shop_room", expect_verb: "activate", optional: false }]);
    expect(resolveFollowBasis(low.follow[0]!, low, room)?.bound_action_id).toBe("proceed-same-room");
    expect(project(room).observation.catalog.map(c => c.kind)).toEqual(["shop.leave", "shop.open"]);
    expect(JSON.stringify(initial.observation)).not.toContain("merchant-session");
  });
  it.each(["owner", "world", "context", "blocked", "duplicate", "price", "stock", "card", "unknown-extension"])("never continues through %s drift", change => {
    const { outer, room } = shopPair(); const initial = project(outer);
    const low = initial.lowering[initial.observation.catalog[0]!.choice_id]!;
    if (change === "owner") room.snapshot.interaction.content.surface.shop_session_entity_id = "different-merchant";
    if (change === "world") room.snapshot.persistent.content.player.gold++;
    if (change === "context") room.snapshot.interaction.content.context.new_fact = true;
    if (change === "blocked") room.snapshot.interaction.content.surface.can_proceed = false;
    const offer = room.reads.at(-1).response.content.cards[0];
    if (change === "price") offer.price++;
    if (change === "stock") offer.stocked = false;
    if (change === "card") offer.card.name += " different";
    if (change === "unknown-extension") offer.unknown_model_extension.value++;
    if (change === "duplicate") {
      room.snapshot.bound_actions.actions.push({ ...room.snapshot.bound_actions.actions[1], bound_action_id: "another-proceed" });
      room.snapshot.bound_actions.total_count = room.snapshot.bound_actions.materialized_count = 3;
    }
    expect(resolveFollowBasis(low.follow[0]!, low, room)).toBeNull();
  });
});
