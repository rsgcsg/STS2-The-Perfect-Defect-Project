import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import {
  PlayerEnvironmentRestClient,
  decodeTextMenuActionResult, decodeTextMenuCapabilities, decodeTextMenuSnapshot,
  decodeTextMenuV2ActionResult, decodeTextMenuV2Capabilities,
  decodeTextMenuV2ObservationContext, decodeTextMenuV2Snapshot,
  TEXT_MENU_V2_OBSERVATION_CONTEXT_SCHEMA, TEXT_MENU_V2_PROFILE,
  TEXT_MENU_V2_RESULT_SCHEMA, TEXT_MENU_V2_SNAPSHOT_SCHEMA
} from "../src/index.js";

const fixture = (name: string): any => JSON.parse(readFileSync(
  new URL(`./fixtures/${name}.json`, import.meta.url), "utf8"));
const targeted = () => fixture("text-menu-v2-targeted-root");
const targetedResult = () => fixture("text-menu-v2-targeted-select");
const cardOnly = () => fixture("text-menu-v2-card-only-root");
const cardOnlyResult = () => fixture("text-menu-v2-card-only-select");

describe("opt-in text-menu-v2 SDK contract", () => {
  it("routes each explicit v2 REST call through the existing strict decoder", async () => {
    const root = targeted();
    const selected = targetedResult();
    const capabilities = {
      protocol_version: "1.0.0", snapshot_schema: TEXT_MENU_V2_SNAPSHOT_SCHEMA,
      receipt_schema: TEXT_MENU_V2_RESULT_SCHEMA, input_profile: TEXT_MENU_V2_PROFILE,
      action_schema: "sts2.player-environment/action-1", control_schema: "sts2.player-environment/control-1",
      status: "implemented", host: { id: "host", name: "Host", version: "candidate",
        runtime_instance_id: "runtime-1", host_kind: "test", implementation: {
          source_revision: null, module_version_id: null, artifact_sha256: null } },
      game: { version: "v", commit: "commit", branch: null, main_assembly_hash: null,
        compatibility: { status: "test", observation_allowed: true, detail: "test" },
        modset: { status: "test", fingerprint: "modset", scope: "test", loaded_mod_ids: [], detail: "test" } },
      environment_fingerprint: "environment-1", verbs: ["select_card", "select_target", "play"],
      snapshot_bound: true, single_controller: true, execution_available: true,
      control: { recommended_renewal_ms: 1000 }, evidence_profiles: [], non_claims: []
    };
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      const route = new URL(url);
      const value = route.pathname.endsWith("/capabilities") ? capabilities
        : route.pathname.endsWith("/observation-context")
          ? { schema: TEXT_MENU_V2_OBSERVATION_CONTEXT_SCHEMA, snapshot: root, game_continuity_id: "game-1" }
          : route.pathname.endsWith("/snapshot") ? root : selected;
      return new Response(JSON.stringify(value), { status: init.method === "POST" ? 200 : 200 });
    });
    const client = new PlayerEnvironmentRestClient("http://127.0.0.1:15526", 1000, fetchImpl as typeof fetch);
    expect((await client.textMenuV2Capabilities()).data.input_profile).toBe("text-menu-v2");
    expect((await client.observeTextMenuV2()).data.menu_actions.actions).toEqual(root.menu_actions.actions);
    expect((await client.observeTextMenuV2Context()).data.game_continuity_id).toBe("game-1");
    const action = root.menu_actions.actions[0];
    const result = await client.submitTextMenuV2({ requestId: selected.request_id,
      expectedSnapshotId: root.snapshot_id, boundActionId: action.action_id,
      clientSessionId: "client", controllerLeaseId: "lease", controllerGeneration: 1 },
    decodeTextMenuV2Snapshot(root).data);
    expect(result.data.successor?.menu.cursor).toBe("card_targets");
    expect((await client.textMenuV2Result(selected.request_id)).data.action?.action_id).toBe(action.action_id);
    expect(fetchImpl.mock.calls.map(([url]) => new URL(url).search)).toEqual([
      "?input_profile=text-menu-v2", "?input_profile=text-menu-v2", "?input_profile=text-menu-v2", "", "?input_profile=text-menu-v2"
    ]);
    expect(JSON.parse(String(fetchImpl.mock.calls[3]?.[1].body))).toMatchObject({
      input_profile: "text-menu-v2", expected_snapshot_id: root.snapshot_id, bound_action_id: action.action_id
    });
  });

  it("accepts complete targeted and card-only menu selections without widening v1", () => {
    const root = targeted();
    const result = targetedResult();
    expect(decodeTextMenuV2Snapshot(root).data.menu.selection).toEqual([]);
    expect(decodeTextMenuV2ActionResult(result, decodeTextMenuV2Snapshot(root).data)
      .data.successor?.menu.selection).toEqual([{ role: "card", referent_id: "card-C" }]);
    const noTarget = cardOnlyResult();
    expect(decodeTextMenuV2ActionResult(noTarget, decodeTextMenuV2Snapshot(cardOnly()).data)
      .data.successor?.menu.cursor).toBe("card_confirmation");
    expect(noTarget.successor.menu.selection).toEqual([{ role: "card", referent_id: "card-C" }]);
    expect(noTarget.successor.menu_actions.actions[0].arguments).toEqual([]);
    expect(() => decodeTextMenuSnapshot(root)).toThrow();
    expect(() => decodeTextMenuSnapshot(result.successor)).toThrow();
    expect(() => decodeTextMenuActionResult(result)).toThrow();
  });

  it("keeps capability and observation context profiles distinct", () => {
    const legacy = fixture("text-menu-root");
    const v2 = targeted();
    const cap = {
      protocol_version: "1.0.0", snapshot_schema: TEXT_MENU_V2_SNAPSHOT_SCHEMA,
      receipt_schema: TEXT_MENU_V2_RESULT_SCHEMA, input_profile: TEXT_MENU_V2_PROFILE,
      action_schema: "sts2.player-environment/action-1", control_schema: "sts2.player-environment/control-1",
      status: "implemented", host: { id: "host", name: "Host", version: "candidate",
        runtime_instance_id: "runtime-1", host_kind: "test", implementation: {
          source_revision: null, module_version_id: null, artifact_sha256: null } },
      game: { version: "v", commit: "commit", branch: null, main_assembly_hash: null,
        compatibility: { status: "test", observation_allowed: true, detail: "test" },
        modset: { status: "test", fingerprint: "modset", scope: "test", loaded_mod_ids: [], detail: "test" } },
      environment_fingerprint: "environment-1", verbs: ["select_card", "select_target", "play"],
      snapshot_bound: true, single_controller: true, execution_available: true,
      control: { recommended_renewal_ms: 1000 }, evidence_profiles: [], non_claims: []
    };
    expect(decodeTextMenuV2Capabilities(cap).data.input_profile).toBe(TEXT_MENU_V2_PROFILE);
    expect(() => decodeTextMenuCapabilities(cap)).toThrow();
    expect(() => decodeTextMenuV2Capabilities({ ...cap, input_profile: "text-menu-v1" })).toThrow();
    expect(() => decodeTextMenuV2Snapshot(legacy)).toThrow();
    const context = { schema: TEXT_MENU_V2_OBSERVATION_CONTEXT_SCHEMA, snapshot: v2,
      game_continuity_id: null };
    expect(decodeTextMenuV2ObservationContext(context).data.snapshot.snapshot_id).toBe(v2.snapshot_id);
    expect(() => decodeTextMenuV2ObservationContext({ ...context, extra: true })).toThrow();
  });

  it("requires visible subjects, exact selection phases and a complete current catalog", () => {
    const root = targeted();
    const badSubject = targeted(); badSubject.menu_actions.actions[0].subject_referent_id = "missing";
    expect(() => decodeTextMenuV2Snapshot(badSubject)).toThrow(/enabled visible/u);
    const disabled = targeted(); disabled.referents[0].state.visible = false;
    expect(() => decodeTextMenuV2Snapshot(disabled)).toThrow(/enabled visible/u);
    const wrongPhase = targetedResult().successor; wrongPhase.menu.selection = [];
    expect(() => decodeTextMenuV2Snapshot(wrongPhase)).toThrow(/cursor and selection/u);
    const wrongTarget = cardOnlyResult().successor;
    wrongTarget.referents.push(targeted().referents[1]);
    wrongTarget.menu.selection.push({ role: "target", referent_id: "enemy-E" });
    expect(() => decodeTextMenuV2Snapshot(wrongTarget)).toThrow(/confirmation/u);
    const partial = targeted(); partial.menu_actions.status = "truncated";
    expect(() => decodeTextMenuV2Snapshot(partial)).toThrow(/incomplete menu/u);
    const duplicate = targeted(); duplicate.menu_actions.actions.push({ ...root.menu_actions.actions[0] });
    duplicate.menu_actions.materialized_count = duplicate.menu_actions.total_count = 2;
    expect(() => decodeTextMenuV2Snapshot(duplicate)).toThrow(/duplicate/u);
    const extra = targeted(); extra.menu.extra = true;
    expect(() => decodeTextMenuV2Snapshot(extra)).toThrow();
  });

  it("binds staged card choices to a ready combat page and correct public roles", () => {
    const wrongCardRole = targeted(); wrongCardRole.referents[0].role = "enemy";
    expect(() => decodeTextMenuV2Snapshot(wrongCardRole)).toThrow(/card or target referent/u);
    const disabledCard = targeted(); disabledCard.referents[0].state.enabled = false;
    expect(() => decodeTextMenuV2Snapshot(disabledCard)).toThrow(/enabled visible/u);
    const alternateCardRole = targeted(); alternateCardRole.referents[0].role = "card";
    expect(decodeTextMenuV2Snapshot(alternateCardRole).data.menu.cursor).toBe("root");

    const targetPage = targetedResult().successor;
    targetPage.referents[1].state.enabled = false;
    expect(() => decodeTextMenuV2Snapshot(targetPage)).toThrow(/enabled visible/u);
    targetPage.referents[1].state.enabled = null;
    targetPage.referents[1].role = "target";
    expect(decodeTextMenuV2Snapshot(targetPage).data.menu.cursor).toBe("card_targets");

    const wrongPage = targeted(); wrongPage.interaction.kind = "map_navigation";
    wrongPage.interaction.content_schema = "sts2.player-environment/surface/map_navigation-1";
    wrongPage.interaction.content.surface.kind = "map_navigation";
    expect(() => decodeTextMenuV2Snapshot(wrongPage)).toThrow(/ready public combat page/u);
  });

  it("does not treat the existing card_tips information cursor as card staging", () => {
    const tips = targeted();
    tips.menu.cursor = "card_tips";
    tips.menu.selection = [{ role: "card", referent_id: "card-C" }];
    tips.menu_actions.actions = [{ action_id: "back-card-tips", kind: "system_navigation",
      verb: "back", label: "Back", subject_referent_id: null, arguments: [], effect_domain: "text_menu" }];
    expect(() => decodeTextMenuV2Snapshot(tips)).toThrow(/cursor and selection/u);
    tips.menu.selection = [];
    expect(decodeTextMenuV2Snapshot(tips).data.menu.cursor).toBe("card_tips");
  });

  it("does not turn cancel into a bound entity or a native delivery", () => {
    const page = targetedResult().successor;
    const cancel = page.menu_actions.actions[1];
    expect(cancel.subject_referent_id).toBeNull();
    expect(decodeTextMenuV2Snapshot(page).data.menu_actions.actions[1]?.verb).toBe("cancel_selection");
    const invalid = structuredClone(page); invalid.menu_actions.actions[1].subject_referent_id = "card-C";
    expect(() => decodeTextMenuV2Snapshot(invalid)).toThrow(/system selection/u);
    const result = targetedResult(); result.native_delivery = "delivered";
    expect(() => decodeTextMenuV2ActionResult(result, decodeTextMenuV2Snapshot(targeted()).data)).toThrow();
  });

  it("checks a system result against its previous public menu and successor", () => {
    const previous = decodeTextMenuV2Snapshot(targeted()).data;
    const value = targetedResult();
    const stale = structuredClone(value); stale.successor.menu.native_snapshot_id = "changed-source";
    expect(() => decodeTextMenuV2ActionResult(stale, previous)).toThrow(/successor/u);
    const noProgress = structuredClone(value); noProgress.successor.sequence = previous.sequence;
    expect(() => decodeTextMenuV2ActionResult(noProgress, previous)).toThrow(/successor/u);
    const wrongAction = structuredClone(value); wrongAction.action.action_id = "unadvertised";
    expect(() => decodeTextMenuV2ActionResult(wrongAction, previous)).toThrow(/previous complete menu/u);
    const missing = structuredClone(value); missing.successor = null;
    expect(() => decodeTextMenuV2ActionResult(missing, previous)).toThrow(/successor/u);
    const wrongStandalone = structuredClone(value); wrongStandalone.successor.menu.cursor = "information";
    wrongStandalone.successor.menu.selection = [];
    wrongStandalone.successor.menu_actions.actions = [{ action_id: "back-info", kind: "system_navigation",
      verb: "back", label: "Back", subject_referent_id: null, arguments: [], effect_domain: "text_menu" }];
    wrongStandalone.successor.menu_actions.materialized_count = 1;
    wrongStandalone.successor.menu_actions.total_count = 1;
    expect(() => decodeTextMenuV2ActionResult(wrongStandalone)).toThrow(/successor/u);
    const unknown = structuredClone(value); unknown.status = "unknown"; unknown.effect_domain = "native_input";
    unknown.native_delivery = "unknown"; unknown.retry = "reobserve"; unknown.successor = null;
    expect(() => decodeTextMenuV2ActionResult(unknown, previous)).toThrow(/never/u);
  });

  it("keeps delivered and unknown native leaves distinct from menu selection", () => {
    const confirm = decodeTextMenuV2Snapshot(cardOnlyResult().successor).data;
    const play = confirm.menu_actions.actions[0];
    const base = cardOnlyResult();
    const delivered = { ...base, request_id: "request-play-1", action: play,
      status: "applied", effect_domain: "native_input", native_delivery: "delivered", successor: null };
    expect(decodeTextMenuV2ActionResult(delivered, confirm).data.native_delivery).toBe("delivered");
    const unknown = { ...delivered, status: "unknown", native_delivery: "unknown" };
    expect(decodeTextMenuV2ActionResult(unknown, confirm).data.retry).toBe("never");
    expect(() => decodeTextMenuV2ActionResult({ ...unknown, retry: "reobserve" }, confirm)).toThrow(/never/u);
    expect(() => decodeTextMenuV2ActionResult({ ...unknown, successor: confirm }, confirm)).toThrow(/successor/u);
  });

  it("requires cancellation to clear staged selection in a progressed menu", () => {
    const current = decodeTextMenuV2Snapshot(targetedResult().successor).data;
    const cancel = current.menu_actions.actions.find(action => action.verb === "cancel_selection")!;
    const cleared = targeted();
    cleared.snapshot_id = "v2-menu-22"; cleared.sequence = 22; cleared.menu.revision = 2;
    const result = { ...targetedResult(), request_id: "request-cancel-2", action: cancel, successor: cleared };
    expect(decodeTextMenuV2ActionResult(result, current).data.successor?.menu.selection).toEqual([]);
    const retained = structuredClone(result);
    retained.successor.menu.selection = [{ role: "card", referent_id: "card-C" }];
    expect(() => decodeTextMenuV2ActionResult(retained, current)).toThrow();
  });
});
