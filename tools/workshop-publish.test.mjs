import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { execFileSync } from "node:child_process";
import { proposeWorkshopBuild } from "./workshop-prepare.mjs";
import { finalizeWorkshop } from "./workshop-finalize.mjs";
import { inspectWorkshopBuild, sha256, stageWorkshop } from "./workshop-stage.mjs";
import { verifyPrepared, verifyPublicationEvolution } from "./workshop-publication-candidate.mjs";
import { publicationPreflight, publishWorkshop, reconcilePreMutationFailure, runUploader, itemId } from "./workshop-publish.mjs";

const root = path.resolve(import.meta.dirname, "..");
async function fixture(t) {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "publish space-"));
  t.after(() => fs.rmSync(repo, { recursive: true, force: true }));
  const output = path.join(repo, "apps/game-mod/bin/Release/net9.0"), w = path.join(repo, "workshop");
  fs.mkdirSync(output, { recursive: true }); fs.mkdirSync(w);
  for (const file of ["workshop.json", "image.png"]) fs.copyFileSync(path.join(root, "workshop", file), path.join(w, file));
  const manifest = fs.readFileSync(path.join(root, "apps/game-mod/mod_manifest.json"));
  fs.writeFileSync(path.join(repo, "apps/game-mod/mod_manifest.json"), manifest);
  fs.writeFileSync(path.join(output, "STS2_PLATFORM.json"), manifest);
  fs.writeFileSync(path.join(output, "STS2_PLATFORM.dll"), "synthetic non-PE only");
  const c = { source_revision: "a".repeat(40), source_digest_sha256: "b".repeat(64), component_worktree_status: "clean" };
  const source = { platform: { ...c, workspace_revision: "c".repeat(40), workspace_worktree_status: "clean" }, components: { fixture: c } };
  const identity = { sha256: sha256(fs.readFileSync(path.join(output, "STS2_PLATFORM.dll"))), module_version_id: "12345678-1234-1234-1234-123456789abc" };
  const provenance = { schema: "sts2.platform/game-mod-build-provenance-1", built_at: "2026-09-19T00:00:00Z", platform: process.platform, architecture: process.arch,
    source, artifact: identity, package: { manifest: JSON.parse(manifest), files: ["STS2_PLATFORM.dll", "STS2_PLATFORM.json"] },
    game: { release: { version: "fixture", commit: "fixture" }, sts2: identity, godotsharp_sha256: "d".repeat(64), harmony_sha256: "e".repeat(64) } };
  fs.writeFileSync(path.join(output, "build-provenance.json"), JSON.stringify(provenance));
  const readSource = () => structuredClone(source), inspect = (args) => inspectWorkshopBuild({ ...args, readIdentity: () => identity });
  const proposal = await proposeWorkshopBuild({ repositoryRoot: repo, readSource, inspect, preflight: async () => {}, run: async () => {} });
  await finalizeWorkshop({ repositoryRoot: repo, approvedProvenanceSha256: proposal.provenance_sha256,
    readSource, inspect, verifyEvolution: () => [], run: async () => stageWorkshop({ repositoryRoot: repo, sourceDirectory: output, approvedProvenanceSha256: proposal.provenance_sha256, readSource, readIdentity: () => identity }) });
  const options = { repositoryRoot: repo, preparedRoot: repo, approvedProvenanceSha256: proposal.provenance_sha256,
    preparedReceiptSha256: sha256(fs.readFileSync(path.join(w, "prepare-receipt.json"))), uploaderReceipt: "fixture", uploaderReceiptSha256: "f".repeat(64), mode: "create" };
  const candidateDir = path.join(repo, "fake-tool"); fs.mkdirSync(candidateDir);
  fs.writeFileSync(path.join(candidateDir, "steam_appid.txt"), "2868840");
  const tool = { repository: "megacrit/sts2-mod-uploader", commit: "d7b7e6b16c413d5a124f474f9e5104ef01f76ab1", rid: `${{ win32: "win", linux: "linux", darwin: "osx" }[process.platform]}-${process.arch}`,
    candidate_directory: candidateDir, executable: "fake", inventory: [{ name: "fixture", sha256: "f".repeat(64), size_bytes: 1 }] };
  const calls = [];
  const dependencies = {
    prepared: (o) => verifyPrepared({ ...o, readSource, inspect, verifyPrepareEvolution: () => [], verifyEvolution: () => [] }),
    uploader: () => structuredClone(tool),
    run: async (call) => {
      calls.push(call); const id = "123456789";
      fs.writeFileSync(path.join(w, "mod_id.txt"), `${id}\n`);
      const line = `Successfully uploaded 'SpireAgent Platform' to the workshop with id ${id}! Browsing to the item in Steam.\n`;
      fs.writeFileSync(path.join(call.cwd, "stdout.log"), line);
      fs.writeFileSync(path.join(call.cwd, "mod-uploader.log"), line);
      fs.writeFileSync(path.join(call.cwd, "stderr.log"), "");
      return { code: 0, signal: null };
    }
  };
  const authorize = async () => { const ready = await publishWorkshop(options, dependencies); options.execute = true; options.authorizeReadinessSha256 = ready.readiness_sha256; return ready; };
  return { repo, w, output, source, tool, options, dependencies, calls, authorize };
}
test("actual Layer 3 fixture preflight reads unchanged receipts, no stage/build/upload", async (t) => {
  const f = await fixture(t), before = fs.readFileSync(path.join(f.w, "prepare-receipt.json"));
  const r = await publishWorkshop(f.options, f.dependencies);
  assert.equal(r.result, "READY_TO_PUBLISH_PRIVATE"); assert.equal(f.calls.length, 0);
  assert.deepEqual(before, fs.readFileSync(path.join(f.w, "prepare-receipt.json")));
  assert.deepEqual(await publishWorkshop(f.options, f.dependencies), r);
});
for (const fault of ["receipt", "dll", "manifest", "metadata", "preview", "extra", "missing", "provenance", "source", "staging", "proposal", "public"]) {
  test(`preflight ${fault} drift fails without uploader invocation`, async (t) => {
    const f = await fixture(t);
    const files = { receipt: "prepare-receipt.json", dll: "content/STS2_PLATFORM.dll", manifest: "content/STS2_PLATFORM.json", metadata: "workshop.json", preview: "image.png", staging: "staging-receipt.json", proposal: "build-proposal.json" };
    if (files[fault]) fs.appendFileSync(path.join(f.w, files[fault]), "tamper");
    if (fault === "extra") fs.writeFileSync(path.join(f.w, "content/secret.txt"), "forbidden");
    if (fault === "missing") fs.unlinkSync(path.join(f.w, "content/STS2_PLATFORM.dll"));
    if (fault === "provenance") f.options.approvedProvenanceSha256 = "0".repeat(64);
    if (fault === "source") f.source.platform.workspace_worktree_status = "dirty";
    if (fault === "public") { const file = path.join(f.w, "workshop.json"), obj = JSON.parse(fs.readFileSync(file)); obj.visibility = "public"; fs.writeFileSync(file, JSON.stringify(obj)); }
    await assert.rejects(publishWorkshop(f.options, f.dependencies)); assert.equal(f.calls.length, 0);
  });
}
test("create requires intent/private/absent mod_id; update cannot guess item", async (t) => {
  const f = await fixture(t);
  f.options.mode = undefined; assert.throws(() => publicationPreflight(f.options, f.dependencies));
  f.options.mode = "update"; assert.throws(() => publicationPreflight(f.options, f.dependencies));
  f.options.itemId = "123"; assert.throws(() => publicationPreflight(f.options, f.dependencies), /accepted_state/);
  f.options.mode = "create"; delete f.options.itemId;
  fs.writeFileSync(path.join(f.w, "mod_id.txt"), "123");
  assert.throws(() => publicationPreflight(f.options, f.dependencies), /ambiguous_mod_id/);
});
test("fake success records full identities; create no ID, update explicitly passes ID", async (t) => {
  const f = await fixture(t); await f.authorize();
  const first = await publishWorkshop(f.options, f.dependencies);
  assert.equal(first.result, "PUBLICATION_CONFIRMED_BY_UPLOADER");
  assert.deepEqual(f.calls[0].args, ["upload", "-w", f.w]);
  assert.equal(first.candidate.prepared.receipt.artifact.sha256, f.dependencies.prepared(f.options).receipt.artifact.sha256);
  assert.deepEqual(first.candidate.uploader.inventory, f.tool.inventory);
  assert.ok(fs.existsSync(path.join(f.calls[0].cwd, "attempt.json")));
  await assert.rejects(publishWorkshop({ ...f.options, execute: false }, f.dependencies), /existing_accepted_item/);
  f.options.mode = "update"; f.options.itemId = "123456789"; f.options.execute = false;
  fs.writeFileSync(path.join(f.w, "mod_id.txt"), "999");
  await assert.rejects(publishWorkshop(f.options, f.dependencies), /mod_id_conflict/);
  fs.unlinkSync(path.join(f.w, "mod_id.txt")); // accepted state prevents implicit create even without file
  await f.authorize(); await publishWorkshop(f.options, f.dependencies);
  assert.deepEqual(f.calls[1].args, ["upload", "-w", f.w, "--id", "123456789"]);
});
for (const fault of ["nonzero", "missing-id", "invalid-id", "no-confirmation", "warning", "timeout", "crash", "interruption", "decode"]) {
  test(`${fault} records UNKNOWN, no success or automatic retry`, async (t) => {
    const f = await fixture(t); await f.authorize();
    const success = f.dependencies.run;
    f.dependencies.run = async (call) => {
      assert.ok(fs.existsSync(path.join(call.cwd, "attempt.json")), "durable attempt precedes invocation");
      const result = await success(call);
      if (fault === "nonzero") return { code: 1 };
      if (["timeout", "interruption"].includes(fault)) return { error: fault };
      if (fault === "crash") throw new Error("process crash");
      if (fault === "missing-id") fs.unlinkSync(path.join(f.w, "mod_id.txt"));
      if (fault === "invalid-id") fs.writeFileSync(path.join(f.w, "mod_id.txt"), "0");
      if (fault === "no-confirmation") fs.writeFileSync(path.join(call.cwd, "mod-uploader.log"), "started only");
      if (fault === "warning") fs.appendFileSync(path.join(call.cwd, "stdout.log"), "\x1b[33mFailed to set visibility!\x1b[0m");
      if (fault === "decode") fs.writeFileSync(path.join(call.cwd, "stdout.log"), Buffer.from([255]));
      return result;
    };
    await assert.rejects(publishWorkshop(f.options, f.dependencies), /PUBLICATION_OUTCOME_UNKNOWN/);
    assert.ok(fs.existsSync(path.join(f.calls[0].cwd, "unknown.json")));
    assert.ok(!fs.existsSync(path.join(f.calls[0].cwd, "publication-receipt.json")));
    await assert.rejects(publishWorkshop(f.options, f.dependencies), /PUBLICATION_OUTCOME_UNKNOWN/);
    assert.equal(f.calls.length, 1);
  });
}
test("missing authorization and concurrent lock block uploader; abrupt attempt blocks restart", async (t) => {
  const f = await fixture(t); f.options.execute = true;
  await assert.rejects(publishWorkshop(f.options, f.dependencies), /authorization/);
  const state = path.join(f.w, ".publication"), lock = path.join(state, "publication.lock");
  fs.writeFileSync(lock, "interrupted");
  await assert.rejects(publishWorkshop(f.options, f.dependencies), /EEXIST/); fs.unlinkSync(lock);
  const folder = path.join(state, "attempts/1-abcd"); fs.mkdirSync(folder);
  fs.writeFileSync(path.join(folder, "attempt.json"), JSON.stringify({ schema: "spireagent/workshop-publication-attempt-1", attempt_id: "1-abcd" }));
  await assert.rejects(publishWorkshop(f.options, f.dependencies), /PUBLICATION_OUTCOME_UNKNOWN/);
  assert.equal(f.calls.length, 0);
});
async function failedBeforeSteamMutation(t) {
  const f = await fixture(t), ready = await f.authorize();
  const originalPrepared = fs.readFileSync(path.join(f.w, "prepare-receipt.json"));
  f.dependencies.run = async (call) => {
    f.calls.push(call);
    const message = "Steam initialization failed! Result: k_ESteamAPIInitResult_FailedGeneric, message: Could not determine Steam client install directory.";
    fs.writeFileSync(path.join(call.cwd, "stdout.log"), `Initializing Steam\r\n\x1b[31m${message}\x1b[0m\r\n`);
    fs.writeFileSync(path.join(call.cwd, "mod-uploader.log"), `Initializing Steam\r\n${message}\r\n`);
    fs.writeFileSync(path.join(call.cwd, "stderr.log"), "");
    return { code: 1 };
  };
  await assert.rejects(publishWorkshop(f.options, f.dependencies), /PUBLICATION_OUTCOME_UNKNOWN/);
  const folder = f.calls[0].cwd;
  const attemptId = path.basename(folder);
  const original = Object.fromEntries(["attempt.json", "unknown.json", "stdout.log", "stderr.log", "mod-uploader.log"]
    .map((name) => [name, fs.readFileSync(path.join(folder, name))]));
  assert.equal(f.calls.length, 1);
  return { ...f, ready, folder, attemptId, original, originalPrepared };
}
const reconcile = (f) => reconcilePreMutationFailure({ ...f.options, attemptId: f.attemptId }, f.dependencies);
test("exact pinned initialization failure is separately reconcilable; original UNKNOWN and candidate stay immutable", async (t) => {
  const f = await failedBeforeSteamMutation(t);
  await assert.rejects(publishWorkshop({ ...f.options, execute: false }, f.dependencies), /PUBLICATION_OUTCOME_UNKNOWN/);
  const result = reconcile(f);
  assert.equal(result.result, "PUBLICATION_FAILED_BEFORE_REMOTE_MUTATION");
  assert.equal(result.new_human_authorization_required, true);
  assert.equal(result.automatic_retry_allowed, false);
  for (const [name, bytes] of Object.entries(f.original)) assert.deepEqual(fs.readFileSync(path.join(f.folder, name)), bytes);
  assert.deepEqual(fs.readFileSync(path.join(f.w, "prepare-receipt.json")), f.originalPrepared);
  assert.equal(f.calls.length, 1, "reconciliation never invokes uploader");
  f.options.execute = false;
  const ready = await publishWorkshop(f.options, f.dependencies);
  assert.equal(ready.result, "READY_TO_PUBLISH_PRIVATE");
  assert.equal(f.calls.length, 1, "later preflight does not retry");
  await assert.rejects(async () => reconcile(f), /reconciliation_already_exists/);
});
for (const fault of ["missing-log", "ambiguous-log", "create-marker", "item-marker", "uploader-commit", "uploader-inventory", "attempt-changed", "missing-unknown", "mod-id"]) {
  test(`pre-mutation reconciliation rejects ${fault} and leaves UNKNOWN`, async (t) => {
    const f = await failedBeforeSteamMutation(t);
    if (fault === "missing-log") fs.unlinkSync(path.join(f.folder, "mod-uploader.log"));
    if (fault === "ambiguous-log") fs.writeFileSync(path.join(f.folder, "mod-uploader.log"), "Initializing Steam\nError\n");
    if (fault === "create-marker") fs.appendFileSync(path.join(f.folder, "mod-uploader.log"), "Creating new workshop item...\n");
    if (fault === "item-marker") fs.appendFileSync(path.join(f.folder, "stdout.log"), "Uploading with item ID 123\n");
    if (fault === "uploader-commit") f.dependencies.uploader = () => ({ ...f.tool, commit: "0".repeat(40) });
    if (fault === "uploader-inventory") f.dependencies.uploader = () => ({ ...f.tool, inventory: [] });
    if (fault === "attempt-changed") fs.appendFileSync(path.join(f.folder, "attempt.json"), " ");
    if (fault === "missing-unknown") fs.unlinkSync(path.join(f.folder, "unknown.json"));
    if (fault === "mod-id") fs.writeFileSync(path.join(f.w, "mod_id.txt"), "123\n");
    await assert.rejects(async () => reconcile(f));
    assert.ok(!fs.existsSync(path.join(f.folder, "pre-mutation-reconciliation.json")));
    assert.equal(f.calls.length, 1);
  });
}
test("a reconciled historical log or attempt modification re-blocks preflight", async (t) => {
  const f = await failedBeforeSteamMutation(t); reconcile(f);
  fs.appendFileSync(path.join(f.folder, "stdout.log"), "altered");
  await assert.rejects(publishWorkshop({ ...f.options, execute: false }, f.dependencies), /PUBLICATION_OUTCOME_UNKNOWN/);
  assert.equal(f.calls.length, 1);
});
test("reconciled history remains valid after a separately authorized later create succeeds", async (t) => {
  const f = await failedBeforeSteamMutation(t); reconcile(f);
  f.options.execute = false;
  const ready = await publishWorkshop(f.options, f.dependencies);
  f.options.execute = true; f.options.authorizeReadinessSha256 = ready.readiness_sha256;
  f.dependencies.run = async (call) => {
    f.calls.push(call);
    fs.writeFileSync(path.join(f.w, "mod_id.txt"), "123456789\n");
    const line = "Successfully uploaded 'SpireAgent Platform' to the workshop with id 123456789! Browsing to the item in Steam.\n";
    fs.writeFileSync(path.join(call.cwd, "stdout.log"), line);
    fs.writeFileSync(path.join(call.cwd, "stderr.log"), "");
    fs.writeFileSync(path.join(call.cwd, "mod-uploader.log"), line);
    return { code: 0 };
  };
  const published = await publishWorkshop(f.options, f.dependencies);
  assert.equal(published.result, "PUBLICATION_CONFIRMED_BY_UPLOADER");
  assert.equal(f.calls.length, 2, "only the separately authorized call invokes uploader again");
  f.options.mode = "update"; f.options.itemId = "123456789"; f.options.execute = false;
  assert.equal((await publishWorkshop(f.options, f.dependencies)).result, "READY_TO_PUBLISH_PRIVATE");
});
test("path separators/spaces preserve preflight; ulong item validation", async (t) => {
  const f = await fixture(t), a = await publishWorkshop(f.options, f.dependencies);
  f.options.preparedRoot = f.repo.replaceAll("\\", "/");
  assert.deepEqual(await publishWorkshop(f.options, f.dependencies), a);
  for (const bad of ["0", "-1", "1;echo", "18446744073709551616", "01"]) assert.throws(() => itemId(bad));
});
test("real process runner timeout/crash stays failed (Node fake, never Steam)", async (t) => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "fake-uploader-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const r = await runUploader({ executable: process.execPath, args: ["-e", "setTimeout(()=>{},10000)"], cwd: directory, timeoutMs: 100 });
  assert.equal(r.error, "timeout");
  // Give the killed process time to release inherited Windows file handles.
  await new Promise((resolve) => setTimeout(resolve, 100));
});
test("real Git publication evolution rejects runtime/unknown paths", (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "publication-git-")); t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const git = (...args) => execFileSync("git", args, { cwd: dir, encoding: "utf8" }).trim();
  git("init", "-q"); git("config", "user.name", "Fixture"); git("config", "user.email", "fixture@example.invalid");
  fs.writeFileSync(path.join(dir, "README.md"), "base"); git("add", "."); git("commit", "-qm", "base"); const base = git("rev-parse", "HEAD");
  fs.writeFileSync(path.join(dir, "README.md"), "docs"); git("add", "."); git("commit", "-qm", "docs");
  assert.deepEqual(verifyPublicationEvolution(dir, base, git("rev-parse", "HEAD")), ["README.md"]);
  fs.writeFileSync(path.join(dir, "runtime.cs"), "bad"); git("add", "."); git("commit", "-qm", "bad");
  assert.throws(() => verifyPublicationEvolution(dir, base, git("rev-parse", "HEAD")), /source_drift/);
});
