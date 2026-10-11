import type { PlayerEnvironmentRestClient, NativeLogicalSession, EnvironmentControllerSession, NativeLogicalCapabilities, JsonObject } from "@rsgcsg/sts2-connector-client";
export interface RealStoreBridge {
  rest: PlayerEnvironmentRestClient;
  session: NativeLogicalSession;
  controller: EnvironmentControllerSession;
  capabilities: NativeLogicalCapabilities;
  requests: { operation: string; body: JsonObject }[];
  call(operation: string, body?: JsonObject): Promise<unknown>;
  stats(): Promise<{ charged_bytes: number; charged_buffers: number; handles: number; live_captures: number; now: number }>;
  diagnostics(): { backend_pid: number | null; events: { phase: string; operation: string | null; elapsed_ms: number }[]; pending_operations: string[] };
  afterReply(callback: ((operation: string, value: unknown) => void | Promise<void>) | undefined): void;
  close(): Promise<void>;
}
export function realStoreBridge(options?: { fallback?: (url: URL, body: JsonObject) => unknown }): Promise<RealStoreBridge>;
