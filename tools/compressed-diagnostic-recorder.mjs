/** The existing episode line schema, gzip members appended durably per line.
 * Decompression restores exact recorded Host raw strings and their hashes. */
import { openSync, writeSync, fsyncSync, closeSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { createHash } from 'node:crypto';
export class CompressedDiagnosticRecorder {
  constructor(path, header, { write = writeSync } = {}) {
    this.write = write; this.path = path; this.fd = openSync(path, 'wx'); this.seq = 0; this.hash = createHash('sha256');
    this.append('record_open', { schema: 'spireagent/recorded-difference-scan-log-1', ...header });
  }
  append(kind, body) {
    const bytes = gzipSync(JSON.stringify({ seq: ++this.seq, at: new Date().toISOString(), kind, ...body }) + '\n', { level: 1 });
    let written = 0;
    while (written < bytes.length) {
      const count = this.write(this.fd, bytes, written, bytes.length - written);
      if (!Number.isSafeInteger(count) || count <= 0) throw new Error('diagnostic_record_short_write');
      written += count;
    }
    fsyncSync(this.fd); this.hash.update(bytes); return this.seq;
  }
  appendRaw(kind, raw, extra = {}) {
    return this.append(kind, { raw, sha256: createHash('sha256').update(raw).digest('hex'), ...extra });
  }
  close(summary) {
    this.append('record_close', summary); fsyncSync(this.fd); closeSync(this.fd); this.fd = null;
    return { path: this.path, lines: this.seq, sha256: this.hash.digest('hex'), encoding: 'gzip-members-jsonl' };
  }
}
