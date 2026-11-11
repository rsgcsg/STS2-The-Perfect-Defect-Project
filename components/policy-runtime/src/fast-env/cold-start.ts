import { mkdirSync, readdirSync, unlinkSync, writeFileSync } from "node:fs";
import { hostname, tmpdir } from "node:os";
import { join } from "node:path";
import { randomUUID } from "node:crypto";

/**
 * Bounds native cold starts (Host start + first reset) across every fast-env process
 * of one node (WP8 §8.13: 100 starts at once serialise on the candidate check and push
 * first resets past the Host request timeout).
 *
 * A counting semaphore in a node-local directory: a start first writes its own uniquely
 * named file, then counts the files of live holders; at or under the bound it holds,
 * otherwise it removes its file and tries again later (two at once may both back off,
 * never both pass over the bound). A file whose owner is a dead process on this host is
 * removed by whoever sees it; files of other hosts or live processes never are. Waiting
 * has no timeout of its own: holders are bounded by the Host's start and request
 * timeouts. Stop ends the wait (the open is refused, nothing is started).
 */
export interface ColdStartGate {
  /** Resolves with the release function, or null when `cancelled()` became true first. */
  acquire(cancelled: () => boolean): Promise<(() => void) | null>;
}

export interface ColdStartOptions {
  maxConcurrent: number;
  directory: string;
  sleep(ms: number): Promise<void>;
  pid?: number;
  host?: string;
  alive?(pid: number): boolean;
  pollMs?: number;
}

/** Node-local by default (never a job TMPDIR, which may be shared storage), one per user. */
export function defaultColdStartDirectory(): string {
  const uid = typeof process.getuid === "function" ? process.getuid() : "user";
  return join(process.platform === "win32" ? tmpdir() : "/tmp", `sts2-fast-env-cold-start-${uid}`);
}

function processAlive(pid: number): boolean {
  try { process.kill(pid, 0); return true; }
  catch (error) { return (error as NodeJS.ErrnoException).code === "EPERM"; }
}

const HELD = /^held-([0-9]+)-[0-9a-f]{32}-(.+)$/;

export function coldStartGate(options: ColdStartOptions): ColdStartGate {
  const pid = options.pid ?? process.pid, alive = options.alive ?? processAlive, poll = options.pollMs ?? 200;
  const host = (options.host ?? hostname()).replace(/[^A-Za-z0-9._-]/g, "_");
  mkdirSync(options.directory, { recursive: true, mode: 0o700 });

  const holders = (): number => {
    let live = 0;
    for (const name of readdirSync(options.directory)) {
      const match = HELD.exec(name);
      if (!match) continue;
      const owner = Number(match[1]);
      if (match[2] === host && owner !== pid && !alive(owner)) unlinkQuiet(join(options.directory, name));
      else live += 1;
    }
    return live;
  };
  return {
    async acquire(cancelled) {
      for (;;) {
        if (cancelled()) return null;
        const mine = join(options.directory, `held-${pid}-${randomUUID().replaceAll("-", "")}-${host}`);
        writeFileSync(mine, "", { flag: "wx", mode: 0o600 });
        if (holders() <= options.maxConcurrent) {
          let released = false;
          return () => { if (!released) { released = true; unlinkQuiet(mine); } };
        }
        unlinkQuiet(mine);
        await options.sleep(poll + Math.floor(Math.random() * poll));
      }
    }
  };
}

function unlinkQuiet(path: string): void {
  try { unlinkSync(path); }
  catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
}
