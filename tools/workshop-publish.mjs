import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { spawn } from "node:child_process";
import { parseArgs } from "node:util";
import { fileURLToPath } from "node:url";
import { regularFile, safePath, sha256 } from "./workshop-stage.mjs";
import { verifyPrepared } from "./workshop-publication-candidate.mjs";
import { verifyUploader, git } from "./workshop-uploader.mjs";

const PRE_MUTATION_RESULT = "PUBLICATION_FAILED_BEFORE_REMOTE_MUTATION";
const PRE_MUTATION_FILE = "pre-mutation-reconciliation.json";
const INIT_FAILURE = "Steam initialization failed! Result: k_ESteamAPIInitResult_FailedGeneric, message: Could not determine Steam client install directory.";
const UNKNOWN_REASON = "uploader_nonzero_exit\n\n1 !== 0\n";
const SUCCESS_RECONCILIATION_FILE = "successful-publication-reconciliation.json";
const LEGACY_WRAPPER = "5a26e77014e264f9d946026e73b8abb4b00524ed";
const LEGACY_WRAPPER_SHA = "f874f74871045674896f3d4f323db79d9fcdbf7228fa04e8212e0af60d11231f";

// These two format strings are present in the pinned steam_api64.dll. This is
// diagnostic classification only, never proof of upload or account authority.
export function classifyUploaderStderr(bytes, tool) {
  const text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
  if (text === "") return "EMPTY";
  assert.equal(tool.repository, "megacrit/sts2-mod-uploader");
  assert.equal(tool.commit, "d7b7e6b16c413d5a124f474f9e5104ef01f76ab1");
  const native = tool.inventory.filter((file) => file.name === "steam_api64.dll");
  assert.equal(native.length, 1, "unqualified_stderr_runtime");
  assert.equal(native[0].sha256, "eb17909a76668cf9ae0b92a618a34a50f6c73d3a6787cb4dd8ce36a8b10bfb75");
  assert.equal(native[0].size_bytes, 317080);
  const match = /^Setting breakpad minidump AppID = 2868840\r\nSteamInternal_SetMinidumpSteamID:  Caching Steam ID:  ([1-9][0-9]{16}) \[API loaded no\]\r\n$/u.exec(text);
  assert.ok(match && match[0] === text, "uploader_stderr_requires_review");
  itemId(match[1]);
  return "PINNED_STEAM_BREAKPAD_MINIDUMP_DIAGNOSTIC_1";
}

