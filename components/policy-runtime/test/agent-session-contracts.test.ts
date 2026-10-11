import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import {
  supportsProfileValue, validateAgentManifest, validateAgentDirective, type AgentManifest, type AgentConsumption
} from "../src/agent-session-contracts.js";
import { AgentConsumptionLedger, type AgentAcquisition } from "../src/agent-session-consumption.js";
import { AgentJsonLineFramer, encodeBoundedAgentJson } from "../src/agent-session-json.js";

const shared = JSON.parse(readFileSync(new URL("../contracts/fixtures/agent-session-v1.json", import.meta.url), "utf8")) as {
  manifest: AgentManifest; acquisitions: Record<string, AgentAcquisition>;
  support_examples: {
    valid: { interaction_kinds: string[]; action_verbs: string[]; kind: string; verb: string }[];
    invalid_declarations: { field: keyof AgentManifest["support"]; values: string[] }[];
    finite_rejections: { interaction_kinds: string[]; action_verbs: string[]; kind: string; verb: string; expected: string }[];
  };
};
const clone = <T>(value: T): T => structuredClone(value);
function manifest(incremental = false): AgentManifest {
  const value = clone(shared.manifest);
  if (incremental) {
    value.input.history_mode = "scoped_query";
    value.input.consumption_mode = "incremental_view";
    value.input.attachment.delivery_mode = "scoped";
  }
  return validateAgentManifest(value);
}
function acquisition(key: string): AgentAcquisition { return clone(shared.acquisitions[key]!); }
function report(key: string, version: number, previous: string | null = null, advanced = true): AgentConsumption {
  return { acquisition_id: shared.acquisitions[key]!.acquisition_id,
    input_spec: shared.manifest.input.input_spec, continuity_token: "segment",
    previous_consumption_id: previous, consumption_id: `consumed-${version}`,
    state_version: version, advanced };
}

