/** Shared transport mechanics for separately versioned retained public profiles.
 * Callers strictly decode their own envelope and chunks. No native reads/input. */
export interface RetainedByteCapture {
  capture_id: string; total_bytes: number; sha256: string; first_cursor: string;
}
export interface RetainedByteChunk {
  capture_id: string; sha256: string; total_bytes: number; offset: number;
  data_base64: string; next_cursor: string | null; end: boolean;
}

export async function publicBytesSha256(bytes: Uint8Array): Promise<string> {
  return Array.from(new Uint8Array(await globalThis.crypto.subtle.digest("SHA-256", bytes as Uint8Array<ArrayBuffer>)))
    .map(value => value.toString(16).padStart(2, "0")).join("");
}

export async function assembleRetainedBytes(
  capture: RetainedByteCapture,
  read: (input: { captureId: string; cursor: string; maxBytes: number }) => Promise<RetainedByteChunk>,
  maxBytes: number
): Promise<Uint8Array> {
  const bytes = new Uint8Array(capture.total_bytes);
  const visited = new Set<string>();
  let offset = 0;
  let next: string | null = capture.first_cursor;
  while (next !== null) {
    if (visited.has(next)) throw new Error("sealed observation cursor cycle");
    visited.add(next);
    const chunk = await read({ captureId: capture.capture_id, cursor: next, maxBytes });
    if (chunk.capture_id !== capture.capture_id || chunk.sha256 !== capture.sha256 ||
        chunk.total_bytes !== capture.total_bytes || chunk.offset !== offset)
      throw new Error("sealed observation identity or contiguous offset mismatch");
    const binary = atob(chunk.data_base64);
    const block = Uint8Array.from(binary, value => value.charCodeAt(0));
    if (btoa(binary) !== chunk.data_base64 || block.length !== Math.min(maxBytes, bytes.length - offset))
      throw new Error("sealed observation chunk size or base64 mismatch");
    bytes.set(block, offset);
    offset += block.length;
    if (chunk.end !== (offset === bytes.length)) throw new Error("sealed observation premature or missing terminal chunk");
    next = chunk.next_cursor;
  }
  if (offset !== bytes.length) throw new Error("sealed observation assembly is incomplete");
  if (await publicBytesSha256(bytes) !== capture.sha256) throw new Error("sealed observation digest mismatch");
  return bytes;
}
