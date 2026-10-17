import { P1G_PORT_PROTOCOL, P1G_PROFILE, P1G_REVISION, sha256Hex, canonicalJson,
  type P1Observation, type P1Result } from "@rsgcsg/sts2-connector-client/p1-global";
import { LineProcess } from "./line-process.js";
import type { EpisodeRecorder } from "./recorder.js";

/**
 * Agent program port (sts2.p1/agent-port-1, NDJSON over stdio). The Agent sees only the
 * public P1 observation and its catalog; it answers with a choice_id. Any language works.
 *
 *   runner -> agent: hello | observation | result | episode_end
 *   agent  -> runner: ready | choose{occurrence, choice_id} | abstain{reason}
 */
export class AgentPortError extends Error {
  constructor(readonly code: string, message: string) { super(message); }
}

export interface AgentLaunch { readonly command: string; readonly args: readonly string[]; readonly replyTimeoutMs: number }

export class AgentPort {
  private process: LineProcess | null = null;
  identity: Record<string, unknown> | null = null;
  constructor(readonly launch: AgentLaunch, private readonly recorder: EpisodeRecorder) {}

  private out(message: Record<string, unknown>): void {
    this.recorder.append("agent_message_out", { message_type: message.type,
      body_sha256: sha256Hex(canonicalJson(message)) });
    this.process!.send(message);
  }

  private async in(timeoutMs: number): Promise<Record<string, unknown>> {
    let raw: string;
    try { raw = await this.process!.next(timeoutMs); }
    catch (error) { throw new AgentPortError("agent_no_reply", String((error as Error).message)); }
    this.recorder.appendRaw("agent_message_in", raw);
    try { return JSON.parse(raw) as Record<string, unknown>; }
    catch { throw new AgentPortError("agent_invalid_json", raw.slice(0, 200)); }
  }

  async start(episode: { episode_id: string; host_kind: string }): Promise<Record<string, unknown>> {
    this.process = new LineProcess(this.launch.command, this.launch.args, { stderr: "inherit" });
    this.out({ type: "hello", protocol: P1G_PORT_PROTOCOL, profile: P1G_PROFILE, revision: P1G_REVISION, ...episode });
    const ready = await this.in(this.launch.replyTimeoutMs);
    if (ready.type !== "ready" || ready.protocol !== P1G_PORT_PROTOCOL)
      throw new AgentPortError("agent_not_ready", JSON.stringify(ready).slice(0, 200));
    this.identity = { agent_id: ready.agent_id ?? null, agent_version: ready.agent_version ?? null };
    this.recorder.append("agent_hello", { identity: this.identity });
    return ready;
  }

  /** Context-only delivery (terminal or forced input): no reply expected. */
  inform(observation: P1Observation, forcedChoiceId: string | null): void {
    this.out({ type: "observation", observation, requires_choice: false, forced_choice_id: forcedChoiceId });
  }

  async choose(observation: P1Observation): Promise<string> {
    this.out({ type: "observation", observation, requires_choice: true, forced_choice_id: null });
    const reply = await this.in(this.launch.replyTimeoutMs);
    if (reply.type === "abstain") throw new AgentPortError("agent_abstained", String(reply.reason ?? ""));
    if (reply.type !== "choose" || reply.occurrence !== observation.envelope.occurrence)
      throw new AgentPortError("agent_reply_unbound", JSON.stringify(reply).slice(0, 200));
    const choiceId = String(reply.choice_id);
    if (!observation.catalog.some((choice) => choice.choice_id === choiceId))
      throw new AgentPortError("agent_choice_not_in_catalog", choiceId);
    return choiceId;
  }

  result(result: P1Result): void { this.out({ type: "result", result }); }

  async end(outcome: Record<string, unknown>): Promise<void> {
    if (!this.process) return;
    try { this.out({ type: "episode_end", ...outcome }); } catch { /* agent already gone */ }
    await this.process.terminate(3000);
    this.process = null;
  }
}