describe("additive Agent manifest/consumption contract", () => {
  it("accepts the shared manifest without requiring scores, index or successor", () => {
    expect(validateAgentManifest(shared.manifest).adapter.protocol).toBe("sts2.policy-runtime/agent-session-ndjson-1");
    expect(shared.manifest.requirements).not.toHaveProperty("successor_required");
  });
  it.each(shared.support_examples.valid)("admits declared profile vocabulary without filtering a candidate", example => {
    const value = manifest();
    value.support.interaction_kinds = example.interaction_kinds;
    value.support.action_verbs = example.action_verbs;
    const input = acquisition("A");
    (input.observation.interaction as Record<string, unknown>).kind = example.kind;
    input.catalog![0]!.verb = example.verb;
    const ledger = new AgentConsumptionLedger(validateAgentManifest(value), "segment");
    ledger.register(input);
    expect(ledger.get(input.acquisition_id).catalog).toEqual(input.catalog);
    expect(ledger.get(input.acquisition_id).catalog).toHaveLength(1);
    expect(ledger.accept(report("A", 1))).toMatchObject({ advanced: true, state_version: 1 });
  });
  it.each(shared.support_examples.invalid_declarations)("rejects mixed or game-identity support wildcards", example => {
    const value = manifest(); value.support[example.field] = example.values;
    expect(() => validateAgentManifest(value)).toThrow("invalid_support_wildcard");
  });
  it.each(shared.support_examples.finite_rejections)("rejects the whole undeclared input and preserves the prefix", example => {
    const value = manifest();
    value.support.interaction_kinds = example.interaction_kinds;
    value.support.action_verbs = example.action_verbs;
    const input = acquisition("A");
    (input.observation.interaction as Record<string, unknown>).kind = example.kind;
    input.catalog![0]!.verb = example.verb;
    const ledger = new AgentConsumptionLedger(value, "segment");
    expect(() => ledger.register(input)).toThrow(example.expected);
    expect(ledger.stateVersion).toBe(0); expect(ledger.consumptionId).toBeNull();
    expect(ledger.byteBudget.used).toBe(0);
    expect(() => ledger.get(input.acquisition_id)).toThrow("unknown_acquisition");
  });
  it("the explicit vocabulary wildcard cannot admit malformed source strings", () => {
    expect(() => supportsProfileValue(["*"], "")).toThrow("invalid_text");
    expect(() => supportsProfileValue(["*"], "\ud800")).toThrow("invalid_text");
    expect(supportsProfileValue(["end_turn"], "confirm")).toBe(false);
  });
  it.each(["k".repeat(257), "界".repeat(86), "k".repeat(65_536), "界".repeat(21_845) + "x"])(
    "uses the Core public semantic field byte bound for kind and stage (%#)", semantic => {
      const value = manifest(); value.support.interaction_kinds = ["*"];
      const input = acquisition("core_null_persistent");
      const interaction = input.observation.interaction as Record<string, unknown>;
      interaction.kind = semantic; interaction.stage = semantic;
      // This is a newly frozen synthetic capture, not a mutation of old source bytes.
      const bytes = Buffer.from(JSON.stringify(input.observation), "utf8");
      input.capture.byte_count = bytes.length;
      input.capture.sha256 = createHash("sha256").update(bytes).digest("hex");
      expect(Buffer.byteLength(semantic, "utf8")).toBeLessThanOrEqual(65_536);
      expect(supportsProfileValue(["*"], semantic)).toBe(true);
      const ledger = new AgentConsumptionLedger(value, "segment");
      ledger.register(input);
      expect(ledger.get(input.acquisition_id).observation.interaction).toEqual(interaction);
      expect(ledger.accept(report("core_null_persistent", 1))).toMatchObject({ advanced: true, state_version: 1 });
    });
  it.each(["kind", "stage"])("rejects an above-bound public %s without accepting any prefix", field => {
    const value = manifest(); value.support.interaction_kinds = ["*"];
    const input = acquisition("core_null_persistent");
    const semantic = "k".repeat(65_537);
    (input.observation.interaction as Record<string, unknown>)[field] = semantic;
    const bytes = Buffer.from(JSON.stringify(input.observation), "utf8");
    input.capture.byte_count = bytes.length;
    input.capture.sha256 = createHash("sha256").update(bytes).digest("hex");
    expect(() => supportsProfileValue(["*"], semantic)).toThrow("invalid_text");
    const ledger = new AgentConsumptionLedger(value, "segment");
    expect(() => ledger.register(input)).toThrow("invalid_text");
    expect(ledger.stateVersion).toBe(0); expect(ledger.byteBudget.used).toBe(0);
  });
  it.each([
    (value: AgentManifest) => { (value as unknown as Record<string, unknown>).executable = "untrusted"; },
    (value: AgentManifest) => { (value.adapter as unknown as Record<string, unknown>).protocol = "sts2.policy-runtime/decision-only-ndjson-2"; },
    (value: AgentManifest) => { value.input.consumption_mode = "incremental_view"; },
    (value: AgentManifest) => { value.limits.max_pending_queries = 9; },
    (value: AgentManifest) => { (value.claims as unknown as Record<string, unknown>).creates_native_operands = true; }
  ])("rejects identity, scope or bound weakening", mutate => {
    const value = manifest(); mutate(value); expect(() => validateAgentManifest(value)).toThrow();
  });
  it("accepts no-score Act and empty-prefix Await; rejects action without consumption", () => {
    const output = { continuity_token: "segment", consumption_id: "one", state_version: 1,
      directive: { type: "act", basis_acquisition_id: "known", selection: { kind: "handle", action_id: "original" }, scores: null } };
    expect(validateAgentDirective(output).directive.type).toBe("act");
    expect(() => validateAgentDirective({ ...output, consumption_id: null, state_version: 0 })).toThrow("Act_requires_consumption");
    expect(validateAgentDirective({ continuity_token: "segment", consumption_id: null, state_version: 0,
      directive: { type: "await", after_cursor: "known", condition: "observation", timeout_ms: 1 } }).directive.type).toBe("await");
  });
  it("consumes an actual complete empty catalog before any scoring or choice", () => {
    const ledger = new AgentConsumptionLedger(manifest(), "segment");
    ledger.register(acquisition("empty"));
    expect(ledger.accept(report("empty", 1))).toMatchObject({ state_version: 1, advanced: true });
    expect(ledger.get(shared.acquisitions.empty!.acquisition_id).catalog).toEqual([]);
  });
  it("deduplicates new acquisition/catalog handles for the same qualified occurrence", () => {
    const ledger = new AgentConsumptionLedger(manifest(), "segment");
    ledger.register(acquisition("A")); ledger.register(acquisition("A_duplicate"));
    ledger.accept(report("A", 1));
    const ack = ledger.accept(report("A_duplicate", 1, "consumed-1", false));
    expect(ack).toMatchObject({ state_version: 1, advanced: false });
    expect(ack.prefix.consumed_publication_index).toBe("12");
    expect(() => ledger.accept(report("A_duplicate", 2, "consumed-1"))).toThrow("consumption_unit_advance_mismatch");
  });
  it("replayed consumption cannot erase another received but unconsumed publication", () => {
    const ledger = new AgentConsumptionLedger(manifest(), "segment");
    ledger.register(acquisition("A")); ledger.register(acquisition("B"));
    ledger.noteReceived("cursor-13", 2);
    expect(ledger.accept(report("A", 1)).prefix.omissions.received_unconsumed_count).toBe(1);
    const duplicate = ledger.accept(report("A", 1, "consumed-1", false));
    expect(duplicate.prefix.omissions.received_unconsumed_count).toBe(1);
    expect(duplicate.state_version).toBe(1);
    expect(ledger.accept(report("B", 2, "consumed-1")).prefix.omissions.received_unconsumed_count).toBe(0);
  });
  it("records descriptor-only catalog exposure only in the declared scoped profile", () => {
    const input = acquisition("A"); input.catalog = null; input.catalog_materialized = false;
    expect(() => new AgentConsumptionLedger(manifest(), "segment").register(input)).toThrow("complete_catalog_required");
    const scoped = manifest(true); scoped.input.state_recovery = { mode: "none", max_state_bytes: 0, model_bindings: [] };
    const ledger = new AgentConsumptionLedger(scoped, "segment"); ledger.register(input);
    const ack = ledger.accept(report("A", 1));
    expect(ledger.get(input.acquisition_id)).toMatchObject({ catalog: null, catalog_materialized: false });
    expect(ack.prefix.history_mode).toBe("scoped_query");
    expect(ledger.get(input.acquisition_id).observation.completeness).toEqual(input.observation.completeness);
  });
  it("later catalog materialization does not manufacture another incremental scope or W advance", () => {
    const input = acquisition("A"); input.catalog = null; input.catalog_materialized = false;
    const ledger = new AgentConsumptionLedger(manifest(true), "segment"); ledger.register(input);
    ledger.accept(report("A", 1));
    const expanded = acquisition("A_duplicate"); ledger.register(expanded);
    const ack = ledger.accept(report("A_duplicate", 1, "consumed-1", false));
    expect(ack).toMatchObject({ state_version: 1, advanced: false });
    expect(ledger.get(expanded.acquisition_id).catalog_materialized).toBe(true);
  });
  it.each([true, false])("rejects a false materialization assertion before retaining any bytes (%s)", marker => {
    const input = acquisition("A"); input.catalog_materialized = marker;
    input.catalog = marker ? null : input.catalog;
    const ledger = new AgentConsumptionLedger(manifest(true), "segment");
    expect(() => ledger.register(input)).toThrow("catalog_materialization_binding");
    expect(ledger.byteBudget.used).toBe(0); expect(ledger.stateVersion).toBe(0);
  });
  it.each(["observed", "terminal"])("consumes a complete %s interaction object with empty C and no invented action", status => {
    const input = acquisition("empty"); input.observation.status = status;
    const ledger = new AgentConsumptionLedger(manifest(), "segment"); ledger.register(input);
    expect(ledger.accept(report("empty", 1))).toMatchObject({ state_version: 1, advanced: true });
    expect(ledger.get(input.acquisition_id).catalog).toEqual([]);
  });
  it.each(["renew", "retain", "release", "cancel_wait", "detach", "result", "read", "catalog"])(
    "requires the actual full-reference lifecycle prerequisite %s in the closed manifest", method => {
      const value = manifest(); value.requirements.required_methods = value.requirements.required_methods.filter(item => item !== method);
      expect(() => validateAgentManifest(value)).toThrow("required_methods_mismatch");
    });
  it("requires scoped Current's sealed-read/pin prerequisites without forcing unrelated query or scoring methods", () => {
    const value = manifest(true); value.input.state_recovery = { mode: "none", max_state_bytes: 0, model_bindings: [] };
    const common = ["capabilities", "attach", "events", "await", "cancel_wait", "detach", "submit", "result", "renew"];
    value.requirements.required_methods = common;
    expect(validateAgentManifest(value).requirements.required_methods).toEqual(common);
    value.requirements.required_methods = [...common, "current"];
    expect(() => validateAgentManifest(value)).toThrow("required_methods_mismatch");
    value.requirements.required_methods = [...common, "current", "read", "retain", "release"];
    expect(validateAgentManifest(value).requirements.required_methods).not.toContain("resolve");
  });
  it("a new publication of the same occurrence is counted once without a state advance", () => {
    const ledger = new AgentConsumptionLedger(manifest(), "segment");
    ledger.register(acquisition("A"));
    const nextPublication = acquisition("A_duplicate"); nextPublication.publication_index = "13";
    ledger.register(nextPublication); ledger.noteReceived("cursor-13", 2);
    ledger.accept(report("A", 1));
    expect(ledger.accept(report("A_duplicate", 1, "consumed-1", false)).prefix).toMatchObject({
      consumed_publication_index: "13", omissions: { received_unconsumed_count: 0 }
    });
    ledger.noteReceived("cursor-14", 1);
    expect(ledger.accept(report("A_duplicate", 1, "consumed-1", false)).prefix.omissions.received_unconsumed_count).toBe(1);
  });
  it("advances A-B-A and same-feature new focus by occurrence, never content hash", () => {
    const ledger = new AgentConsumptionLedger(manifest(), "segment");
    for (const [index, key] of ["A", "B", "A_return", "same_features_new_focus"].entries()) {
      ledger.register(acquisition(key));
      ledger.accept(report(key, index + 1, index === 0 ? null : `consumed-${index}`));
    }
    expect(ledger.stateVersion).toBe(4);
  });
  it("allows incremental new included fields, but no new state from random scope IDs", () => {
    const ledger = new AgentConsumptionLedger(manifest(true), "segment");
    for (const key of ["incremental_persistent", "incremental_expanded", "incremental_expanded_duplicate"]) ledger.register(acquisition(key));
    ledger.accept(report("incremental_persistent", 1));
    ledger.accept(report("incremental_expanded", 2, "consumed-1"));
    const duplicate = ledger.accept(report("incremental_expanded_duplicate", 2, "consumed-2", false));
    expect(duplicate.state_version).toBe(2);
    expect(duplicate.prefix.omissions.missing_scopes).toEqual(["referents", "catalog"]);
    // No Model or W was created by this stateless programmed Agent path.
  });
  it("rejects same-scope/overlapping field drift even after a consumed acquisition is released", () => {
    const ledger = new AgentConsumptionLedger(manifest(true), "segment");
    ledger.register(acquisition("incremental_persistent")); ledger.accept(report("incremental_persistent", 1));
    ledger.release(shared.acquisitions.incremental_persistent!.acquisition_id);
    const drift = acquisition("incremental_expanded");
    (drift.observation.persistent as { content: { hp: number } }).content.hp = 99;
    expect(() => ledger.register(drift)).toThrow("same_occurrence_coherence_drift");
  });
  it("rejects old known acquisitions and leaves the accepted prefix unchanged", () => {
    const ledger = new AgentConsumptionLedger(manifest(true), "segment");
    ledger.register(acquisition("A")); ledger.register(acquisition("query_current"));
    ledger.accept(report("query_current", 1));
    expect(() => ledger.accept(report("A", 1, "consumed-1", false))).toThrow("native_revision_regressed");
    expect(ledger.stateVersion).toBe(1);
    expect(() => ledger.accept({ ...report("query_current", 2, "consumed-1"), acquisition_id: "fabricated" })).toThrow("unknown_acquisition");
  });
  it("fails malformed native frames outside the child/Model boundary", () => {
    const ledger = new AgentConsumptionLedger(manifest(), "segment");
    const malformed = acquisition("A"); malformed.observation.interaction = null;
    expect(() => ledger.register(malformed)).toThrow("native_interaction_required");
    expect(ledger.stateVersion).toBe(0);
    const incomplete = acquisition("A");
    (incomplete.observation.catalog as Record<string, unknown>).total_count = 2;
    expect(() => ledger.register(incomplete)).toThrow("catalog_count_binding");
  });
  it("preserves actual Core full-reference persistent:null without fabricating an empty object", () => {
    const value = manifest(); value.support.interaction_kinds.push("selector");
    const ledger = new AgentConsumptionLedger(value, "segment");
    const nullable = acquisition("core_null_persistent");
    expect(nullable.observation.completeness).toMatchObject({ status: "complete",
      included: ["persistent", "interaction", "referents", "catalog"], full_reference_complete: true });
    ledger.register(nullable);
    expect(ledger.accept(report("core_null_persistent", 1))).toMatchObject({ advanced: true, state_version: 1 });
    expect(ledger.get(nullable.acquisition_id).observation.persistent).toBeNull();
    for (const kind of ["missing", "undefined", "interaction_null"] as const) {
      const rejected = acquisition("core_null_persistent");
      if (kind === "missing") delete rejected.observation.persistent;
      else if (kind === "undefined") rejected.observation.persistent = undefined;
      else rejected.observation.interaction = null;
      expect(() => new AgentConsumptionLedger(value, "segment").register(rejected)).toThrow();
    }
  });
  it("requires declared reset and never turns a gap into seamless history", () => {
    const value = manifest(); value.input.gap_policy = "explicit_reset";
    const ledger = new AgentConsumptionLedger(value, "segment");
    expect(() => ledger.explicitReset("new")).toThrow("reset_requires_declared_gap");
    ledger.recordGap({ reason: "retention_gap", from_publication_index: "13", through_publication_index: "15" });
    ledger.explicitReset("new");
    ledger.register(acquisition("query_current"));
    const ack = ledger.accept({ ...report("query_current", 1), continuity_token: "new" });
    expect(ack.prefix.omissions.gap).toMatchObject({ reason: "retention_gap" });
    expect(ack.prefix.omissions.received_unconsumed_count).toBeNull();
  });
});

