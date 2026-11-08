import { mkdir, open } from "node:fs/promises";
import { join } from "node:path";

/**
 * Per-game evidence seam of the K-game process. The record format and its
 * durability belong to WP6; the process only calls this interface. Records never
 * carry native operands: a decision is its catalog position plus digests.
 */
export interface FastGameEvidence {
  record(kind: string, data: Record<string, unknown>): void;
  close(summary: Record<string, unknown>): Promise<string>;
}
export interface FastEvidenceSink {
  openGame(meta: { slot: number; game_ref: string; game_instance_id: string; r7: boolean }): FastGameEvidence;
}

/** Interim measurement-only sink (not formal evidence): one JSONL file per game,
 * written and fsynced once when the game closes. */
export class MinimalJsonlEvidenceSink implements FastEvidenceSink {
  constructor(private readonly directory: string) {}
  openGame(meta: { slot: number; game_ref: string; game_instance_id: string; r7: boolean }): FastGameEvidence {
    const lines: string[] = [JSON.stringify({ kind: "game_open", ...meta })];
    const directory = this.directory;
    return {
      record(kind, data) { lines.push(JSON.stringify({ kind, ...data })); },
      async close(summary) {
        lines.push(JSON.stringify({ kind: "game_close", ...summary }));
        await mkdir(directory, { recursive: true, mode: 0o700 });
        const path = join(directory, `${meta.game_instance_id.replace(/[^A-Za-z0-9._-]/gu, "_")}.jsonl`);
        const handle = await open(path, "wx", 0o600);
        try { await handle.writeFile(`${lines.join("\n")}\n`); await handle.sync(); }
        finally { await handle.close(); }
        return path;
      }
    };
  }
}
