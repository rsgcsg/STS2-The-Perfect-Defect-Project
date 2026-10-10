import type { ChildProcess } from "node:child_process";
import type { JsonObject } from "@rsgcsg/sts2-connector-client";
export interface RealStoreTransport {
  endpoint: string;
  requests: { operation: string; body: JsonObject }[];
  call(operation: string, body?: JsonObject): Promise<unknown>;
  stats(): Promise<{ charged_bytes: number; charged_buffers: number; handles: number; live_captures: number; now: number }>;
  diagnostics(): { backend_pid: number | null; events: { phase: string; operation: string | null; elapsed_ms: number }[]; pending_operations: string[] };
  afterReply(callback: ((operation: string, value: unknown) => void | Promise<void>) | undefined): void;
  beforeRequest(callback: ((operation: string, body: JsonObject, url: URL) => void | Promise<void>) | undefined): void;
  close(): Promise<{ code: number | null; signal: NodeJS.Signals | null }>;
}
export function openRealStoreTransport(options?: {
  fallback?: (url: URL, body: JsonObject) => unknown | Promise<unknown>;
  backendFactory?: () => ChildProcess;
  readinessTimeoutMs?: number;
  port?: number;
}): Promise<RealStoreTransport>;