describe("bounded Agent NDJSON", () => {
  it("measures UTF-8/escaping cost before serialization and rejects cycles/scalars", () => {
    const value = { unicode: "🐉é", control: "\n\"" };
    const actual = Buffer.byteLength(JSON.stringify(value));
    expect(encodeBoundedAgentJson(value, actual).toString()).toBe(JSON.stringify(value));
    expect(() => encodeBoundedAgentJson(value, actual - 1)).toThrow("message_size_limit");
    const cycle: Record<string, unknown> = {}; cycle.self = cycle;
    expect(() => encodeBoundedAgentJson(cycle, 100)).toThrow("non_json_value");
    expect(() => encodeBoundedAgentJson({ bad: "\ud800" }, 100)).toThrow("invalid_unicode_scalar");
  });
  it("reassembles multilingual bytes, caps a fragmented line and rejects unterminated/invalid UTF-8", () => {
    const values: unknown[] = [];
    const raw = Buffer.from(JSON.stringify({ text: "🐉" }) + "\n");
    const framer = new AgentJsonLineFramer(100, value => values.push(value));
    for (const byte of raw) framer.push(Buffer.from([byte]));
    framer.end(); expect(values).toEqual([{ text: "🐉" }]);
    const oversized = new AgentJsonLineFramer(3, () => {});
    oversized.push(Buffer.from("abc")); expect(() => oversized.push(Buffer.from("d"))).toThrow("message_size_limit");
    const unterminated = new AgentJsonLineFramer(20, () => {});
    unterminated.push(Buffer.from("{}")); expect(() => unterminated.end()).toThrow("unterminated_message");
    expect(() => new AgentJsonLineFramer(20, () => {}).push(Buffer.from([123, 255, 125, 10]))).toThrow("invalid_json_or_utf8");
  });
});
