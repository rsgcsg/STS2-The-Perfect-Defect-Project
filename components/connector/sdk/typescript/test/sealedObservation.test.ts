import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import {
  assembleSealedTextMenuV2, decodeSealedObservationCapture, decodeSealedObservationChunk,
  PlayerEnvironmentRestClient, SEALED_OBSERVATION_SCHEMA, SEALED_OBSERVATION_CHUNK_SCHEMA,
  SEALED_OBSERVATION_RELEASE_SCHEMA, SEALED_OBSERVATION_READ_PROFILE,
  type SealedObservationCapture, type SealedObservationChunk
} from "../src/index.js";

function setup() {
  const snapshot = JSON.parse(readFileSync(new URL("./fixtures/text-menu-v2-targeted-root.json", import.meta.url), "utf8"));
  snapshot.interaction.prompt = "完整中文" + "x".repeat(2500);
  // Put the first Chinese codepoint across a 1024-byte boundary deliberately.
  const start = Buffer.byteLength(JSON.stringify(snapshot).split("完整中文")[0]!, "utf8");
  const padding = (1023 - start % 1024 + 1024) % 1024;
  snapshot.interaction.prompt = "x".repeat(padding) + snapshot.interaction.prompt;
  const serialized = JSON.stringify(snapshot);
  const bytes = Buffer.from(serialized, "utf8");
  const capture: SealedObservationCapture = {
    schema: SEALED_OBSERVATION_SCHEMA, read_profile: SEALED_OBSERVATION_READ_PROFILE,
    input_profile: "text-menu-v2", capture_id: "capture-one", source_snapshot_id: snapshot.snapshot_id,
    session: snapshot.session, generation_id: "generation-one", game_continuity_id: "run-one",
    captured_at: "2026-10-08T00:00:00+00:00", expires_at: "2026-10-08T00:02:00+00:00",
    total_bytes: bytes.length, sha256: createHash("sha256").update(bytes).digest("hex"),
    first_cursor: "cursor-0", capture_ordinal: 1
  };
  const read = ({ cursor, maxBytes = 1024 }: { cursor: string; maxBytes?: number }): SealedObservationChunk => {
    const offset = Number(cursor.slice("cursor-".length));
    const block = bytes.subarray(offset, offset + maxBytes);
    const next = offset + block.length;
    return { schema: SEALED_OBSERVATION_CHUNK_SCHEMA, capture_id: capture.capture_id,
      sha256: capture.sha256, total_bytes: bytes.length, offset, data_base64: block.toString("base64"),
      end: next === bytes.length, next_cursor: next === bytes.length ? null : `cursor-${next}` };
  };
  return { snapshot, serialized, bytes, capture, read };
}
function reader(mutator: (chunk: SealedObservationChunk) => SealedObservationChunk = value => value) {
  const source = setup();
  const readSealed = vi.fn(async (input: { captureId: string; cursor: string; maxBytes?: number }) => {
    const data = mutator(source.read(input));
    return { raw: data as any, data };
  });
  return { ...source, readSealed };
}

