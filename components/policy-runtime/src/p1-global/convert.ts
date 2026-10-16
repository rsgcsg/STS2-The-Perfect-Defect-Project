import {
  applyCommitment, canonicalJson, createSession, reduce, sha256Hex,
  type HostKind, type P1Observation, type P1SessionState, type ReadCapture
} from "@rsgcsg/sts2-connector-client/p1-global";
import { readRecord, type RecordLine } from "./recorder.js";

/**
 * Offline conversion: rebuild every P1 observation from the record's exact Host bytes
 * with the same shared reducer, and check it equals what the Agent was offered.
 * Read-only: no Host, no Agent, no live dispatch authority.
 */
export interface ConversionReport {
  schema: "sts2.p1/conversion-report-1";
  record: string; record_sha256: string; episode_id: string; host_kind: HostKind;
  observations: number; identical: number; mismatches: { occurrence: number; seq: number; recorded: string; rebuilt: string }[];
  results_applied: number; errors: string[]; verdict: "identical" | "mismatch" | "error";
  converted: P1Observation[];
}

function snapshotFrom(lines: Map<number, RecordLine>, seq: number, path: string): unknown {
  const line = lines.get(seq);
  if (!line || line.kind !== "env_response") throw new Error(`basis_ref_not_env_response:${seq}`);
  const parsed = JSON.parse(String(line.raw)) as Record<string, any>;
  return path === "receipt.successor" ? parsed.receipt?.successor : parsed.snapshot;
}

export function convertRecord(path: string, recordBytesSha: string): ConversionReport {
  const records = readRecord(path);
  const bySeq = new Map(records.map((line) => [line.seq, line]));
  const open = records[0]!;
  const report: ConversionReport = { schema: "sts2.p1/conversion-report-1", record: path, record_sha256: recordBytesSha,
    episode_id: String(open.episode_id), host_kind: open.host_kind as HostKind, observations: 0, identical: 0,
    mismatches: [], results_applied: 0, errors: [], verdict: "identical", converted: [] };
  let state: P1SessionState = createSession(report.episode_id, report.host_kind);
  let lastLowering: ReturnType<typeof reduce>["lowering"] | null = null;
  let lastChoice: string | null = null;
  try {
    for (const line of records) {
      if (line.kind === "p1_observation") {
        const refs = line.basis_refs as { snapshot_seq: number; snapshot_path: string; read_seqs: { read_id: string; seq: number | null }[] };
        const snapshot = snapshotFrom(bySeq, refs.snapshot_seq, refs.snapshot_path) as Record<string, any>;
        const reads: ReadCapture[] = refs.read_seqs.map((item) => {
          const listed = (snapshot.reads as { read_id: string; kind: string }[]).find((read) => read.read_id === item.read_id);
          if (item.seq === null) return { read_id: item.read_id, kind: listed?.kind ?? "", response: null, error: "recorded_read_failure" };
          const raw = JSON.parse(String(bySeq.get(item.seq)!.raw)) as Record<string, any>;
          return { read_id: item.read_id, kind: listed?.kind ?? "", response: raw.read, error: null };
        });
        const reduction = reduce(state, { snapshot, reads });
        state = reduction.state; lastLowering = reduction.lowering;
        const rebuilt = canonicalJson(reduction.observation);
        const rebuiltSha = sha256Hex(rebuilt);
        const recordedSha = sha256Hex(canonicalJson(line.observation));
        report.observations += 1;
        if (rebuiltSha === line.sha256 && recordedSha === line.sha256) report.identical += 1;
        else report.mismatches.push({ occurrence: Number(line.occurrence), seq: line.seq, recorded: String(line.sha256), rebuilt: rebuiltSha });
        report.converted.push(reduction.observation);
      } else if (line.kind === "p1_choice") {
        lastChoice = String(line.choice_id);
      } else if (line.kind === "p1_result") {
        const result = line.result as { status: string; choice_id: string };
        if (result.status === "completed" && lastLowering && lastChoice === result.choice_id) {
          const lowering = lastLowering[result.choice_id];
          if (lowering) { state = applyCommitment(state, lowering, true); report.results_applied += 1; }
        }
      }
    }
  } catch (error) { report.errors.push(String((error as Error).message)); }
  report.verdict = report.errors.length > 0 ? "error" : report.mismatches.length > 0 ? "mismatch" : "identical";
  return report;
}
