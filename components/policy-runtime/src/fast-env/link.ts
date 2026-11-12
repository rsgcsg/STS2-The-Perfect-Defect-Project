import { createConnection, type Socket } from "node:net";
import { createInterface } from "node:readline";
import { FAST_ENV_LINK_SCHEMA, FastEnvContractError, parseLinkReply, type LinkReply } from "./contracts.js";

export type LinkResult =
  | { kind: "reply"; reply: LinkReply }
  /** Names this pending row but breaks the reply contract: never applied, the row is not_started. */
  | { kind: "invalid"; detail: string }
  | { kind: "timeout" }
  /** Stop: the environment gave the row up before any reply; it is never applied. */
  | { kind: "cancelled" }
  | { kind: "disconnected" };

/** Environment side of fast-env-link-2. One instance per environment process. */
export interface DecisionLinkPort {
  /** Sends one message that expects no reply (hello, episode_open, step_outcome, episode_close). */
  send(message: Record<string, unknown>): void;
  /**
   * Sends one decision line (already serialised, without its trailing newline) and
   * waits for its reply or the timeout. Never resends. A reply after the timeout,
   * for any row that is not pending, or naming a game instance that is no longer
   * open, is discarded and counted (late); the pending row keeps waiting.
   */
  decide(rowId: number, gameInstanceId: string, line: string, timeoutMs: number): Promise<LinkResult>;
  /** Stop: every pending row resolves as cancelled now; a reply that comes later is late. */
  cancelPending(): void;
  /** Which game instances are open now (late-reply rule). */
  setOpenInstances(isOpen: (gameInstanceId: string) => boolean): void;
  readonly lateReplies: number;
  readonly invalidReplies: number;
  readonly connected: boolean;
  close(): Promise<void>;
}

interface Pending { gameInstanceId: string; resolve: (result: LinkResult) => void; timer: NodeJS.Timeout }

/** NDJSON over one stream pair; the Unix socket client and the tests use it. */
export class StreamDecisionLink implements DecisionLinkPort {
  #pending = new Map<number, Pending>();
  #late = 0;
  #invalid = 0;
  #connected = true;
  #onDisconnect: ((reason: string) => void) | null;
  #isOpen: (gameInstanceId: string) => boolean = () => false;

  constructor(private readonly output: { write(chunk: string): boolean; end(): void },
              input: NodeJS.ReadableStream, onDisconnect: ((reason: string) => void) | null = null) {
    this.#onDisconnect = onDisconnect;
    const lines = createInterface({ input, crlfDelay: Infinity });
    lines.on("line", line => this.#accept(line));
    lines.on("close", () => this.#disconnect("link_closed"));
  }

  get lateReplies() { return this.#late; }
  get invalidReplies() { return this.#invalid; }
  get connected() { return this.#connected; }

  setOpenInstances(isOpen: (gameInstanceId: string) => boolean): void { this.#isOpen = isOpen; }

  cancelPending(): void {
    for (const [rowId, pending] of this.#pending) { clearTimeout(pending.timer); this.#pending.delete(rowId); pending.resolve({ kind: "cancelled" }); }
  }

  #accept(line: string): void {
    let raw: unknown;
    try { raw = JSON.parse(line); }
    catch { this.#invalid += 1; return; }
    const rowId = raw !== null && typeof raw === "object" ? (raw as Record<string, unknown>).row_id : undefined;
    const pending = typeof rowId === "number" ? this.#pending.get(rowId) : undefined;
    // A reply that cannot be attributed to a pending row is never applied to one.
    if (!pending) { if (typeof rowId === "number") this.#late += 1; else this.#invalid += 1; return; }
    const gameInstanceId = (raw as Record<string, unknown>).game_instance_id;
    if (gameInstanceId !== pending.gameInstanceId && !(typeof gameInstanceId === "string" && this.#isOpen(gameInstanceId))) {
      this.#late += 1; return;
    }
    this.#pending.delete(rowId as number);
    clearTimeout(pending.timer);
    try { pending.resolve({ kind: "reply", reply: parseLinkReply(raw) }); }
    catch (error) { pending.resolve({ kind: "invalid", detail: error instanceof FastEnvContractError ? error.field : "reply" }); }
  }

  #disconnect(reason: string): void {
    if (!this.#connected) return;
    this.#connected = false;
    for (const [rowId, pending] of this.#pending) { clearTimeout(pending.timer); this.#pending.delete(rowId); pending.resolve({ kind: "disconnected" }); }
    this.#onDisconnect?.(reason);
  }

  send(message: Record<string, unknown>): void {
    if (!this.#connected) return;
    this.output.write(`${JSON.stringify({ schema: FAST_ENV_LINK_SCHEMA, ...message })}\n`);
  }

  decide(rowId: number, gameInstanceId: string, line: string, timeoutMs: number): Promise<LinkResult> {
    if (!this.#connected) return Promise.resolve({ kind: "disconnected" });
    if (this.#pending.has(rowId)) throw new Error("fast_env_row_id_reused");
    return new Promise(resolve => {
      const timer = setTimeout(() => {
        if (this.#pending.get(rowId)?.timer !== timer) return;
        this.#pending.delete(rowId);
        resolve({ kind: "timeout" });
      }, timeoutMs);
      this.#pending.set(rowId, { gameInstanceId, resolve, timer });
      this.output.write(`${line}\n`);
    });
  }

  async close(): Promise<void> {
    this.#onDisconnect = null;
    this.#disconnect("closed_by_environment");
    this.output.end();
  }
}

export async function connectUnixDecisionLink(path: string, onDisconnect: (reason: string) => void): Promise<DecisionLinkPort> {
  const socket: Socket = await new Promise((resolve, reject) => {
    const connection = createConnection(path);
    connection.once("connect", () => { connection.off("error", reject); resolve(connection); });
    connection.once("error", reject);
  });
  socket.setNoDelay(true);
  socket.on("error", () => undefined); // the readline close reports the disconnect
  return new StreamDecisionLink(socket, socket, onDisconnect);
}
