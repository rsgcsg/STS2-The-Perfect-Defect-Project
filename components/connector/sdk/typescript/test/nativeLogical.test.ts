import { describe, expect, it } from "vitest";
import {
  decodeNativeLogicalAction, decodeNativeLogicalObservation, decodeNativeLogicalCapture, decodeNativeLogicalContext,
  decodeNativeLogicalRead, decodeNativeLogicalCatalogPage, decodeNativeLogicalResolve, decodeNativeLogicalAttach,
  decodeNativeLogicalEvent, decodeNativeLogicalEvents, decodeNativeLogicalAwait, decodeNativeLogicalResult,
  decodeNativeLogicalCapabilities, decodeNativeLogicalCurrent, decodeNativeLogicalRetain, decodeNativeLogicalRelease,
  decodeNativeLogicalRenew, decodeNativeLogicalCancelWait, decodeNativeLogicalDetach, decodeNativeLogicalAttachRequest,
  digestNativeLogicalActions, parseNativeLogicalJson, validateNativeLogicalPrefix, validateNativeLogicalExpression,
  validateNativeLogicalRequest, type NativeLogicalAction, type NativeLogicalTransportOperation
} from "../src/index.js";
import { nativeFixture } from "./nativeLogicalFixtures.js";

const fixture = nativeFixture();
const decoders: Record<string, (value: unknown) => unknown> = {
  observation: decodeNativeLogicalObservation, capture: decodeNativeLogicalCapture, observation_context: decodeNativeLogicalContext,
  read: decodeNativeLogicalRead, catalog_page: decodeNativeLogicalCatalogPage, resolve: decodeNativeLogicalResolve,
  attach_request: decodeNativeLogicalAttachRequest, attach: decodeNativeLogicalAttach, event: decodeNativeLogicalEvent,
  event_batch: decodeNativeLogicalEvents, await: decodeNativeLogicalAwait, result: decodeNativeLogicalResult,
  capabilities: decodeNativeLogicalCapabilities, current: decodeNativeLogicalCurrent, current_retained: decodeNativeLogicalCurrent,
  current_failed: decodeNativeLogicalCurrent, current_partial: decodeNativeLogicalCurrent, current_partial_observation: decodeNativeLogicalObservation,
  current_partial_read: decodeNativeLogicalRead, retain: decodeNativeLogicalRetain, retain_expired: decodeNativeLogicalRetain,
  release: decodeNativeLogicalRelease, renew: decodeNativeLogicalRenew, renew_expired: decodeNativeLogicalRenew,
  cancel_wait: decodeNativeLogicalCancelWait, cancel_wait_not_pending: decodeNativeLogicalCancelWait,
  detach: decodeNativeLogicalDetach, detach_absent: decodeNativeLogicalDetach
};

