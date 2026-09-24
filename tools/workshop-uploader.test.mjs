import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { execFileSync } from "node:child_process";
import { COMMIT, UPSTREAM, fileInventory, verifyUploader, verifyUploaderSource } from "./workshop-uploader.mjs";
import { sha256 } from "./workshop-stage.mjs";

function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "uploader fixture-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const source = path.join(dir, "source"), artifacts = path.join(dir, "build"), output = path.join(artifacts, "publish/ModUploader/release_win-x64");
  fs.mkdirSync(source); fs.mkdirSync(output, { recursive: true });
  const names = ["ModUploader.exe", "ModUploader.pdb", "steam_api64.dll", "steam_appid.txt", "template/README.md", "template/content/README.md", "template/image.png", "template/workshop.json"];
  for (const name of names) {
    const bytes = name === "steam_appid.txt" ? "2868840" : `nonexecutable fixture ${name}`;
    const original = name.startsWith("template/") ? name : `steam/${name}`;
    fs.mkdirSync(path.dirname(path.join(source, original)), { recursive: true });
    fs.mkdirSync(path.dirname(path.join(output, name)), { recursive: true });
    fs.writeFileSync(path.join(source, original), bytes); fs.writeFileSync(path.join(output, name), bytes);
  }
  const r = { schema: "spireagent/workshop-uploader-1", result: "UPLOADER_BUILT_NOT_EXECUTED", repository: UPSTREAM, commit: COMMIT, worktree: "clean",
    source_directory: source, artifacts_directory: artifacts, candidate_directory: output, rid: "win-x64", executable: "ModUploader.exe",
    build_command: ["publish", "-c", "Release", "-r", "win-x64", "-p:PublishTrimmed=true", "--artifacts-path", artifacts], inventory: fileInventory(output) };
  const receipt = path.join(dir, "receipt.json");
  const seal = () => { fs.writeFileSync(receipt, JSON.stringify(r)); return sha256(fs.readFileSync(receipt)); };
  const options = { verifySource: () => ({ repository: UPSTREAM, commit: COMMIT, worktree: "clean" }) };
  return { source, output, receipt, r, seal, options };
}
test("complete exact uploader inventory verified, not just executable", (t) => {
  const f = fixture(t); assert.deepEqual(verifyUploader(f.receipt, f.seal(), f.options), f.r);
  assert.equal(f.r.inventory.length, 8);
});
for (const fault of ["repository", "commit", "dirty", "source-dirty", "extra", "empty-extra-directory", "missing", "native-hash", "exe-hash", "receipt", "command"]) {
  test(`uploader ${fault} rejects`, (t) => {
    const f = fixture(t);
    if (fault === "repository") f.r.repository = "impostor/repo";
    if (fault === "commit") f.r.commit = "f".repeat(40);
    if (fault === "dirty") f.r.worktree = "dirty";
    if (fault === "source-dirty") f.options.verifySource = () => { throw new Error("source dirty"); };
    if (fault === "extra") fs.writeFileSync(path.join(f.output, "secret"), "bad");
    if (fault === "empty-extra-directory") fs.mkdirSync(path.join(f.output, "unknown"));
    if (fault === "missing") fs.unlinkSync(path.join(f.output, "ModUploader.pdb"));
    if (fault === "native-hash") fs.appendFileSync(path.join(f.output, "steam_api64.dll"), "bad");
    if (fault === "exe-hash") fs.appendFileSync(path.join(f.output, "ModUploader.exe"), "bad");
    if (fault === "command") f.r.build_command = ["build", "unreviewed"];
    const pin = f.seal(); if (fault === "receipt") fs.appendFileSync(f.receipt, " ");
    assert.throws(() => verifyUploader(f.receipt, pin, f.options));
  });
}
test("real Git repository and commit checks reject unrelated checkout", (t) => {
  const f = fixture(t), git = (...args) => execFileSync("git", args, { cwd: f.source, encoding: "utf8" });
  git("init", "-q"); git("remote", "add", "origin", "https://github.com/impostor/repo.git");
  assert.throws(() => verifyUploaderSource(f.source), /repository_mismatch/);
  git("remote", "set-url", "origin", `https://github.com/${UPSTREAM}.git`);
  git("config", "user.name", "Fixture"); git("config", "user.email", "fixture@example.invalid");
  git("add", "."); git("commit", "-qm", "fixture");
  assert.throws(() => verifyUploaderSource(f.source), /commit_mismatch/);
});
