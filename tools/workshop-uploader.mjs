import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { regularFile, safePath, sha256 } from "./workshop-stage.mjs";

export const UPSTREAM = "megacrit/sts2-mod-uploader";
export const COMMIT = "d7b7e6b16c413d5a124f474f9e5104ef01f76ab1";
export const git = (cwd, args) => execFileSync("git", args, { cwd, encoding: "utf8", windowsHide: true }).trim();
export function fileInventory(directory) {
  const result = [];
  function visit(relative) {
    const names = fs.readdirSync(safePath(path.join(directory, relative))).sort();
    assert.ok(names.length > 0, "empty_candidate_directory");
    for (const name of names) {
      const file = path.posix.join(relative, name);
      const full = safePath(path.join(directory, file));
      if (fs.lstatSync(full).isDirectory()) visit(file);
      else { const bytes = regularFile(full); result.push({ name: file, size_bytes: bytes.length, sha256: sha256(bytes) }); }
    }
  }
  visit("");
  return result.sort((a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0);
}
export function verifyUploaderSource(source) {
  safePath(source);
  const remote = git(source, ["remote", "get-url", "origin"]);
  assert.ok([`https://github.com/${UPSTREAM}.git`, `https://github.com/${UPSTREAM}`, `git@github.com:${UPSTREAM}.git`].includes(remote), "uploader_repository_mismatch");
  assert.equal(git(source, ["rev-parse", "HEAD"]), COMMIT, "uploader_commit_mismatch");
  assert.equal(git(source, ["status", "--porcelain", "--untracked-files=all"]), "", "uploader_source_dirty");
  // Ignored source additions can affect MSBuild too. Build outputs live elsewhere.
  assert.equal(git(source, ["ls-files", "--others", "--ignored", "--exclude-standard"]), "", "uploader_ignored_source_inputs");
  return { repository: UPSTREAM, commit: COMMIT, worktree: "clean" };
}
export function verifyPublishInventory(source, directory, rid) {
  const native = { "win-x64": "steam_api64.dll", "linux-x64": "libsteam_api.so", "osx-x64": "libsteam_api.dylib", "osx-arm64": "libsteam_api.dylib" }[rid];
  assert.ok(native, "unsupported_uploader_rid");
  const executable = rid.startsWith("win-") ? "ModUploader.exe" : "ModUploader";
  const entries = fileInventory(directory);
  const copied = ["steam_appid.txt", native, "template/README.md", "template/content/README.md", "template/image.png", "template/workshop.json"];
  const allowed = [executable, "ModUploader.pdb", ...copied].sort();
  assert.deepEqual(entries.map((x) => x.name), allowed, "unexpected_uploader_publish_inventory");
  for (const name of copied) {
    const original = name.startsWith("template/") ? name : `steam/${name}`;
    assert.ok(regularFile(path.join(source, original)).equals(regularFile(path.join(directory, name))), `uploader_copy_mismatch:${name}`);
  }
  assert.equal(regularFile(path.join(directory, "steam_appid.txt")).toString("utf8").trim(), "2868840");
  return { executable, inventory: entries };
}
export function verifyUploader(receiptPath, expectedHash, { verifySource = verifyUploaderSource } = {}) {
  assert.match(expectedHash ?? "", /^[a-f0-9]{64}$/u, "uploader_receipt_pin_required");
  const bytes = regularFile(receiptPath);
  assert.equal(sha256(bytes), expectedHash, "uploader_receipt_drift");
  const r = JSON.parse(bytes);
  assert.equal(r.schema, "spireagent/workshop-uploader-1");
  assert.equal(r.result, "UPLOADER_BUILT_NOT_EXECUTED");
  assert.equal(r.repository, UPSTREAM, "uploader_repository_mismatch");
  assert.equal(r.commit, COMMIT, "uploader_commit_mismatch");
  assert.equal(r.worktree, "clean", "uploader_source_dirty");
  assert.deepEqual(verifySource(r.source_directory), { repository: UPSTREAM, commit: COMMIT, worktree: "clean" });
  const actual = verifyPublishInventory(r.source_directory, r.candidate_directory, r.rid);
  assert.deepEqual(actual.inventory, r.inventory, "uploader_inventory_drift");
  assert.equal(actual.executable, r.executable);
  assert.deepEqual(r.build_command, publishArgs(r.rid, r.artifacts_directory));
  assert.equal(path.resolve(r.candidate_directory), path.join(path.resolve(r.artifacts_directory), "publish/ModUploader", `release_${r.rid}`));
  return r;
}
const publishArgs = (rid, artifacts) => ["publish", "-c", "Release", "-r", rid, "-p:PublishTrimmed=true", "--artifacts-path", artifacts];
export function buildUploader({ sourceDirectory, artifactsDirectory, rid, receiptPath }) {
  assert.ok(["win-x64", "linux-x64", "osx-x64", "osx-arm64"].includes(rid), "unsupported_uploader_rid");
  const source = safePath(sourceDirectory), artifacts = safePath(artifactsDirectory);
  const identity = verifyUploaderSource(source);
  // Never overwrite an existing candidate, never compile inside upstream source.
  assert.ok(path.isAbsolute(artifactsDirectory));
  assert.ok(!fs.existsSync(artifacts), "fresh_uploader_artifacts_directory_required");
  assert.ok(path.relative(source, artifacts).startsWith(`..${path.sep}`), "uploader_build_must_be_outside_source");
  assert.ok(!fs.existsSync(receiptPath), "uploader_receipt_already_exists");
  const sdk = execFileSync("dotnet", ["--version"], { cwd: source, encoding: "utf8" }).trim();
  assert.match(sdk, /^9\./u, "upstream_dotnet9_sdk_required");
  execFileSync("dotnet", publishArgs(rid, artifacts), { cwd: source, stdio: "inherit", windowsHide: true });
  assert.deepEqual(verifyUploaderSource(source), identity);
  const directory = path.join(artifacts, "publish/ModUploader", `release_${rid}`);
  const actual = verifyPublishInventory(source, directory, rid);
  const receipt = { schema: "spireagent/workshop-uploader-1", result: "UPLOADER_BUILT_NOT_EXECUTED", ...identity,
    source_directory: source, artifacts_directory: artifacts, candidate_directory: directory,
    rid, build_platform: process.platform, build_architecture: process.arch, sdk,
    build_command: publishArgs(rid, artifacts), ...actual };
  safePath(receiptPath);
  fs.writeFileSync(receiptPath, `${JSON.stringify(receipt, null, 2)}\n`, { flag: "wx" });
  return { ...receipt, receipt_sha256: sha256(regularFile(receiptPath)) };
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const { values: v } = parseArgs({ options: { source: { type: "string" }, artifacts: { type: "string" }, rid: { type: "string" }, receipt: { type: "string" } } });
    console.log(JSON.stringify(buildUploader({ sourceDirectory: v.source, artifactsDirectory: v.artifacts, rid: v.rid, receiptPath: v.receipt }), null, 2));
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
