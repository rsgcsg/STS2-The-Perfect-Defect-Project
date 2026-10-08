import { AgentSessionError } from "./agent-session-contracts.js";

/** Check JSON cost before JSON.stringify allocates the encoded document. */
export function agentJsonByteLength(value: unknown, maxBytes: number): number {
  let bytes = 0, nodes = 0;
  const ancestors = new Set<object>();
  const add = (count: number) => {
    bytes += count;
    if (bytes > maxBytes) throw new AgentSessionError("message_size_limit");
  };
  const string = (text: string) => {
    add(2);
    for (const character of text) {
      const point = character.codePointAt(0)!;
      if (point >= 0xd800 && point <= 0xdfff) throw new AgentSessionError("invalid_unicode_scalar");
      add(point === 34 || point === 92 || [8, 9, 10, 12, 13].includes(point) ? 2
        : point < 32 ? 6 : point < 128 ? 1 : point < 2048 ? 2 : point < 65536 ? 3 : 4);
    }
  };
  const visit = (item: unknown, depth: number) => {
    if (++nodes > 2_000_000 || depth > 64) throw new AgentSessionError("json_structure_limit");
    if (item === null) { add(4); return; }
    if (typeof item === "boolean") { add(item ? 4 : 5); return; }
    if (typeof item === "number") {
      if (!Number.isFinite(item)) throw new AgentSessionError("nonfinite_json_number");
      add(JSON.stringify(item).length); return;
    }
    if (typeof item === "string") { string(item); return; }
    if (typeof item !== "object" || ancestors.has(item)) throw new AgentSessionError("non_json_value");
    if (!Array.isArray(item) && Object.getPrototypeOf(item) !== Object.prototype && Object.getPrototypeOf(item) !== null)
      throw new AgentSessionError("non_json_object");
    ancestors.add(item);
    add(2);
    if (Array.isArray(item)) {
      for (let index = 0; index < item.length; index += 1) {
        if (index > 0) add(1);
        visit(item[index], depth + 1);
      }
    } else {
      const entries = Object.entries(item);
      for (let index = 0; index < entries.length; index += 1) {
        const [key, entry] = entries[index]!;
        if (index > 0) add(1);
        string(key); add(1); visit(entry, depth + 1);
      }
    }
    ancestors.delete(item);
  };
  visit(value, 0);
  return bytes;
}
export function encodeBoundedAgentJson(value: unknown, maxBytes: number): Buffer {
  agentJsonByteLength(value, maxBytes);
  return Buffer.from(JSON.stringify(value), "utf8");
}

/** Binary framing: no unbounded readline string or lenient UTF-8 replacement. */
export class AgentJsonLineFramer {
  private buffer = Buffer.alloc(0);
  private bytes = 0;
  private readonly decoder = new TextDecoder("utf-8", { fatal: true });
  constructor(private readonly maxBytes: number, private readonly onValue: (value: unknown) => void) {}

  push(data: Buffer): void {
    let start = 0;
    for (let index = 0; index < data.length; index += 1) {
      if (data[index] !== 10) continue;
      this.add(data.subarray(start, index));
      const raw = this.buffer.subarray(0, this.bytes);
      this.bytes = 0;
      if (raw.length === 0) throw new AgentSessionError("empty_message");
      let value: unknown;
      try { value = JSON.parse(this.decoder.decode(raw)); }
      catch { throw new AgentSessionError("invalid_json_or_utf8"); }
      this.onValue(value);
      start = index + 1;
    }
    this.add(data.subarray(start));
  }

  end(): void {
    if (this.bytes !== 0) throw new AgentSessionError("unterminated_message");
  }

  private add(part: Buffer): void {
    if (this.bytes + part.length > this.maxBytes) throw new AgentSessionError("message_size_limit");
    if (part.length > 0) {
      const required = this.bytes + part.length;
      if (required > this.buffer.length) {
        const capacity = Math.min(this.maxBytes, Math.max(required, Math.max(512, this.buffer.length * 2)));
        const larger = Buffer.allocUnsafe(capacity);
        this.buffer.copy(larger, 0, 0, this.bytes);
        this.buffer = larger;
      }
      part.copy(this.buffer, this.bytes);
      this.bytes = required;
    }
  }
}
