import { describe, expect, it } from "vitest";
import { ObservationFrameDivergence, ObservationFrames } from "../src/fast-env/frame-delta.js";
import { pyJsonSha256 } from "../src/fast-env/json-bytes.js";
import { FrameReader } from "./frame-delta-reader.js";

const line = (rowId: number, encoded: { frame: string; parts: string }) =>
  `{"row_id":${rowId},"input":{"input_spec":{},"frame":${encoded.frame}},"parts":${encoded.parts}}`;

/** A game's views: values change, lists grow, shrink, reorder and repeat elements; keys come and go. */
function views(): Record<string, unknown>[] {
  const out: Record<string, unknown>[] = [];
  for (let step = 0; step < 40; step += 1) {
    const hand = Array.from({ length: 3 + (step % 4) }, (_, i) => ({ id: (i + step) % 5, cost: i % 3, name: "Zap ⚡" }));
    const view: Record<string, unknown> = {
      run: { act: 1, floor: Math.floor(step / 7) },
      player: { hp: 70 - step, energy: step % 3, flags: [true, null, 1.5] },
      hand, empty: [], dup: [1, 1, 2, 1],
      catalog: hand.map((card, i) => ({ verb: "play", card: card.id, i })),
    };
    if (step % 5 === 0) view.extra = { note: `s${step}` };
    if (step % 9 === 4) delete view.empty;
    out.push(view);
  }
  return out;
}

describe("fast-env-link-2 observation frames (E7)", () => {
  it("rebuilds every view byte for byte and gives the full-computation observation_sha256", () => {
    const frames = new ObservationFrames({ checkInterval: 1, checkSeed: 7 });
    const reader = new FrameReader();
    let sent = 0, total = 0;
    views().forEach((view, index) => {
      const encoded = frames.encode(view, index + 1);
      const rebuilt = reader.read(line(index + 1, encoded));
      expect(rebuilt.text).toBe(JSON.stringify(view));
      expect(encoded.observationSha256).toBe(pyJsonSha256(view));
      expect(JSON.parse(encoded.frame).base_row_id).toBe(index === 0 ? null : index);
      sent += encoded.sent; total += encoded.total;
    });
    expect(sent).toBeLessThan(total);
    expect(frames.status()).toMatchObject({ check_interval: 1, frames: 40, verified: 40 });
  });

  it("first frame is full, an unchanged view sends nothing", () => {
    const frames = new ObservationFrames({ checkInterval: 64, checkSeed: 1 });
    const view = { a: 1, b: [1, 2] };
    expect(frames.encode(view, 1)).toMatchObject({ sent: 3, total: 3 });
    const again = frames.encode({ a: 1, b: [1, 2] }, 2);
    expect(again).toMatchObject({ sent: 0, parts: "[]" });
    expect(JSON.parse(again.frame).layout).toEqual(["s", "s"]);
    expect(JSON.parse(frames.encode({ a: 1, b: [2, 9, 1] }, 3).frame).layout).toEqual(["s", [1, -1, 0]]);
  });

  it("a receiver catches any broken part, reference or base", () => {
    const frames = new ObservationFrames({ checkInterval: 64, checkSeed: 1 });
    const all = views().map((view, index) => line(index + 1, frames.encode(view, index + 1)));
    const breaks: [string, (text: string) => string][] = [
      ["part byte", text => text.replace(/"name":"Zap ⚡"/u, '"name":"Zap ⚢"')],
      ["layout ref", text => text.replace(/"layout":\[(.*?)\[(\d+)/u, (_m, a, k) => `"layout":[${a}[${(Number(k) + 1) % 3}`)],
      ["base row", text => text.replace(/"base_row_id":\d+/u, '"base_row_id":1')],
    ];
    for (const [name, mutate] of breaks) {
      const reader = new FrameReader();
      const broken = all.map((text, index) => (index === 6 ? mutate(text) : text));
      expect(broken[6], name).not.toBe(all[6]);
      expect(() => broken.forEach(text => reader.read(text)), name).toThrow(/frame_/u);
    }
  });

  it("a wrong frame encoding is fatal, not a fallback", () => {
    const frames = new ObservationFrames({ checkInterval: 1, checkSeed: 1 });
    const view: Record<string, unknown> = { a: { x: 1 } };
    frames.encode(view, 1);
    // A value with a toJSON that is not stable: the cached part no longer matches JSON.stringify(view).
    let calls = 0;
    const flaky = { toJSON: () => (calls++ === 0 ? { x: 1 } : { x: 2 }) };
    expect(() => frames.encode({ a: flaky }, 2)).toThrow(ObservationFrameDivergence);
    expect(() => new ObservationFrames({ checkInterval: 1 }).encode({ a: undefined }, 1)).toThrow();
    expect(() => new ObservationFrames({ checkInterval: 1 }).encode({ "a-b": 1 }, 1)).toThrow(/observation_frame_key/u);
  });
});
