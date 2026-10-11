import type { ChildProcessWithoutNullStreams } from "node:child_process";
import type { NdjsonAgentSessionPort } from "../src/agent-session-port.js";

/** Observe at spawn time: kill() requests termination; close proves stdio and
 * the actual fixture child have finished before their temporary files go away. */
export function fixturePortCloser(port: NdjsonAgentSessionPort): () => Promise<void> {
  const child = (port as unknown as { child: ChildProcessWithoutNullStreams }).child;
  const closed = new Promise<void>(resolve => child.once("close", () => resolve()));
  return async () => { port.close(); await closed; };
}