export function durableJson(file, value) {
  safePath(file);
  const fd = fs.openSync(file, "wx");
  try { fs.writeFileSync(fd, `${JSON.stringify(value, null, 2)}\n`); fs.fsyncSync(fd); }
  finally { fs.closeSync(fd); }
}
export function itemId(value) {
  assert.match(value ?? "", /^[1-9][0-9]{0,19}$/u, "valid_explicit_item_id_required");
  assert.ok(BigInt(value) <= 18446744073709551615n, "item_id_overflow");
  return value;
}
function historicalPreMutationProof(folder, workspace, tool, uploaderReceiptSha256, requireNoModId = false) {
  const attemptBytes = regularFile(path.join(folder, "attempt.json"));
  const unknownBytes = regularFile(path.join(folder, "unknown.json"));
  const attempt = JSON.parse(attemptBytes), unknown = JSON.parse(unknownBytes);
  assert.ok(attemptBytes.equals(Buffer.from(`${JSON.stringify(attempt, null, 2)}\n`)), "historical_attempt_bytes_drift");
  assert.ok(unknownBytes.equals(Buffer.from(`${JSON.stringify(unknown, null, 2)}\n`)), "historical_unknown_bytes_drift");
  const ready = attempt.candidate;
  assert.deepEqual(Object.keys(attempt).sort(), ["args", "attempt_id", "candidate", "readiness_sha256", "result", "schema"].sort(), "historical_attempt_shape_drift");
  assert.equal(attempt.schema, "spireagent/workshop-publication-attempt-1");
  assert.equal(attempt.result, "ATTEMPT_STARTED_OUTCOME_UNCONFIRMED");
  assert.equal(attempt.attempt_id, path.basename(folder));
  assert.deepEqual(attempt.args, ["upload", "-w", workspace], "historical_create_args_required");
  assert.equal(ready.schema, "spireagent/workshop-publication-ready-1");
  assert.equal(ready.result, "READY_TO_PUBLISH_PRIVATE");
  assert.equal(ready.operation, "create", "only_pinned_create_init_failure_reconcilable");
  assert.equal(ready.explicit_item_id, null);
  assert.equal(ready.requested_visibility, "private");
  assert.equal(ready.uploader_receipt_sha256, uploaderReceiptSha256, "historical_uploader_receipt_drift");
  assert.deepEqual(ready.uploader, tool, "historical_uploader_identity_drift");
  const readyBytes = Buffer.from(`${JSON.stringify(ready, null, 2)}\n`);
  assert.equal(sha256(readyBytes), attempt.readiness_sha256, "historical_readiness_drift");
  assert.ok(regularFile(path.join(workspace, ".publication", `ready-${attempt.readiness_sha256}.json`)).equals(readyBytes), "historical_ready_file_drift");
  assert.deepEqual(unknown, { schema: "spireagent/workshop-publication-unknown-1",
    result: "PUBLICATION_OUTCOME_UNKNOWN", reason: UNKNOWN_REASON, retry_allowed: false }, "historical_nonzero_disposition_required");
  assert.ok(!fs.existsSync(path.join(folder, "publication-receipt.json")), "historical_success_conflict");
  if (requireNoModId) assert.ok(!fs.existsSync(path.join(workspace, "mod_id.txt")), "historical_item_id_conflict");
  assert.equal(regularFile(path.join(folder, "steam_appid.txt")).toString("utf8"), "2868840", "historical_app_id_drift");
  const decode = (name) => new TextDecoder("utf-8", { fatal: true }).decode(regularFile(path.join(folder, name))).replace(/\r\n/gu, "\n");
  assert.equal(decode("stdout.log"), `Initializing Steam\n\u001b[31m${INIT_FAILURE}\u001b[0m\n`, "init_failure_stdout_not_exact");
  assert.equal(decode("mod-uploader.log"), `Initializing Steam\n${INIT_FAILURE}\n`, "init_failure_log_not_exact");
  assert.equal(decode("stderr.log"), "", "init_failure_stderr_not_empty");
  const names = fs.readdirSync(folder).sort();
  const allowed = ["attempt.json", "mod-uploader.log", "stderr.log", "stdout.log", "steam_appid.txt", "unknown.json"];
  assert.deepEqual(names.filter((name) => name !== PRE_MUTATION_FILE), allowed.sort(), "historical_attempt_files_drift");
  return { attempt, hashes: Object.fromEntries(allowed.map((name) => [name, sha256(regularFile(path.join(folder, name)))])) };
}
function verifyPreMutationReconciliation(folder, workspace, tool, uploaderReceiptSha256, currentPrepared) {
  const proof = historicalPreMutationProof(folder, workspace, tool, uploaderReceiptSha256);
  assert.equal(currentPrepared.prepared_receipt_sha256, proof.attempt.candidate.prepared.prepared_receipt_sha256, "historical_prepared_receipt_drift");
  assert.deepEqual(currentPrepared.receipt, proof.attempt.candidate.prepared.receipt, "historical_prepared_candidate_drift");
  assert.deepEqual(currentPrepared.payload, proof.attempt.candidate.prepared.payload, "historical_payload_drift");
  const reconciliation = JSON.parse(regularFile(path.join(folder, PRE_MUTATION_FILE)));
  assert.deepEqual(reconciliation, {
    schema: "spireagent/workshop-pre-mutation-reconciliation-1", result: PRE_MUTATION_RESULT,
    evidence_level: "pinned_uploader_local_control_flow_before_CreateItem_not_steam_readback",
    attempt_id: proof.attempt.attempt_id, readiness_sha256: proof.attempt.readiness_sha256,
    uploader_repository: tool.repository, uploader_commit: tool.commit,
    uploader_receipt_sha256: uploaderReceiptSha256, evidence_sha256: proof.hashes,
    steam_item_id: null, automatic_retry_allowed: false, new_human_authorization_required: true
  }, "pre_mutation_reconciliation_drift");
  return reconciliation;
}
function publicationState(workspace, tool, uploaderReceiptSha256, currentPrepared) {
  const directory = safePath(path.join(workspace, ".publication"));
  fs.mkdirSync(directory, { recursive: true });
  const attempts = path.join(directory, "attempts");
  fs.mkdirSync(attempts, { recursive: true });
  let accepted;
  for (const name of fs.readdirSync(attempts).sort()) {
    assert.match(name, /^[0-9]+-[a-f0-9-]+$/u, "unknown_attempt_entry");
    const folder = safePath(path.join(attempts, name));
    const attempt = JSON.parse(regularFile(path.join(folder, "attempt.json")));
    assert.equal(attempt.schema, "spireagent/workshop-publication-attempt-1");
    assert.equal(attempt.attempt_id, name);
    const success = path.join(folder, "publication-receipt.json");
    if (fs.existsSync(path.join(folder, "unknown.json"))) {
      assert.ok(!fs.existsSync(success), "PUBLICATION_OUTCOME_UNKNOWN: conflicting outcomes");
      if (fs.existsSync(path.join(folder, SUCCESS_RECONCILIATION_FILE))) {
        assert.ok(!fs.existsSync(path.join(folder, PRE_MUTATION_FILE)), "conflicting_reconciliations");
        const r = JSON.parse(regularFile(path.join(folder, SUCCESS_RECONCILIATION_FILE)));
        const verified = successReconciliationProof(folder, workspace, tool, uploaderReceiptSha256, currentPrepared, r.human_observation);
        assert.deepEqual(r, verified, "success_reconciliation_drift");
        if (accepted) assert.equal(accepted, r.steam.item_id, "conflicting_accepted_item_identity");
        accepted = r.steam.item_id;
        continue;
      }
      assert.ok(fs.existsSync(path.join(folder, PRE_MUTATION_FILE)), "PUBLICATION_OUTCOME_UNKNOWN: Human must reconcile Steam/item/logs; no retry");
      try { verifyPreMutationReconciliation(folder, workspace, tool, uploaderReceiptSha256, currentPrepared); }
      catch (error) { throw new Error(`PUBLICATION_OUTCOME_UNKNOWN: invalid pre-mutation proof: ${error.message}`); }
      continue;
    }
    assert.ok(fs.existsSync(success), "PUBLICATION_OUTCOME_UNKNOWN: incomplete attempt; no retry");
    const receipt = JSON.parse(regularFile(success));
    assert.equal(receipt.result, "PUBLICATION_CONFIRMED_BY_UPLOADER", "PUBLICATION_OUTCOME_UNKNOWN");
    assert.equal(receipt.schema, "spireagent/workshop-publication-1");
    assert.deepEqual(receipt.candidate, attempt.candidate);
    assert.equal(receipt.attempt_id, name);
    assert.equal(sha256(Buffer.from(`${JSON.stringify(attempt.candidate, null, 2)}\n`)), attempt.readiness_sha256, "accepted_attempt_identity_drift");
    assert.equal(sha256(regularFile(path.join(folder, "mod-uploader.log"))), receipt.log_sha256, "accepted_log_drift");
    const id = itemId(receipt.steam.item_id);
    assert.equal(receipt.steam.operation, attempt.candidate.operation);
    if (accepted) assert.equal(id, accepted, "conflicting_accepted_item_identity");
    accepted = id;
  }
  return { directory, attempts, accepted };
}
function intent(workspace, mode, id, tool, uploaderReceiptSha256, currentPrepared) {
  assert.ok(["create", "update"].includes(mode), "explicit_create_or_update_required");
  const state = publicationState(workspace, tool, uploaderReceiptSha256, currentPrepared);
  const modFile = path.join(workspace, "mod_id.txt");
  const modId = fs.existsSync(modFile) ? itemId(regularFile(modFile).toString("utf8").trim()) : undefined;
  if (mode === "create") {
    assert.equal(id, undefined, "create_must_not_have_item_id");
    assert.equal(state.accepted, undefined, "create_existing_accepted_item");
    assert.equal(modId, undefined, "create_ambiguous_mod_id");
  } else {
    itemId(id);
    assert.equal(state.accepted, id, "update_requires_matching_accepted_state");
    if (modId !== undefined) assert.equal(modId, id, "explicit_mod_id_conflict");
  }
  return state;
}
// Tests can inject readers/runner, never via CLI flags or environment switches.
export function publicationPreflight(options, { prepared = verifyPrepared, uploader = verifyUploader } = {}) {
  const candidate = prepared(options);
  const tool = uploader(options.uploaderReceipt, options.uploaderReceiptSha256);
  const expectedRid = `${{ win32: "win", linux: "linux", darwin: "osx" }[process.platform]}-${process.arch}`;
  assert.equal(tool.rid, expectedRid, "uploader_not_for_current_host");
  intent(candidate.workspace, options.mode, options.itemId, tool, options.uploaderReceiptSha256, candidate);
  return { schema: "spireagent/workshop-publication-ready-1", result: "READY_TO_PUBLISH_PRIVATE",
    evidence_level: "local_preflight_only_no_steam_contact", operation: options.mode,
    explicit_item_id: options.itemId ?? null, requested_visibility: "private", prepared: candidate,
    uploader_receipt_sha256: options.uploaderReceiptSha256, uploader: tool };
}
// A separate, local-only operator action. This never launches the uploader or
// changes the historical UNKNOWN; all proof is rechecked on every later preflight.
export function reconcilePreMutationFailure(options, { prepared = verifyPrepared, uploader = verifyUploader } = {}) {
  const workspace = safePath(path.join(options.preparedRoot, "workshop"));
  const directory = safePath(path.join(workspace, ".publication"));
  const lockPath = path.join(directory, "publication.lock"), lock = fs.openSync(lockPath, "wx");
  try {
    const current = prepared(options);
    assert.equal(current.workspace, workspace, "prepared_workspace_drift");
    const tool = uploader(options.uploaderReceipt, options.uploaderReceiptSha256);
    assert.match(options.attemptId ?? "", /^[0-9]+-[a-f0-9-]+$/u, "exact_attempt_id_required");
    const folder = safePath(path.join(directory, "attempts", options.attemptId));
    assert.ok(fs.statSync(folder).isDirectory(), "attempt_directory_required");
    assert.ok(!fs.existsSync(path.join(folder, PRE_MUTATION_FILE)), "reconciliation_already_exists");
    const proof = historicalPreMutationProof(folder, workspace, tool, options.uploaderReceiptSha256, true);
    assert.equal(current.prepared_receipt_sha256, proof.attempt.candidate.prepared.prepared_receipt_sha256, "historical_prepared_receipt_drift");
    assert.deepEqual(current.receipt, proof.attempt.candidate.prepared.receipt, "historical_prepared_candidate_drift");
    assert.deepEqual(current.payload, proof.attempt.candidate.prepared.payload, "historical_payload_drift");
    const reconciliation = {
      schema: "spireagent/workshop-pre-mutation-reconciliation-1", result: PRE_MUTATION_RESULT,
      evidence_level: "pinned_uploader_local_control_flow_before_CreateItem_not_steam_readback",
      attempt_id: options.attemptId, readiness_sha256: proof.attempt.readiness_sha256,
      uploader_repository: tool.repository, uploader_commit: tool.commit,
      uploader_receipt_sha256: options.uploaderReceiptSha256, evidence_sha256: proof.hashes,
      steam_item_id: null, automatic_retry_allowed: false, new_human_authorization_required: true
    };
    durableJson(path.join(folder, PRE_MUTATION_FILE), reconciliation);
    return { ...reconciliation, evidence_path: path.join(folder, PRE_MUTATION_FILE),
      evidence_file_sha256: sha256(regularFile(path.join(folder, PRE_MUTATION_FILE))) };
  } finally { fs.closeSync(lock); fs.unlinkSync(lockPath); }
}
export function runUploader({ executable, args, cwd, timeoutMs = 120_000 }) {
  return new Promise((resolve) => {
    const out = fs.openSync(path.join(cwd, "stdout.log"), "wx"), err = fs.openSync(path.join(cwd, "stderr.log"), "wx");
    let child, timer, finished = false;
    const done = (value) => {
      if (finished) return; finished = true; clearTimeout(timer);
      for (const signal of ["SIGINT", "SIGTERM"]) process.removeListener(signal, interrupt);
      fs.fsyncSync(out); fs.fsyncSync(err); fs.closeSync(out); fs.closeSync(err); resolve(value);
    };
    const interrupt = () => { child?.kill(); done({ error: "interrupted" }); };
    for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, interrupt);
    try {
      child = spawn(executable, args, { cwd, stdio: ["ignore", out, err], windowsHide: true, shell: false });
      child.once("error", (error) => done({ error: error.message }));
      child.once("close", (code, signal) => done({ code, signal }));
      timer = setTimeout(() => { child.kill(); done({ error: "timeout" }); }, timeoutMs);
    } catch (error) { done({ error: error.message }); }
  });
}
function confirmUploader(attemptDirectory, workspace, ready, result) {
  assert.equal(result.error, undefined, "uploader_interrupted_or_failed");
  assert.equal(result.signal ?? null, null, "uploader_signal");
  assert.equal(result.code, 0, "uploader_nonzero_exit");
  const decode = (file) => new TextDecoder("utf-8", { fatal: true }).decode(regularFile(file));
  const stdout = decode(path.join(attemptDirectory, "stdout.log"));
  classifyUploaderStderr(regularFile(path.join(attemptDirectory, "stderr.log")), ready.uploader);
  const log = decode(path.join(attemptDirectory, "mod-uploader.log"));
  // Exact upstream Warn/Error uses these ANSI colors; even successful SubmitItemUpdate
  // may follow a rejected visibility/content setter. Do not promote that to success.
  assert.ok(!/\u001b\[(?:31|33)m/u.test(stdout), "uploader_warning_requires_human_review");
  const id = itemId(regularFile(path.join(workspace, "mod_id.txt")).toString("utf8").trim());
  if (ready.operation === "update") assert.equal(id, ready.explicit_item_id);
  const title = JSON.parse(regularFile(path.join(workspace, "workshop.json"))).title;
  const expected = `Successfully uploaded '${title}' to the workshop with id ${id}! Browsing to the item in Steam.`;
  for (const text of [stdout, log]) assert.deepEqual(text.split(/\r?\n/u).filter((line) => line.startsWith("Successfully uploaded '")), [expected], "missing_unambiguous_uploader_confirmation");
  return id;
}

function successReconciliationProof(folder, workspace, tool, uploaderSha, current, human) {
  const names = ["attempt.json", "unknown.json", "stdout.log", "stderr.log", "mod-uploader.log", "steam_appid.txt"];
  assert.deepEqual(fs.readdirSync(folder).filter((n) => n !== SUCCESS_RECONCILIATION_FILE).sort(), [...names].sort(), "historical_success_files_drift");
  const hashes = Object.fromEntries(names.map((n) => [n, sha256(regularFile(path.join(folder, n)))]));
  const attempt = JSON.parse(regularFile(path.join(folder, "attempt.json")));
  assert.deepEqual(Object.keys(attempt).sort(), ["args", "attempt_id", "candidate", "readiness_sha256", "result", "schema"].sort());
  assert.equal(attempt.schema, "spireagent/workshop-publication-attempt-1");
  assert.equal(attempt.result, "ATTEMPT_STARTED_OUTCOME_UNCONFIRMED");
  assert.equal(attempt.attempt_id, path.basename(folder));
  const ready = attempt.candidate;
  assert.equal(ready.schema, "spireagent/workshop-publication-ready-1");
  assert.equal(ready.result, "READY_TO_PUBLISH_PRIVATE");
  assert.equal(ready.operation, "create");
  assert.equal(ready.explicit_item_id, null);
  assert.equal(ready.requested_visibility, "private");
  assert.deepEqual(attempt.args, ["upload", "-w", workspace]);
  assert.equal(ready.prepared.workspace, workspace);
  assert.equal(ready.prepared.publication_workspace_revision, LEGACY_WRAPPER, "unsupported_historical_classifier");
  assert.equal(ready.uploader_receipt_sha256, uploaderSha);
  assert.deepEqual(ready.uploader, tool, "historical_uploader_identity_drift");
  assert.equal(current.prepared_receipt_sha256, ready.prepared.prepared_receipt_sha256);
  assert.deepEqual(current.receipt, ready.prepared.receipt);
  assert.deepEqual(current.payload, ready.prepared.payload);
  const readyBytes = Buffer.from(`${JSON.stringify(ready, null, 2)}\n`);
  assert.equal(sha256(readyBytes), attempt.readiness_sha256);
  assert.ok(regularFile(path.join(workspace, ".publication", `ready-${attempt.readiness_sha256}.json`)).equals(readyBytes));
  assert.equal(regularFile(path.join(folder, "steam_appid.txt")).toString("utf8"), "2868840");
  const stderrBytes = regularFile(path.join(folder, "stderr.log"));
  const classification = classifyUploaderStderr(stderrBytes, tool);
  assert.equal(classification, "PINNED_STEAM_BREAKPAD_MINIDUMP_DIAGNOSTIC_1");
  // The audited legacy wrapper checks no error, no signal and code===0 BEFORE
  // asserting empty stderr. Reproduce that exact assertion, not an operator-
  // supplied exit code. A different old failure cannot acquire this proof.
  let reason;
  try { assert.equal(new TextDecoder("utf-8", { fatal: true }).decode(stderrBytes).trim(), "", "uploader_stderr_requires_review"); }
  catch (error) { reason = error.message; }
  assert.deepEqual(JSON.parse(regularFile(path.join(folder, "unknown.json"))), {
    schema: "spireagent/workshop-publication-unknown-1", result: "PUBLICATION_OUTCOME_UNKNOWN", reason, retry_allowed: false
  }, "historical_exit_zero_proof_missing");
  const id = confirmUploader(folder, workspace, ready, { code: 0 });
  assert.deepEqual(human, { evidence_level: "human_reported_steam_page_not_api_readback", item_id: id,
    visibility: "private", title: JSON.parse(regularFile(path.join(workspace, "workshop.json"))).title,
    preview_matches_candidate: true }, "human_observation_does_not_match_local_proof");
  return { schema: "spireagent/workshop-success-reconciliation-1", result: "PUBLICATION_RECONCILED_AS_UPLOADER_SUCCESS",
    evidence_level: "pinned_uploader_local_success_plus_separate_human_observation_not_api_readback",
    attempt_id: attempt.attempt_id, readiness_sha256: attempt.readiness_sha256, candidate: ready,
    evidence_sha256: hashes, evidence_inventory_sha256: sha256(Buffer.from(JSON.stringify(hashes))),
    uploader_exit_code: 0, exit_code_proof: { kind: "legacy_assertion_control_flow", revision: LEGACY_WRAPPER, source_sha256: LEGACY_WRAPPER_SHA },
    stderr_classification: classification, steam: { item_id: id, operation: "create", requested_visibility: "private" },
    human_observation: human, automatic_retry_allowed: false, new_human_authorization_required: true };
}

// Local-only; never invokes the uploader or rewrites the original UNKNOWN.
export function reconcileUploaderSuccess(options, { prepared = verifyPrepared, uploader = verifyUploader } = {}) {
  const workspace = safePath(path.join(options.preparedRoot, "workshop"));
  const directory = safePath(path.join(workspace, ".publication"));
  const lockPath = path.join(directory, "publication.lock"), lock = fs.openSync(lockPath, "wx");
  try {
    // git() trims terminal whitespace; restore the canonical LF for the hash.
    assert.equal(sha256(Buffer.from(`${git(path.resolve(import.meta.dirname, ".."), ["show", `${LEGACY_WRAPPER}:tools/workshop-publish.mjs`])}\n`)), LEGACY_WRAPPER_SHA, "legacy_wrapper_source_drift");
    const current = prepared(options), tool = uploader(options.uploaderReceipt, options.uploaderReceiptSha256);
    assert.match(options.attemptId ?? "", /^[0-9]+-[a-f0-9-]+$/u);
    const folder = safePath(path.join(directory, "attempts", options.attemptId));
    const file = path.join(folder, SUCCESS_RECONCILIATION_FILE);
    assert.ok(!fs.existsSync(file), "reconciliation_already_exists");
    const result = successReconciliationProof(folder, workspace, tool, options.uploaderReceiptSha256, current, options.humanObservation);
    assert.equal(result.evidence_inventory_sha256, options.evidenceSha256, "audited_historical_evidence_pin_mismatch");
    durableJson(file, result);
    return { ...result, evidence_path: file, evidence_file_sha256: sha256(regularFile(file)) };
  } finally { fs.closeSync(lock); fs.unlinkSync(lockPath); }
}
export async function publishWorkshop(options, dependencies = {}) {
  const workspace = safePath(path.join(options.preparedRoot, "workshop"));
  const directory = safePath(path.join(workspace, ".publication"));
  fs.mkdirSync(directory, { recursive: true });
  const lockPath = path.join(directory, "publication.lock"), lock = fs.openSync(lockPath, "wx");
  let attemptDirectory;
  try {
    const ready = publicationPreflight(options, dependencies);
    const readyBytes = Buffer.from(`${JSON.stringify(ready, null, 2)}\n`);
    const hash = sha256(readyBytes);
    const readiness = path.join(directory, `ready-${hash}.json`);
    if (!fs.existsSync(readiness)) durableJson(readiness, ready);
    else assert.ok(regularFile(readiness).equals(readyBytes));
    if (!options.execute) return { ...ready, readiness_path: readiness, readiness_sha256: hash };
    if (!dependencies.run) assert.equal(process.env.CI, undefined, "real_publication_forbidden_in_ci");
    assert.equal(options.authorizeReadinessSha256, hash, "separate_human_authorization_for_exact_readiness_required");
    // Persist intent before any official uploader process can make a remote mutation.
    const state = intent(workspace, options.mode, options.itemId, ready.uploader, options.uploaderReceiptSha256, ready.prepared);
    const attemptId = `${Date.now()}-${crypto.randomUUID()}`;
    attemptDirectory = path.join(state.attempts, attemptId);
    fs.mkdirSync(attemptDirectory);
    const args = ["upload", "-w", workspace, ...(options.mode === "update" ? ["--id", options.itemId] : [])];
    durableJson(path.join(attemptDirectory, "attempt.json"), { schema: "spireagent/workshop-publication-attempt-1",
      result: "ATTEMPT_STARTED_OUTCOME_UNCONFIRMED", attempt_id: attemptId, candidate: ready, readiness_sha256: hash, args });
    fs.copyFileSync(path.join(ready.uploader.candidate_directory, "steam_appid.txt"), path.join(attemptDirectory, "steam_appid.txt"), fs.constants.COPYFILE_EXCL);
    // Revalidate bytes/source immediately before invocation; attempt remains conservative
    // UNKNOWN if interrupted after this point, including a power loss before spawn.
    assert.deepEqual((dependencies.prepared ?? verifyPrepared)(options), ready.prepared);
    assert.deepEqual((dependencies.uploader ?? verifyUploader)(options.uploaderReceipt, options.uploaderReceiptSha256), ready.uploader);
    const modIdPath = path.join(workspace, "mod_id.txt");
    if (ready.operation === "create") assert.ok(!fs.existsSync(modIdPath), "create_mod_id_appeared_before_invocation");
    else if (fs.existsSync(modIdPath)) assert.equal(itemId(regularFile(modIdPath).toString("utf8").trim()), options.itemId, "update_mod_id_changed_before_invocation");
    const result = await (dependencies.run ?? runUploader)({ executable: path.join(ready.uploader.candidate_directory, ready.uploader.executable), args, cwd: attemptDirectory });
    const id = confirmUploader(attemptDirectory, workspace, ready, result);
    assert.deepEqual((dependencies.prepared ?? verifyPrepared)(options), ready.prepared);
    assert.deepEqual((dependencies.uploader ?? verifyUploader)(options.uploaderReceipt, options.uploaderReceiptSha256), ready.uploader);
    const receipt = { schema: "spireagent/workshop-publication-1", result: "PUBLICATION_CONFIRMED_BY_UPLOADER",
      evidence_level: "exact_uploader_submit_confirmation_not_subscription_or_runtime", attempt_id: attemptId,
      candidate: ready, steam: { item_id: id, requested_visibility: "private", operation: ready.operation },
      log_sha256: sha256(regularFile(path.join(attemptDirectory, "mod-uploader.log"))),
      visibility_observed_remotely: false };
    durableJson(path.join(attemptDirectory, "publication-receipt.json"), receipt);
    return receipt;
  } catch (error) {
    if (attemptDirectory) {
      durableJson(path.join(attemptDirectory, "unknown.json"), { schema: "spireagent/workshop-publication-unknown-1",
        result: "PUBLICATION_OUTCOME_UNKNOWN", reason: error.message, retry_allowed: false });
      throw new Error(`PUBLICATION_OUTCOME_UNKNOWN: ${error.message}; stop, inspect Steam/item/mod_id/logs; never retry automatically`);
    }
    throw error;
  } finally { fs.closeSync(lock); fs.unlinkSync(lockPath); }
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const { values: v } = parseArgs({ options: Object.fromEntries([
      "prepared-root", "prepared-receipt-sha256", "provenance-sha256", "uploader-receipt", "uploader-receipt-sha256", "mode", "item-id", "authorize-readiness-sha256"
    ].map((key) => [key, { type: "string" }]).concat([["execute", { type: "boolean" }]])), allowPositionals: false });
    console.log(JSON.stringify(await publishWorkshop({ repositoryRoot: path.resolve(import.meta.dirname, ".."),
      preparedRoot: v["prepared-root"], preparedReceiptSha256: v["prepared-receipt-sha256"],
      approvedProvenanceSha256: v["provenance-sha256"], uploaderReceipt: v["uploader-receipt"],
      uploaderReceiptSha256: v["uploader-receipt-sha256"], mode: v.mode, itemId: v["item-id"],
      execute: v.execute, authorizeReadinessSha256: v["authorize-readiness-sha256"] }), null, 2));
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
