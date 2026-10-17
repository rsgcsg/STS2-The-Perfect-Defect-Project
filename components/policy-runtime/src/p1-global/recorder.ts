import { closeSync, fsyncSync, mkdirSync, openSync, readFileSync, writeSync } from "node:fs";
import { dirname } from "node:path";
import { canonicalJson, sha256Hex, P1G_RECORD_SCHEMA } from "@rsgcsg/sts2-connector-client/p1-global";

/**
 * Append-only episode record. Every line is written and fsync'd before the runner
 * continues, so environment facts exist on disk independently of any Agent consumption.
 * Environment lines carry the Host's exact response bytes; nothing is re-encoded.
 */
export type RecordKind =
  | "record_open" | "host_ready" | "env_request" | "env_response" | "env_error"
  | "p1_observation" | "agent_hello" | "agent_message_in" | "agent_message_out"
  | "p1_choice" | "p1_step" | "p1_result" | "episode_end" | "host_close" | "record_close";

export interface RecordLine { readonly seq: number; readonly at: string; readonly kind: RecordKind; readonly [key: string]: unknown }

export class EpisodeRecorder {
  private fd: number | null;
  private seq = 0;
  private lineCount = 0;
  constructor(readonly path: string, header: Record<string, unknown>) {
    mkdirSync(dirname(path), { recursive: true });
    this.fd = openSync(path, "wx");
    this.append("record_open", { schema: P1G_RECORD_SCHEMA, ...header });
  }

  append(kind: RecordKind, body: Record<string, unknown>): number {
    if (this.fd === null) throw new Error("p1_record_closed");
    this.seq += 1;
    const line = JSON.stringify({ seq: this.seq, at: new Date().toISOString(), kind, ...body }) + "\n";
    writeSync(this.fd, line);
    fsyncSync(this.fd); // durable before the runner proceeds (sync-before-continue)
    this.lineCount += 1;
    return this.seq;
  }

  /** Raw Host line: stored byte-exact with its digest. */
  appendRaw(kind: "env_response" | "agent_message_in", raw: string, extra: Record<string, unknown> = {}): number {
    return this.append(kind, { raw, sha256: sha256Hex(raw), ...extra });
  }

  close(summary: Record<string, unknown>): { path: string; lines: number; sha256: string } {
    this.append("record_close", { lines_before_close: this.lineCount, ...summary });
    if (this.fd !== null) { fsyncSync(this.fd); closeSync(this.fd); this.fd = null; }
    const bytes = readFileSync(this.path);
    return { path: this.path, lines: this.lineCount, sha256: sha256Hex(bytes) };
  }
}

export function readRecord(path: string): RecordLine[] {
  const text = readFileSync(path, "utf8");
  const lines = text.split("\n").filter((line) => line.length > 0);
  const parsed = lines.map((line) => JSON.parse(line) as RecordLine);
  parsed.forEach((line, index) => { if (line.seq !== index + 1) throw new Error(`p1_record_sequence_gap:${index + 1}`); });
  for (const line of parsed) {
    if ((line.kind === "env_response" || line.kind === "agent_message_in")
        && sha256Hex(String(line.raw)) !== line.sha256) throw new Error(`p1_record_digest_mismatch:${line.seq}`);
  }
  return parsed;
}

export { canonicalJson };
