import { createHash } from "node:crypto";
import type { PlayerEnvironmentBoundAction } from "@rsgcsg/sts2-connector-client";

/** Candidate identity is the UTF-8 SHA-256 of the exact JSON array, never sorted. */
export function candidateOrderDigest(actions: readonly (PlayerEnvironmentBoundAction | { action_id: string } | string)[]): string {
  const ids = actions.map((action) => typeof action === "string" ? action : "action_id" in action ? action.action_id : action.bound_action_id);
  return createHash("sha256").update(JSON.stringify(ids), "utf8").digest("hex");
}

export function candidateOrderIds(actions: readonly PlayerEnvironmentBoundAction[]): string[] {
  return actions.map((action) => action.bound_action_id);
}