describe("native logical shared wire conformance", () => {
  it.each(fixture.digest_cases)("matches C# canonical UTF8 digest $name", value => {
    expect(digestNativeLogicalActions(value.actions)).toBe(value.sha256);
  });

  it.each(Object.entries(fixture.wire_samples))("strictly decodes shared producer sample %s", (name, wire) => {
    if (name.endsWith("_request") && name !== "attach_request") {
      const operation = name.slice(0, -"_request".length) as NativeLogicalTransportOperation;
      expect(() => validateNativeLogicalRequest(operation, structuredClone(wire))).not.toThrow();
    } else {
      expect(decoders[name], `every source fixture needs a decoder: ${name}`).toBeDefined();
      expect(() => decoders[name]!(structuredClone(wire))).not.toThrow();
    }
  });

  it.each(fixture.invalid_wire_cases)("rejects source-negative wire $name", value => {
    expect(decoders[value.type], `every negative source fixture needs a decoder: ${value.type}`).toBeDefined();
    expect(() => decoders[value.type]!(structuredClone(value.wire))).toThrow();
  });

  it.each(fixture.expression_cases.map((value, index) => ({ ...value, index })))
    ("preserves prefix/exact grammar source case $index", value => {
      if (value.prefix_valid) expect(() => validateNativeLogicalPrefix(value.expression as any)).not.toThrow();
      else expect(() => validateNativeLogicalPrefix(value.expression as any)).toThrow();
      if (value.resolve_valid) expect(() => validateNativeLogicalExpression(value.expression as any)).not.toThrow();
      else expect(() => validateNativeLogicalExpression(value.expression as any)).toThrow();
    });

  it("does not normalize Unicode, CRLF, null or ordered arguments when hashing", () => {
    const original = structuredClone(fixture.digest_cases[1]!.actions);
    const changed = structuredClone(original);
    changed[0]!.label = changed[0]!.label.normalize("NFC");
    expect(digestNativeLogicalActions(changed)).not.toBe(digestNativeLogicalActions(original));
    changed[0]!.label = original[0]!.label;
    changed[0]!.subject_referent_id = "";
    expect(digestNativeLogicalActions(changed)).not.toBe(digestNativeLogicalActions(original));
    changed[0]!.subject_referent_id = null;
    changed[2]!.arguments.reverse();
    expect(digestNativeLogicalActions(changed)).not.toBe(digestNativeLogicalActions(original));
  });

  it("rejects duplicate IDs/roles, extra fields and unpaired surrogates before returning a digest", () => {
    const action = fixture.digest_cases[1]!.actions[0]!;
    expect(() => digestNativeLogicalActions([action, action])).toThrow(/duplicate action/u);
    expect(() => digestNativeLogicalActions([{ ...action, hidden: true } as any])).toThrow();
    expect(() => digestNativeLogicalActions([{ ...action, label: "\ud800" }])).toThrow(/surrogate/u);
    expect(() => digestNativeLogicalActions([{ ...action, arguments: [
      { role: "target", referent_id: "one" }, { role: "target", referent_id: "two" }
    ] }])).toThrow(/duplicate argument/u);
  });

  it("rejects duplicate escaped-equivalent JSON fields at any depth", () => {
    expect(() => parseNativeLogicalJson('{"schema":"a","\\u0073chema":"b"}')).toThrow(/duplicate field/u);
    expect(() => parseNativeLogicalJson('{"outer":{"x":1,"x":2}}')).toThrow(/duplicate field/u);
    expect(() => parseNativeLogicalJson('{"valid":true,"late":{"x":1,"x":2}}')).toThrow(/duplicate field/u);
    expect(() => parseNativeLogicalJson('{"text":"\\ud800"}')).toThrow(/surrogate/u);
    expect(() => parseNativeLogicalJson('{"\\udc00":"text"}')).toThrow(/surrogate/u);
    expect(() => parseNativeLogicalJson('{"number":1e999}')).toThrow();
    expect(parseNativeLogicalJson('{"text":"🐉 / café / é\\r\\n", "null":null}')).toEqual({ text: "🐉 / café / é\r\n", null: null });
  });

  it("keeps source U64 strings losslessly and rejects overflow/leading zeros", () => {
    const wire = structuredClone(fixture.wire_samples.event!);
    expect(decodeNativeLogicalEvent(structuredClone(wire)).data.publication_index).toBe("9007199254740993");
    wire.publication_index = "18446744073709551616";
    expect(() => decodeNativeLogicalEvent(wire)).toThrow();
    wire.publication_index = "01";
    expect(() => decodeNativeLogicalEvent(wire)).toThrow();
  });

  it("requires all explicit nullable fixed fields and freezes decoded public records", () => {
    const action = structuredClone(fixture.digest_cases[1]!.actions[0]!) as unknown as Record<string, unknown>;
    delete action.subject_referent_id;
    expect(() => decodeNativeLogicalAction(action)).toThrow();
    const observation = decodeNativeLogicalObservation(structuredClone(fixture.wire_samples.observation!));
    expect(Object.isFrozen(observation.data)).toBe(true);
    expect(Object.isFrozen(observation.data.catalog)).toBe(true);
    expect(() => decodeNativeLogicalObservation({ ...fixture.wire_samples.observation, private_binding: {} })).toThrow();
    expect(() => decodeNativeLogicalObservation({ ...fixture.wire_samples.observation, input_profile: "text-menu-v2" })).toThrow();
  });

  it("permits an explicit empty persistent domain but requires the captured interaction domain", () => {
    const observation = structuredClone(fixture.wire_samples.observation!);
    expect(() => decodeNativeLogicalObservation({ ...observation, persistent: null })).not.toThrow();
    expect(() => decodeNativeLogicalObservation({ ...observation, interaction: null })).toThrow();
    delete observation.persistent;
    expect(() => decodeNativeLogicalObservation(observation)).toThrow();
  });

  it("keeps compound stages bounded with finite delivery and no automatic retry", () => {
    const source = structuredClone(fixture.wire_samples.result!);
    expect(decodeNativeLogicalResult(structuredClone(source)).data.delivery).toBe("partially_delivered");
    source.retry = "automatic";
    expect(() => decodeNativeLogicalResult(source)).toThrow();
    source.retry = "never_automatic";
    source.stages = Array.from({ length: 17 }, () => ({ stage: "focus", delivery: "delivered", evidence: "native" }));
    expect(() => decodeNativeLogicalResult(source)).toThrow();
    source.stages = [{ stage: "🐉".repeat(33), delivery: "delivered", evidence: "native" }];
    expect(() => decodeNativeLogicalResult(source)).toThrow();
  });

  it("does not turn Current failure or partial scope into captured/full success", () => {
    const captured = structuredClone(fixture.wire_samples.current!);
    for (const status of ["stale", "capacity_exceeded", "source_capture_incomplete", "failed"]) {
      expect(() => decodeNativeLogicalCurrent({ ...captured, status, reason: status })).toThrow();
      expect(() => decodeNativeLogicalCurrent({ ...captured, status, context: null, capture: null, retention: null, reason: status })).not.toThrow();
    }
    expect(() => decodeNativeLogicalCurrent({ ...captured, status: "partial", context: null, capture: null, reason: "scope_omission" })).toThrow();
    expect(() => decodeNativeLogicalCurrent({ ...captured, status: "partial", reason: null })).toThrow();
  });
});