describe("sealed public text-v2 byte observations", () => {
  it("assembles exact UTF8 once across split Chinese text and retains metadata separately", async () => {
    const source = reader();
    const full = await assembleSealedTextMenuV2(source.capture, source, 1024);
    expect(full.serializedSnapshot).toBe(source.serialized);
    const unicodeStart = Buffer.byteLength(source.serialized.split("完整中文")[0]!, "utf8");
    expect(unicodeStart % 1024).toBe(1023);
    expect(createHash("sha256").update(full.serializedSnapshot, "utf8").digest("hex")).toBe(source.capture.sha256);
    expect(full.context.snapshot.interaction.prompt).toBe(source.snapshot.interaction.prompt);
    expect(full.context.game_continuity_id).toBe("run-one");
    expect(JSON.parse(full.serializedSnapshot).game_continuity_id).toBeUndefined();
    expect(source.readSealed.mock.calls.length).toBe(Math.ceil(source.bytes.length / 1024));
    const again = await assembleSealedTextMenuV2(source.capture, source, 1024);
    expect(again).toEqual(full);
  });

  it.each([
    ["cross-capture", (chunk: SealedObservationChunk) => ({ ...chunk, capture_id: "other" })],
    ["mixed-digest", (chunk: SealedObservationChunk) => ({ ...chunk, sha256: "0".repeat(64) })],
    ["wrong-total", (chunk: SealedObservationChunk) => ({ ...chunk, total_bytes: chunk.total_bytes + 1 })],
    ["gap", (chunk: SealedObservationChunk) => ({ ...chunk, offset: chunk.offset + 1 })],
    ["short", (chunk: SealedObservationChunk) => ({ ...chunk, data_base64: "YQ==" })],
    ["early-terminal", (chunk: SealedObservationChunk) => ({ ...chunk, end: true, next_cursor: null })],
    ["cycle", (chunk: SealedObservationChunk) => ({ ...chunk, next_cursor: "cursor-0" })]
  ] as const)("rejects %s chunks without delivering model input", async (_, mutate) => {
    const source = reader(mutate);
    await expect(assembleSealedTextMenuV2(source.capture, source, 1024)).rejects.toThrow();
  });

  it("rejects full-length tampering by final digest", async () => {
    const source = reader(chunk => {
      const bytes = Buffer.from(chunk.data_base64, "base64");
      bytes[0] = bytes[0]! ^ 1;
      return { ...chunk, data_base64: bytes.toString("base64") };
    });
    await expect(assembleSealedTextMenuV2(source.capture, source, 1024)).rejects.toThrow("digest mismatch");
  });

  it("strictly validates envelope fields, profile, bounded bytes and chunk terminality", () => {
    const source = setup();
    expect(() => decodeSealedObservationCapture({ ...source.capture, hidden: true })).toThrow();
    expect(() => decodeSealedObservationCapture({ ...source.capture, input_profile: "text-menu-v1" })).toThrow();
    expect(() => decodeSealedObservationCapture({ ...source.capture, total_bytes: 8388609 })).toThrow();
    expect(() => decodeSealedObservationCapture({ ...source.capture, expires_at: source.capture.captured_at })).toThrow();
    expect(() => decodeSealedObservationChunk({ ...source.read({ cursor: "cursor-0" }), end: true })).toThrow();
    expect(() => decodeSealedObservationChunk({ ...source.read({ cursor: "cursor-0" }), data_base64: "%%%%" })).toThrow();
  });

  it("rejects a valid digest whose snapshot session/source or public schema disagrees", async () => {
    const source = reader();
    await expect(assembleSealedTextMenuV2({ ...source.capture, source_snapshot_id: "other" }, source, 1024))
      .rejects.toThrow("source/session mismatch");
    await expect(assembleSealedTextMenuV2({ ...source.capture,
      session: { ...source.capture.session, runtime_instance_id: "other" } }, source, 1024))
      .rejects.toThrow("source/session mismatch");
    source.bytes.fill(32); // mutate payload then update declared digest; schema still must reject
    source.capture.sha256 = createHash("sha256").update(source.bytes).digest("hex");
    await expect(assembleSealedTextMenuV2(source.capture, source, 1024)).rejects.toThrow();
  });

  it("rejects well-hashed extra private Snapshot fields and invalid UTF8", async () => {
    const source = setup();
    for (const bytes of [Buffer.from(JSON.stringify({ ...source.snapshot, private_frame: {} })), Buffer.from([0xff])]) {
      const capture = { ...source.capture, total_bytes: bytes.length,
        sha256: createHash("sha256").update(bytes).digest("hex") };
      const readSealed = async () => {
        const data: SealedObservationChunk = { schema: SEALED_OBSERVATION_CHUNK_SCHEMA,
          capture_id: capture.capture_id, sha256: capture.sha256, offset: 0, total_bytes: bytes.length,
          data_base64: bytes.toString("base64"), next_cursor: null, end: true };
        return { raw: data as any, data };
      };
      await expect(assembleSealedTextMenuV2(capture, { readSealed }, 1048576)).rejects.toThrow();
    }
  });

  it("REST GetFull captures once, reads chunks, releases and preserves actual raw bytes", async () => {
    const source = setup();
    let captureCalls = 0;
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      const route = new URL(url);
      let value: unknown;
      if (route.pathname.endsWith("/current")) {
        captureCalls++;
        expect(route.searchParams.get("input_profile")).toBe("text-menu-v2");
        expect(route.searchParams.get("expected_snapshot_id")).toBe(source.capture.source_snapshot_id);
        value = source.capture;
      } else if (route.pathname.endsWith("/read")) {
        value = source.read({ cursor: route.searchParams.get("cursor")!, maxBytes: Number(route.searchParams.get("max_bytes")) });
      } else {
        expect(JSON.parse(String(init.body))).toEqual({ capture_id: source.capture.capture_id });
        value = { schema: SEALED_OBSERVATION_RELEASE_SCHEMA, capture_id: source.capture.capture_id, released: true };
      }
      return new Response(JSON.stringify(value));
    });
    const client = new PlayerEnvironmentRestClient("http://127.0.0.1:15526", 1000, fetchImpl as typeof fetch);
    const full = await client.getFullTextMenuV2({ expectedSnapshotId: source.capture.source_snapshot_id, maxBytes: 1024 });
    expect(full.serializedSnapshot).toBe(source.serialized);
    expect(captureCalls).toBe(1);
    expect(fetchImpl.mock.calls.at(-1)?.[0]).toContain("/release");
  });

  it("releases on failed assembly and never retries capture on expired read", async () => {
    const source = setup();
    const fetchImpl = vi.fn(async (url: string) => {
      const path = new URL(url).pathname;
      return path.endsWith("/current") ? new Response(JSON.stringify(source.capture))
        : path.endsWith("/read") ? new Response(JSON.stringify({ error: "expired" }), { status: 410 })
        : new Response(JSON.stringify({ schema: SEALED_OBSERVATION_RELEASE_SCHEMA,
          capture_id: source.capture.capture_id, released: true }));
    });
    const client = new PlayerEnvironmentRestClient("http://127.0.0.1:15526", 1000, fetchImpl as typeof fetch);
    await expect(client.getFullTextMenuV2()).rejects.toThrow("410");
    expect(fetchImpl.mock.calls.map(([url]) => new URL(url).pathname.split("/").at(-1))).toEqual(["current", "read", "release"]);
    await expect(client.getFullTextMenuV2({ maxBytes: 0 })).rejects.toThrow("limits");
    expect(fetchImpl).toHaveBeenCalledTimes(3);
  });
});
