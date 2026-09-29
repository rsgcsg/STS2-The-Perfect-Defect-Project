import { createHash } from "node:crypto";
import {
  chmodSync, constants, copyFileSync, lstatSync, mkdirSync, mkdtempSync,
  readFileSync, rmSync, writeFileSync
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { canonicalizeEpisodeSeed } from "./episode-provenance.mjs";
import { startManagedCandidateRuntime } from "./managed-candidate.mjs";
import { projectManagedCandidateDecision } from "./managed-player-environment.mjs";
import { readProjectIdentity } from "./project-identity.mjs";
import { canonicalDecisionDigest, canonicalizeSelectedAction } from "./semantic-decision.mjs";

const MAX_SAVE_BYTES = 8 * 1024 * 1024;
const PENDING_FLAGS = [
  "action_executor_running", "pending_host_operation", "pending_card_selection",
  "pending_card_reward", "pending_reward_set", "pending_bundle"
];

class ProbeFailure extends Error {}
function requireFact(condition, reason) {
  if (!condition) throw new ProbeFailure(reason);
}
function failureReason(error) {
  return error instanceof ProbeFailure ? error.message : "probe_operation_failed";
}
function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}
function saveIdentity(file) {
  const stat = lstatSync(file);
  requireFact(stat.isFile() && !stat.isSymbolicLink()
    && stat.size > 0 && stat.size <= MAX_SAVE_BYTES, "invalid_private_save_file");
  const bytes = readFileSync(file);
  requireFact(bytes.length === stat.size, "private_save_size_drift");
  return { bytes: bytes.length, sha256: sha256(bytes) };
}
function assertSave(file, expected) {
  const actual = saveIdentity(file);
  requireFact(actual.bytes === expected.bytes && actual.sha256 === expected.sha256,
    "private_save_digest_drift");
}
function exactIdentity(runtime) {
  const identity = {
    artifact_sha256: runtime.build?.artifact_sha256,
    artifact_mvid: runtime.build?.artifact_mvid,
    source_patch_sha256: runtime.build?.source_patch_sha256,
    runtime_sts2_sha256: runtime.build?.runtime_sts2_sha256
  };
  requireFact(Object.values(identity).every((value) => typeof value === "string" && value.length > 0),
    "candidate_identity_missing");
  return identity;
}
async function assertQuiescent(runtime, seed, timeoutMs) {
  const identity = await runtime.process.request({ cmd: "run_identity" }, timeoutMs);
  requireFact(identity?.type === "run_identity" && identity.active === true
    && identity.seed === seed && Number.isSafeInteger(identity.act) && identity.act > 0
    && Number.isSafeInteger(identity.floor) && identity.floor >= 0
    && PENDING_FLAGS.every((flag) => identity[flag] === false),
  "run_not_quiescent_or_seed_changed");
  return identity;
}
async function project(runtime, state, sequence, environmentFingerprint, timeoutMs) {
  requireFact(state?.type === "decision", "native_decision_missing");
  if (state.decision === "map_select") {
    const visibleMap = await runtime.process.request({ cmd: "get_map" }, timeoutMs);
    requireFact(visibleMap?.type === "map", "visible_map_unavailable");
    state = { ...state, visible_map: visibleMap };
  }
  const projected = projectManagedCandidateDecision({ state, sequence,
    runtimeInstanceId: runtime.adapterRuntimeInstanceId, environmentFingerprint });
  requireFact(projected.snapshot.completeness.status === "complete"
    && projected.snapshot.bound_actions.status === "complete", "public_projection_incomplete");
  return projected;
}
function mapAction(projected, expected = null) {
  const choices = [...projected.bindings.entries()]
    .filter(([, binding]) => binding.raw_request?.cmd === "action"
      && binding.raw_request.action === "select_map_node")
    .map(([id, binding]) => ({ binding,
      semantic: JSON.stringify(canonicalizeSelectedAction(projected.snapshot, id)) }))
    .sort((left, right) => left.semantic.localeCompare(right.semantic));
  requireFact(choices.length > 0, "map_action_missing");
  const selected = expected == null ? choices[0] : choices.find((choice) => choice.semantic === expected);
  requireFact(selected != null && selected.semantic !== "null", "restored_map_action_changed");
  requireFact(choices.filter((choice) => choice.semantic === selected.semantic).length === 1,
    "map_action_semantics_ambiguous");
  return selected;
}

