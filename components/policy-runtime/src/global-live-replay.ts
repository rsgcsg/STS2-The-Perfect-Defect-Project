/** Incremental consumer of the ordinary Runtime journal; never drives a game.
 * Original byte/schema/semantic checks are the same replay state machine used
 * after close. The research consumer supplies its own durable segment barrier.
 */
import { open } from "node:fs/promises";
import { createHash } from "node:crypto";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { setTimeout as delay } from "node:timers/promises";
import { globalReplaySemanticIdentity, readLiveManifest, streamGlobalAgentRun, type GlobalReplayEvent } from "./global-replay.js";

export async function* globalJournalEvents(directory: string, bytes?: (raw: Buffer) => void): AsyncGenerator<GlobalReplayEvent> {
  const handle = await open(join(directory, "events.jsonl"), "r");
  let offset = 0, remainder: Buffer = Buffer.alloc(0);
  const buffer = Buffer.alloc(1024 * 1024);
  try {
    while (true) {
      const stat = await handle.stat();
      if (stat.size < offset || stat.size > 128 * 1024 * 1024)
        throw new Error("global_journal_changed_or_limit");
      const { bytesRead } = await handle.read(buffer, 0, buffer.length, offset);
      if (!bytesRead) {
        const manifest = await readLiveManifest(directory);
        if (manifest.ended_at !== null) {
          // Re-read after observing finalization; a preceding stat/read may have
          // raced the last append. Only a genuinely exhausted final file fails.
          if ((await handle.stat()).size === offset) throw new Error("global_stop_required");
        }
        await delay(50);
        continue;
      }
      offset += bytesRead;
      remainder = Buffer.concat([remainder, buffer.subarray(0, bytesRead)]);
      let newline: number;
      while ((newline = remainder.indexOf(10)) >= 0) {
        const line = remainder.subarray(0, newline);
        remainder = remainder.subarray(newline + 1);
        const event = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(line)) as GlobalReplayEvent;
        bytes?.(Buffer.concat([line, Buffer.from("\n")]));
        yield event;
        if (event.kind === "stopped") {
          if (remainder.length || (await handle.stat()).size !== offset)
            throw new Error("global_event_after_stop");
          return;
        }
      }
      if (remainder.length > 8 * 1024 * 1024) throw new Error("global_event_line_limit");
    }
  } finally { await handle.close(); }
}

async function main() {
  if (process.argv.length !== 4 || process.argv[2] !== "--run")
    throw new Error("usage: global-live-replay --run ORIGINAL_DIRECTORY");
  const directory = resolve(process.argv[3]!);
  const write = async (value: unknown) => {
    const bytes = JSON.stringify(value) + "\n";
    if (!process.stdout.write(bytes)) await new Promise<void>(resolve => process.stdout.once("drain", resolve));
  };
  const identity = await globalReplaySemanticIdentity();
  const journal = createHash("sha256");
  let journalBytes = 0, proof: Record<string, unknown> | undefined;
  const report = await streamGlobalAgentRun(directory, globalJournalEvents(directory, raw => {
    journal.update(raw); journalBytes += raw.length;
  }), write, write, value => { proof = value; });
  if (JSON.stringify(identity) !== JSON.stringify(await globalReplaySemanticIdentity()))
    throw new Error("global_live_semantic_code_changed");
  await write({ schema: "sts2.policy-runtime/verified-global-end-1", report,
    proof: { ...proof, identity, journal: { sha256: journal.digest("hex"), bytes: journalBytes } } });
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url))
  main().catch(error => { process.stderr.write(String(error) + "\n"); process.exitCode = 1; });
