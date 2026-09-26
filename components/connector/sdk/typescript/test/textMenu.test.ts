import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { PlayerEnvironmentRestClient, TEXT_MENU_PROFILE, TEXT_MENU_SNAPSHOT_SCHEMA,
  TEXT_MENU_RESULT_SCHEMA, decodePlayerSnapshot, decodePlayerReceipt,
  decodeTextMenuCapabilities, decodeTextMenuSnapshot, decodeTextMenuActionResult } from "../src/index.js";

const fixture = (name: string): any => JSON.parse(readFileSync(
  new URL(`./fixtures/${name}.json`, import.meta.url), "utf8"));
const page = () => fixture("text-menu-root");
const result = () => fixture("text-menu-system-result");
const capabilities = () => ({
  protocol_version: "1.0.0", snapshot_schema: TEXT_MENU_SNAPSHOT_SCHEMA,
  receipt_schema: TEXT_MENU_RESULT_SCHEMA, input_profile: TEXT_MENU_PROFILE,
  action_schema: "sts2.player-environment/action-1", control_schema: "sts2.player-environment/control-1",
  status: "implemented", host: { id: "host", name: "Host", version: "candidate",
    runtime_instance_id: "runtime-1", host_kind: "test", implementation: {
      source_revision: null, module_version_id: null, artifact_sha256: null } },
  game: { version: "v", commit: "commit", branch: null, main_assembly_hash: null,
    compatibility: { status: "test", observation_allowed: true, detail: "test" },
    modset: { status: "test", fingerprint: "modset", scope: "test", loaded_mod_ids: [], detail: "test" } },
  environment_fingerprint: "environment-1", verbs: ["begin_card_play", "focus_target",
    "confirm_target", "open_information"], snapshot_bound: true,
  single_controller: true, execution_available: true,
  control: { recommended_renewal_ms: 1000 }, evidence_profiles: [], non_claims: []
});

