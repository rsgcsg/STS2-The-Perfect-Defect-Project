import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import crypto from "node:crypto";
import { canonical, collectionIdentity, copyCollectionSetup, copyP1NativeTools, p1NativeSupport, inventory, publishCollectionTool, setupFiles } from "./publish-collection-tool.mjs";

test("Source3 capability is authenticated by the publisher identity and keeps legacy support", () => {
  const identity = collectionIdentity({ workspaceRevision: "a".repeat(40),
    componentSourceRevision: "b".repeat(40), componentTreeRevision: "c".repeat(40) }, []);
  assert.equal(identity.supported_recording_schema, "sts2.human-annotator/recording-manifest-2");
  assert.equal(identity.output_schema, "sts2.human-annotator/session-bundle-3");
  assert.deepEqual(identity.source_v3_support, {
    schema: "sts2.evidence/source3-collection-support-1", command: "pack-source-v3",
    recording_schema: "sts2.annotator/source-session-manifest-3", source_profile: "native-logical-source-v3",
    bundle_schema: "sts2.annotator/source-session-bundle-3", type_id: "source-session-bundle-v3"
  });
  const release = value => crypto.createHash("sha256").update(canonical(value)).digest("hex");
  const old = { ...identity }; delete old.source_v3_support;
  assert.notEqual(release(identity), release(old));
  assert.notEqual(release(identity), release({ ...identity,
    source_v3_support: { ...identity.source_v3_support, command: "pack-session" } }));
});

test("release identity sorts nested keys and binds exact dependency bytes", () => {
  assert.equal(canonical({ z: [{ b: 2, a: 1 }], a: "你好" }), '{"a":"你好","z":[{"a":1,"b":2}]}');
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "collection-tool-"));
  try {
    fs.writeFileSync(path.join(directory, "tool.dll"), "original");
    const first = inventory(directory);
    fs.writeFileSync(path.join(directory, "tool.dll"), "replaced");
    assert.notEqual(inventory(directory)[0].sha256, first[0].sha256);
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});

test("release publication refuses dirty source before invoking any build", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "collection-source-"));
  const git = (...args) => execFileSync("git", args, { cwd: directory, stdio: "pipe" });
  try {
    git("init"); git("config", "user.name", "Fixture"); git("config", "user.email", "fixture@example.invalid");
    fs.writeFileSync(path.join(directory, "source"), "one");
    git("add", "source"); git("commit", "-m", "fixture");
    fs.writeFileSync(path.join(directory, "source"), "two");
    assert.throws(() => publishCollectionTool(directory, path.join(directory, "out"), { dotnet: "must-not-run" }), /clean workspace/);
    assert.equal(fs.existsSync(path.join(directory, "out")), false);
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});

test("installed setup copies its owning dependency closure and runs without a source checkout", () => {
  const directory = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "collection-installed-")));
  try {
    const workspaceRoot = path.resolve(import.meta.dirname, "../../..");
    copyCollectionSetup(workspaceRoot, directory);
    assert.deepEqual(inventory(directory).map(row => row.path), setupFiles.map(file => `setup/${file}`).sort());
    const result = JSON.parse(execFileSync(process.execPath, [path.join(directory, "setup/apps/game-mod/collection-setup.mjs"),
      "status", "--game-dir", path.join(directory, "absent-game"), "--recordings-root", path.join(directory, "recordings"),
      "--mod-provenance", path.join(directory, "absent-provenance.json")], { cwd: directory, encoding: "utf8" }));
    assert.equal(result.schema, "sts2.platform/collection-setup-1");
    assert.equal(result.bound, false);
    assert.equal(result.reason, "installation_unavailable");
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});

test("fixed P1 release includes a real executable reader/core/SDK closure and explicit capability", () => {
  const directory = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "collection-p1-installed-")));
  try {
    const workspaceRoot = path.resolve(import.meta.dirname, "../../..");
    copyCollectionSetup(workspaceRoot, directory); copyP1NativeTools(workspaceRoot, directory);
    const files = inventory(directory), identity = collectionIdentity({ workspaceRevision: "a".repeat(40),
      componentSourceRevision: "b".repeat(40), componentTreeRevision: "c".repeat(40) }, files);
    assert.deepEqual(identity.p1_native_support, p1NativeSupport);
    assert.ok(files.some(file => file.path === "p1/node_modules/zod/package.json"));
    assert.ok(files.some(file => file.path === p1NativeSupport.recording_client_entrypoint));
    const asUrl = file => new URL(`file://${file}`).href;
    const code = `import {convertNativeSemanticOriginals as convert} from ${JSON.stringify(asUrl(path.join(directory, p1NativeSupport.converter_entrypoint)))};
      import * as sdk from ${JSON.stringify(asUrl(path.join(directory, "p1/components/connector/sdk/typescript/dist/index.js")))};
      import * as core from ${JSON.stringify(asUrl(path.join(directory, "p1/components/connector/sdk/typescript/dist/p1Global/index.js")))};
      import {P1NativeRecordingClient} from ${JSON.stringify(asUrl(path.join(directory, p1NativeSupport.recording_client_entrypoint)))};
      import {nativeHandTransaction} from ${JSON.stringify(asUrl(path.join(workspaceRoot, "tools/test/fixtures/native-hand-transaction.mjs")))};
      const c=convert(nativeHandTransaction().source,sdk,core,{includeSegments:true,selectorTransactions:'native-selector-finalized-d-1'});
      console.log(JSON.stringify({complete:c.complete,D:c.derived_transaction_choices.length,client:typeof P1NativeRecordingClient}));`;
    const result = JSON.parse(execFileSync(process.execPath, ["--input-type=module", "-e", code],
      { cwd: directory, encoding: "utf8" }));
    assert.deepEqual(result, { complete: true, D: 1, client: "function" });
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});
