import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { createSession, reduce, P1G_ORDER_REVISION, createPublicOrderingPolicy,
  publicMembershipFields, publicOrderingIssue } from "../src/p1Global/index.js";
const basis = () => JSON.parse(readFileSync(new URL("./fixtures/p1-global/synthetic-complete-combat.json", import.meta.url), "utf8"));

describe("P1 r1.6 order declaration, explicitly selected", () => {
  it("preserves the old handshake and produces the new revision only on explicit selection", () => {
    const original = basis(), before = JSON.stringify(original);
    const old = reduce(createSession("old", "managed"), original);
    const next = reduce(createSession("new", "managed", P1G_ORDER_REVISION), original);
    expect(old.observation.revision).toBe("r1.5");
    expect(next.observation.revision).toBe("r1.6");
    expect(next.complete).toBe(true);
    expect(JSON.stringify(original)).toBe(before);
    expect({ ...next.observation, revision: "r1.5", envelope: old.observation.envelope }).toEqual(old.observation);
    expect(() => createSession("bad", "managed", "future" as any)).toThrow("unsupported_p1_revision");
  });
  it("declares only schema-owned membership paths and keeps selection/display/effect order", () => {
    const o = reduce(createSession("order", "managed", P1G_ORDER_REVISION), basis()).observation;
    (o.scene as any).native_kind = "combat_hand_card_selection";
    const policy = createPublicOrderingPolicy(o);
    expect(publicMembershipFields(o.scene.native_kind)).toEqual(["selectable_card_entity_ids", "deselectable_card_entity_ids"]);
    const root = "$.observation.scene.content.surface.";
    expect(policy.unordered(root + "selectable_card_entity_ids", null)).toBe(true);
    for (const path of ["selected_card_entity_ids", "selector_request.selected_entity_ids", "cards",
      "selection_progress.selected_card_entity_ids", "extension.selectable_card_entity_ids"])
      expect(policy.unordered(root + path, null)).toBe(false);
    (o.scene.content as any).surface.selectable_card_entity_ids = ["a", "a"];
    expect(publicOrderingIssue(o)).toBe("invalid_public_membership_set");
    (o.scene as any).native_kind = "event_option";
    expect(policy.unordered(root + "extension.selectable_card_entity_ids", null)).toBe(false);
    expect(publicOrderingIssue(o)).toBeNull();
  });
});