/** Private native qualification only. No restore capability is added to the public driver. */
export async function runManagedSaveRoundtripProbe({
  root, candidateDirectory, diskIdentity, seed = "M2H0ST20260929A",
  requestTimeoutMs = 10_000, evidenceRoot = null
}, { startRuntime = startManagedCandidateRuntime } = {}) {
  seed = canonicalizeEpisodeSeed(seed);
  requireFact(seed != null, "seed_required");
  requireFact(Number.isSafeInteger(requestTimeoutMs) && requestTimeoutMs > 0
    && requestTimeoutMs <= 120_000, "invalid_request_timeout");
  const report = {
    schema: "sts2.headless/managed-map-save-roundtrip-probe-1",
    generated_at: new Date().toISOString(), status: "managed_map_save_roundtrip_incomplete",
    headless: readProjectIdentity(root), seed, character: "Defect", ascension: 0,
    comparison: "sts2.headless/canonical-player-decision-1",
    candidate: null, save: null, processes: [], gates: {}, failure: null, cleanup_failures: [],
    non_claims: [
      "Only a quiescent native MapRoom continue-save is tested; non-map saves are refused.",
      "After saving, only native MapRoom acknowledgement, run identity and a new map read are checked; native has no full-page observe opcode.",
      "Comparison covers the existing public Snapshot and finite action catalog, not Read payload equality, raw save equality across serialization or hidden state/RNG equivalence.",
      "One map action after each load is not arbitrary scene, pre-room, mid-combat, decision-point restore or full-run replay qualification.",
      "Native load and action responses are not a new public Receipt/Commit contract or model-visible save API.",
      "A unit fixture is not exact-game runtime evidence; this probe does not qualify shipped-host parity."
    ]
  };
  let directory = null;
  let stage = "private_workspace";
  try {
    directory = mkdtempSync(path.join(tmpdir(), "sts2-managed-map-save-"));
    chmodSync(directory, 0o700);
    const original = path.join(directory, "original.save");
    let selectedSemantic = null;
    let mapDigest = null;
    let successorDigest = null;
    const pids = new Set();
    const instances = new Set();
    for (let index = 0; index < 3; index += 1) {
      stage = `process_${index + 1}_start`;
      const runtime = await startRuntime({ root, candidateDirectory, diskIdentity,
        requestTimeoutMs, quietDiagnostics: true });
      const processReport = { role: index === 0 ? "seeded_reference" : `restore_${index}`,
        pid: runtime.process.pid, runtime_instance_id: runtime.adapterRuntimeInstanceId,
        map_digest: null, successor_digest: null, exit: null };
      report.processes.push(processReport);
      let primaryError = null;
      try {
        requireFact(Number.isSafeInteger(runtime.process.pid) && runtime.process.pid > 0
          && typeof runtime.adapterRuntimeInstanceId === "string" && runtime.adapterRuntimeInstanceId.length > 0
          && !pids.has(runtime.process.pid) && !instances.has(runtime.adapterRuntimeInstanceId),
        "runtime_instance_not_fresh");
        pids.add(runtime.process.pid);
        instances.add(runtime.adapterRuntimeInstanceId);
        const identity = exactIdentity(runtime);
        if (report.candidate == null) report.candidate = identity;
        requireFact(JSON.stringify(identity) === JSON.stringify(report.candidate), "candidate_identity_drift");
        const fingerprint = sha256(JSON.stringify(identity));
        stage = `process_${index + 1}_mount`;
        let state;
        let restoreFile = null;
        if (index === 0) {
          state = await runtime.process.request({ cmd: "start_run", character: "Defect",
            ascension: 0, seed, lang: "en" }, requestTimeoutMs);
        } else {
          assertSave(original, report.save);
          restoreFile = path.join(directory, `restore-${index}.save`);
          copyFileSync(original, restoreFile, constants.COPYFILE_EXCL);
          state = await runtime.process.request({ cmd: "load_save", path: restoreFile, lang: "en" }, requestTimeoutMs);
          assertSave(restoreFile, report.save);
          assertSave(original, report.save);
        }
        requireFact(state?.type === "decision" && state.decision === "map_select", "native_boundary_not_map");
        requireFact(state.player?.character_id === "DEFECT" && state.context?.ascension === 0,
          "run_character_or_ascension_changed");
        const mounted = await assertQuiescent(runtime, seed, requestTimeoutMs);
        const before = await project(runtime, state, 1, fingerprint, requestTimeoutMs);
        processReport.map_digest = canonicalDecisionDigest(before.snapshot);
        if (mapDigest == null) mapDigest = processReport.map_digest;
        requireFact(processReport.map_digest === mapDigest, "restored_public_map_changed");
        if (index === 0) {
          stage = "write_native_map_save";
          const saved = await runtime.process.request({ cmd: "write_continue_save", path: original }, requestTimeoutMs);
          requireFact(saved?.type === "save_result" && saved.success === true
            && saved.room_type === "MapRoom" && saved.path === original, "native_map_save_not_confirmed");
          report.save = saveIdentity(original);
          chmodSync(original, 0o600);
          // Native has no full observe opcode. Re-read its map and run coordinates;
          // fresh-load comparisons below separately cover the entire public page.
          const afterSave = await project(runtime, state, 2, fingerprint, requestTimeoutMs);
          requireFact(canonicalDecisionDigest(afterSave.snapshot) === mapDigest,
            "map_changed_during_save");
          // size in the native response counts characters; the digest and bound here use bytes.
          report.gates.map_continue_save = "pass";
          report.gates.post_save_native_map_read = "pass";
        }
        const ready = await assertQuiescent(runtime, seed, requestTimeoutMs);
        requireFact(ready.act === mounted.act && ready.floor === mounted.floor,
          "run_position_changed_before_map_action");
        const selected = mapAction(before, selectedSemantic);
        selectedSemantic ??= selected.semantic;
        stage = `process_${index + 1}_single_map_action`;
        const successorState = await runtime.process.request(selected.binding.raw_request, requestTimeoutMs);
        await assertQuiescent(runtime, seed, requestTimeoutMs);
        const successor = await project(runtime, successorState, 2, fingerprint, requestTimeoutMs);
        processReport.successor_digest = canonicalDecisionDigest(successor.snapshot);
        if (successorDigest == null) successorDigest = processReport.successor_digest;
        requireFact(processReport.successor_digest === successorDigest, "restored_public_successor_changed");
        assertSave(original, report.save);
        if (restoreFile != null) assertSave(restoreFile, report.save);
      } catch (error) {
        primaryError = error;
        throw error;
      } finally {
        try {
          processReport.exit = await runtime.process.stop({ request: { cmd: "quit" },
            timeoutMs: Math.min(requestTimeoutMs, 5_000) });
          requireFact(processReport.exit?.code === 0, "process_exit_not_clean");
        } catch (error) {
          report.cleanup_failures.push({ stage: `process_${index + 1}_stop`, reason: failureReason(error) });
          if (primaryError == null) {
            stage = `process_${index + 1}_stop`;
            throw error;
          }
        }
      }
    }
    report.gates.distinct_exact_runtimes = "pass";
    report.gates.two_restored_public_maps = "pass";
    report.gates.two_restored_single_map_successors = "pass";
    report.gates.private_save_digest_unchanged = "pass";
    report.gates.clean_process_exits = "pass";
    report.selected_action_digest = sha256(selectedSemantic);
    report.status = "managed_map_save_roundtrip_pass";
  } catch (error) {
    // Do not copy native errors, save contents or local paths into a shareable report.
    report.failure = { stage, reason: failureReason(error) };
  } finally {
    if (directory != null) {
      try {
        rmSync(directory, { recursive: true, force: true });
        report.gates.private_workspace_removed = "pass";
      } catch {
        report.status = "managed_map_save_roundtrip_incomplete";
        const failure = { stage: "cleanup", reason: "private_workspace_cleanup_failed" };
        report.cleanup_failures.push(failure);
        report.failure ??= failure;
      }
    }
  }
  let reportFile = null;
  if (evidenceRoot != null) {
    mkdirSync(evidenceRoot, { recursive: true });
    const output = mkdtempSync(path.join(evidenceRoot, "managed-map-save-roundtrip-"));
    reportFile = path.join(output, "report.json");
    writeFileSync(reportFile, `${JSON.stringify(report, null, 2)}\n`, { mode: 0o600 });
  }
  return { report, reportFile };
}