describe("text-menu-v1", () => {
  it("decodes only its explicit profile and preserves legacy closure", () => {
    expect(decodeTextMenuCapabilities(capabilities()).data.receipt_schema).toBe(TEXT_MENU_RESULT_SCHEMA);
    expect(decodeTextMenuCapabilities(capabilities()).data.verbs).toContain("begin_card_play");
    expect(() => decodeTextMenuCapabilities({ ...capabilities(), verbs: ["begin_card_play", "begin_card_play"] }))
      .toThrow(/unique/u);
    expect(() => decodeTextMenuCapabilities({ ...capabilities(), verbs: [""] })).toThrow();
    expect(decodeTextMenuSnapshot(page()).data.menu.cursor).toBe("root");
    expect(() => decodePlayerSnapshot(page())).toThrow();
    expect(() => decodePlayerReceipt(result())).toThrow();
    expect(() => decodeTextMenuCapabilities({ ...capabilities(), input_profile: "other" })).toThrow();
    expect(() => decodeTextMenuSnapshot({ ...page(), bound_actions: {} })).toThrow();
    expect(() => decodeTextMenuSnapshot({ ...page(), reads: [] })).toThrow();
  });

  it("rejects illegal edges, effect confusion, duplicate IDs and partial authority", () => {
    const illegal = page(); illegal.menu_actions.actions[0].verb = "open_relic_tips";
    expect(() => decodeTextMenuSnapshot(illegal)).toThrow(/illegal/u);
    const effect = page(); effect.menu_actions.actions[0].effect_domain = "native_input";
    expect(() => decodeTextMenuSnapshot(effect)).toThrow(/effect domain/u);
    const duplicate = page(); duplicate.menu_actions.actions.push({ ...duplicate.menu_actions.actions[0] });
    duplicate.menu_actions.materialized_count = 2; duplicate.menu_actions.total_count = 2;
    expect(() => decodeTextMenuSnapshot(duplicate)).toThrow(/unique/u);
    const partial = page(); partial.status = "visible_unsupported";
    partial.menu_actions.status = "unavailable";
    expect(() => decodeTextMenuSnapshot(partial)).toThrow(/incomplete menu/u);
    partial.menu_actions.actions = []; partial.menu_actions.materialized_count = 0;
    expect(decodeTextMenuSnapshot(partial).data.menu_actions.actions).toEqual([]);
  });

  it("keeps native leaves bound to current referents", () => {
    const p = page();
    p.referents.push({ referent_id: "relic-1", role: "relic", kind: "entity", label: "Relic",
      state: { visible: true, enabled: true, observation_basis: "native_visible_fact" },
      properties_schema: null, properties: null });
    p.menu_actions.actions.push({ action_id: "native-relic-1", kind: "native_input",
      verb: "inspect_relic", label: "Inspect Relic", subject_referent_id: "relic-1",
      arguments: [], effect_domain: "native_input" });
    p.menu_actions.materialized_count = 2; p.menu_actions.total_count = 2;
    expect(decodeTextMenuSnapshot(p).data.menu_actions.actions).toHaveLength(2);
    p.menu_actions.actions[1].subject_referent_id = "missing";
    expect(() => decodeTextMenuSnapshot(p)).toThrow(/unknown current referent/u);
  });

  it("never turns system navigation into native delivery or retryable unknown", () => {
    expect(decodeTextMenuActionResult(result()).data.native_delivery).toBeNull();
    expect(() => decodeTextMenuActionResult({ ...result(), native_delivery: "delivered" })).toThrow();
    expect(() => decodeTextMenuActionResult({ ...result(), retry: "reobserve" })).toThrow();
    const unknown = { ...result(), status: "unknown", effect_domain: "native_input",
      native_delivery: "unknown", action: { ...result().action,
        kind: "native_input", effect_domain: "native_input", verb: "play" } };
    expect(decodeTextMenuActionResult(unknown).data.status).toBe("unknown");
    expect(() => decodeTextMenuActionResult({ ...unknown, retry: "reobserve" })).toThrow(/never/u);
    expect(() => decodeTextMenuActionResult({ ...result(), input_profile: "other" })).toThrow();
  });

  it("requires a profiled successor and every nullable result field", () => {
    const r = result(); r.successor = page(); r.successor.menu.cursor = "information";
    r.successor.menu.revision = 1; r.successor.snapshot_id = "text-runtime-1-native-1-u1";
    r.successor.menu_actions.actions[0].verb = "back";
    expect(decodeTextMenuActionResult(r).data.successor?.menu.revision).toBe(1);
    delete r.reason_code; expect(() => decodeTextMenuActionResult(r)).toThrow();
    r.reason_code = null; r.successor.input_profile = "other";
    expect(() => decodeTextMenuActionResult(r)).toThrow();
  });

  it("uses the explicit selector on all four HTTP calls", async () => {
    const fetchImpl = vi.fn(async (url: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify(
      String(url).includes("/capabilities?") ? capabilities() :
      String(url).includes("/snapshot?") ? page() : result())));
    const client = new PlayerEnvironmentRestClient("http://test", 1000, fetchImpl as typeof fetch);
    await client.textMenuCapabilities(); await client.observeTextMenu();
    await client.submitTextMenu({ requestId: "request-nav-1", expectedSnapshotId: "text-runtime-1-native-1-u0",
      boundActionId: "menu:open_information:1", clientSessionId: "client", controllerLeaseId: "lease",
      controllerGeneration: 1 });
    await client.textMenuResult("request-nav-1");
    expect(String(fetchImpl.mock.calls[0]?.[0])).toContain(`input_profile=${TEXT_MENU_PROFILE}`);
    expect(String(fetchImpl.mock.calls[1]?.[0])).toContain(`input_profile=${TEXT_MENU_PROFILE}`);
    expect(JSON.parse(String(fetchImpl.mock.calls[2]?.[1]?.body)).input_profile).toBe(TEXT_MENU_PROFILE);
    expect(String(fetchImpl.mock.calls[3]?.[0])).toContain(`input_profile=${TEXT_MENU_PROFILE}`);
  });
});
