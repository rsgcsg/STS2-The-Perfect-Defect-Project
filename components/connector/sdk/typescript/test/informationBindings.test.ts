import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { decodeTextMenuV2Snapshot } from "../src/index.js";

function currentTips() {
  const snapshot = JSON.parse(readFileSync(new URL("./fixtures/text-menu-v2-targeted-root.json", import.meta.url), "utf8"));
  snapshot.menu.cursor = "card_tips";
  snapshot.menu.selection = [];
  const card = snapshot.referents.find((value: any) => ["card", "playable_card"].includes(value.role));
  card.referent_id = "card-a";
  card.label = "Strike";
  card.properties_schema = "sts2.player-environment/referent/card-1";
  card.properties = { definition_id: "STRIKE", cost: "1", is_upgraded: false };
  snapshot.referents.push({ ...structuredClone(card), referent_id: "card-b",
    properties: { definition_id: "STRIKE", cost: "0", is_upgraded: true } });
  snapshot.referents.push({ referent_id: "topbar-hp", role: "topbar_hp", kind: "control", label: "HP",
    state: { visible: true, enabled: true, selected: false, focused: false, observation_basis: "native_visible_fact" },
    properties_schema: "sts2.player-environment/referent/topbar_hp-1", properties: { control_role: "hp", hp: 60, max_hp: 75 } });
  snapshot.menu_actions.actions = [
    { action_id: "tip-a", kind: "native_input", verb: "show_card_tips", label: "Show Strike tips",
      subject_referent_id: "card-a", arguments: [], effect_domain: "native_input" },
    { action_id: "tip-b", kind: "native_input", verb: "show_card_tips", label: "Show Strike tips",
      subject_referent_id: "card-b", arguments: [], effect_domain: "native_input" },
    { action_id: "back", kind: "system_navigation", verb: "back", label: "Back",
      subject_referent_id: null, arguments: [], effect_domain: "text_menu" }
  ];
  snapshot.menu_actions.materialized_count = snapshot.menu_actions.total_count = 3;
  return snapshot;
}

describe("information subjects in the existing public text-v2 contract", () => {
  it("retains duplicate-name instance facts as scoreable subjects without ID or ordinal labels", () => {
    const snapshot = decodeTextMenuV2Snapshot(currentTips()).data;
    const actions = snapshot.menu_actions.actions.filter(action => action.verb === "show_card_tips");
    expect(actions.map(action => action.label)).toEqual(["Show Strike tips", "Show Strike tips"]);
    const subjects = actions.map(action => snapshot.referents.find(value => value.referent_id === action.subject_referent_id)!);
    expect(subjects.map(value => value.properties)).toEqual([
      { definition_id: "STRIKE", cost: "1", is_upgraded: false },
      { definition_id: "STRIKE", cost: "0", is_upgraded: true }
    ]);
    expect(snapshot.referents.find(value => value.referent_id === "topbar-hp")?.role).toBe("topbar_hp");
  });

  it("rejects absent subjects instead of inventing an information target", () => {
    const value = currentTips();
    value.menu_actions.actions[0].subject_referent_id = "not-in-this-frame";
    expect(() => decodeTextMenuV2Snapshot(value)).toThrow();
  });
});
