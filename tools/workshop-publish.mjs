import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { spawn } from "node:child_process";
import { parseArgs } from "node:util";
import { fileURLToPath } from "node:url";
import { regularFile, safePath, sha256 } from "./workshop-stage.mjs";
import { verifyPrepared } from "./workshop-publication-candidate.mjs";
import { verifyUploader } from "./workshop-uploader.mjs";

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
function publicationState(workspace) {
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
    assert.ok(!fs.existsSync(path.join(folder, "unknown.json")) && fs.existsSync(success), "PUBLICATION_OUTCOME_UNKNOWN: Human must reconcile Steam/item/logs; no retry");
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
function intent(workspace, mode, id) {
  assert.ok(["create", "update"].includes(mode), "explicit_create_or_update_required");
  const state = publicationState(workspace);
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
  intent(candidate.workspace, options.mode, options.itemId);
  return { schema: "spireagent/workshop-publication-ready-1", result: "READY_TO_PUBLISH_PRIVATE",
    evidence_level: "local_preflight_only_no_steam_contact", operation: options.mode,
    explicit_item_id: options.itemId ?? null, requested_visibility: "private", prepared: candidate,
    uploader_receipt_sha256: options.uploaderReceiptSha256, uploader: tool };
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
  const stderr = decode(path.join(attemptDirectory, "stderr.log"));
  const log = decode(path.join(attemptDirectory, "mod-uploader.log"));
  assert.equal(stderr.trim(), "", "uploader_stderr_requires_review");
  // Exact upstream Warn/Error uses these ANSI colors; even successful SubmitItemUpdate
  // may follow a rejected visibility/content setter. Do not promote that to success.
  assert.ok(!/\u001b\[(?:31|33)m/u.test(stdout), "uploader_warning_requires_human_review");
  const id = itemId(regularFile(path.join(workspace, "mod_id.txt")).toString("utf8").trim());
  if (ready.operation === "update") assert.equal(id, ready.explicit_item_id);
  const title = JSON.parse(regularFile(path.join(workspace, "workshop.json"))).title;
  const expected = `Successfully uploaded '${title}' to the workshop with id ${id}! Browsing to the item in Steam.`;
  for (const text of [stdout, log]) assert.equal(text.split(/\r?\n/u).filter((line) => line === expected).length, 1, "missing_unambiguous_uploader_confirmation");
  return id;
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
    const state = intent(workspace, options.mode, options.itemId);
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
