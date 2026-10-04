import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";
import golden from "./fixtures/runtime-seal-golden.json" with { type: "json" };
import contract from "../runtime-seal-contract.json" with { type: "json" };
import { fetchOfficialRuntimeRelease, readOfficialRuntimeRelease, parseRuntimeSeal } from "../runtime-release.mjs";
import { publishPreparedRuntimeRelease, readRuntimeReleaseDocuments, runtimeSidecarNames,
  verifyNativeSealBinding, verifyInstalledRuntimePair } from "../runtime-installation.mjs";

const sha = (bytes) => crypto.createHash("sha256").update(bytes).digest("hex");
async function fixture() {
  const seal = JSON.parse(golden.seal_raw);
  const archive = Buffer.from("synthetic ZIP bytes; Python verifies real ZIP independently");
  const evidence = Buffer.from("synthetic bootstrap audit; not ordinary qualification");
  seal.release.archive_sha256 = sha(archive);
  seal.evidence.record_sha256 = sha(evidence);
  const payloads = [archive, Buffer.from(JSON.stringify(seal)), evidence];
  const names = [seal.release.archive_asset, `STS2-Platform-${seal.release.version}-runtime-seal.json`, seal.evidence.record_asset];
  payloads.push(Buffer.from(names.map((name, index) => `${sha(payloads[index])}  ${name}`).join("\n") + "\n"));
  names.push("checksums.sha256");
  const metadata = { id: 1234, tag_name: seal.release.tag, draft: false, prerelease: true,
    published_at: "2026-10-01T00:00:00Z", assets: names.map((name, index) => ({
      id: index + 1, name, state: "uploaded", size: payloads[index].length, digest: `sha256:${sha(payloads[index])}`
    })) };
  const bundle = await fetchOfficialRuntimeRelease(seal.release.version, { fetchImpl: async (url) => {
    if (url.includes("/releases/tags/")) return new Response(JSON.stringify(metadata));
    const id = Number(url.split("/").at(-1));
    assert.ok(id >= 1 && id <= 4);
    return new Response(payloads[id - 1]);
  } });
  const identity = { status: "verified_runtime_archive_identity", archive_sha256: sha(archive),
    manifest: { id: "STS2_PLATFORM", version: seal.release.version },
    provenance: { schema: "sts2.platform/game-mod-build-provenance-1",
      platform: "darwin", architecture: "arm64",
      artifact: { sha256: seal.host.artifact_sha256, module_version_id: seal.host.artifact_mvid },
      source: { platform: { source_revision: seal.host.platform_source_revision,
        source_digest_sha256: seal.host.compiled_source_digest_sha256 },
      components: { connector: { source_revision: seal.host.connector_source_revision } } },
      game: { release: { version: seal.environment.game_version, commit: seal.environment.game_commit,
        main_assembly_hash: 999 /* declared hash intentionally differs from runtime */ },
      sts2: { sha256: seal.environment.main_assembly_sha256, module_version_id: seal.environment.main_assembly_mvid } } } };
  return { bundle, identity, archive, seal };
}

test("official bundle publication binds complete archive tuple and local document pair", async () => {
  const { bundle, identity, archive, seal } = await fixture();
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "sts2-seal-install-test-"));
  try {
    const directory = path.join(temporary, sha(archive));
    fs.mkdirSync(directory);
    fs.writeFileSync(path.join(directory, "package.zip"), archive);
    assert.throws(() => publishPreparedRuntimeRelease({ status: "official_release_assets_verified" }, directory, identity), /official_release_bundle_required/u);
    const result = publishPreparedRuntimeRelease(bundle, directory, identity);
    assert.equal(result.status, "official_runtime_release_prepared");
    const documents = path.join(directory, "runtime-release");
    const prepared = readRuntimeReleaseDocuments(documents, sha(archive));
    assert.equal(prepared.seal.environment.main_assembly_hash, seal.environment.main_assembly_hash);
    const mods = path.join(temporary, "synthetic-mods");
    fs.mkdirSync(mods);
    assert.throws(() => verifyInstalledRuntimePair(mods, prepared));
    for (const [index, raw] of [prepared.sealBytes, prepared.provenanceBytes].entries())
      fs.writeFileSync(path.join(mods, runtimeSidecarNames[index]), raw);
    verifyInstalledRuntimePair(mods, prepared);
    assert.throws(() => verifyInstalledRuntimePair(mods, null), /unexpected_installed_runtime_seal/u);
    fs.appendFileSync(path.join(mods, runtimeSidecarNames[0]), " ");
    assert.throws(() => verifyInstalledRuntimePair(mods, prepared), /installed_runtime_seal_drift/u);
    fs.appendFileSync(path.join(documents, "runtime-evidence.json"), "changed");
    assert.throws(() => readRuntimeReleaseDocuments(documents, sha(archive)), /prepared_binding_mismatch/u);
    assert.throws(() => publishPreparedRuntimeRelease(bundle, directory, identity), /already_published/u);
  } finally { fs.rmSync(temporary, { recursive: true, force: true }); }
});

test("seal cannot bind an archive with a different native producer or platform tuple", async () => {
  const { identity, seal } = await fixture();
  verifyNativeSealBinding(identity, seal);
  const paths = ["archive_sha256", "manifest.id", "manifest.version", "provenance.platform", "provenance.architecture",
    "provenance.artifact.sha256", "provenance.artifact.module_version_id",
    "provenance.source.platform.source_revision", "provenance.source.platform.source_digest_sha256",
    "provenance.source.components.connector.source_revision", "provenance.game.release.version",
    "provenance.game.release.commit", "provenance.game.sts2.sha256", "provenance.game.sts2.module_version_id"];
  for (const dotted of paths) {
    const changed = structuredClone(identity);
    const keys = dotted.split(".");
    let value = changed;
    for (const key of keys.slice(0, -1)) value = value[key];
    value[keys.at(-1)] = "different";
    assert.throws(() => verifyNativeSealBinding(changed, seal), /native_binding_mismatch/u, dotted);
  }
});

test("prepared document inventory, raw duplicates, partial pairs and oversize files fail closed", async () => {
  const { bundle, identity, archive } = await fixture();
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "sts2-seal-invalid-test-"));
  try {
    const directory = path.join(temporary, sha(archive));
    fs.mkdirSync(directory);
    fs.writeFileSync(path.join(directory, "package.zip"), archive);
    publishPreparedRuntimeRelease(bundle, directory, identity);
    const documents = path.join(directory, "runtime-release");
    fs.writeFileSync(path.join(documents, "unexpected.json"), "{}");
    assert.throws(() => readRuntimeReleaseDocuments(documents, sha(archive)), /inventory_invalid/u);
    fs.rmSync(path.join(documents, "unexpected.json"));
    const sealPath = path.join(documents, runtimeSidecarNames[0]);
    const raw = readOfficialRuntimeRelease(bundle).seal_bytes;
    fs.writeFileSync(sealPath, raw.toString().replace('"schema":', '"schema":"duplicate","schema":'));
    assert.throws(() => readRuntimeReleaseDocuments(documents, sha(archive)), /duplicate_key/u);
    fs.writeFileSync(sealPath, Buffer.alloc(contract.limits.seal_bytes + 1));
    assert.throws(() => readRuntimeReleaseDocuments(documents, sha(archive)), /bounded_file_required/u);
    fs.rmSync(sealPath);
    assert.throws(() => readRuntimeReleaseDocuments(documents, sha(archive)), /inventory_invalid/u);
  } finally { fs.rmSync(temporary, { recursive: true, force: true }); }
});


test("JavaScript consumes the same malformed raw seal fixtures as the C# guard", () => {
  for (const raw of golden.invalid_seal_raw) assert.throws(() => parseRuntimeSeal(raw));
});

test("existing native deployment owner restores exact prior DLL and both sidecar bytes", () => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "sts2-seal-rollback-test-"));
  try {
    const mods = path.join(temporary, "mods");
    const backup = path.join(temporary, "backup");
    fs.mkdirSync(mods);
    fs.mkdirSync(backup);
    // Execute the actual existing owner's narrow pure file functions. Do not
    // import its CLI composition root, discover the real game or fake deploy.
    const source = fs.readFileSync(path.join(import.meta.dirname, "../lifecycle.mjs"), "utf8");
    const body = (name, next) => source.slice(source.indexOf(`function ${name}(`), source.indexOf(`function ${next}(`));
    const context = vm.createContext({ fs, path, installation: { mods_dir: mods },
      installedProvenance: path.join(temporary, "installed-provenance.json") });
    vm.runInContext(body("archiveTarget", "restoreTarget") + body("restoreTarget", "validateRollbackManifest")
      + body("restoreEntries", "rollbackDirectory"), context);
    const names = ["STS2_PLATFORM.dll", ...runtimeSidecarNames];
    for (const name of names) fs.writeFileSync(path.join(mods, name), `old exact bytes: ${name}`);
    const entries = names.map((name) => context.archiveTarget(backup, "mods", path.join(mods, name)));
    // A replacement fails after DLL and first sidecar changed; restoration is
    // the same owner called by deploy's catch, rather than a test reimplementation.
    fs.writeFileSync(path.join(mods, names[0]), "new dll");
    fs.writeFileSync(path.join(mods, names[1]), "new incomplete pair");
    context.restoreEntries(backup, entries);
    for (const name of names) assert.equal(fs.readFileSync(path.join(mods, name), "utf8"), `old exact bytes: ${name}`);
    const absent = path.join(mods, "synthetic-previously-absent");
    const entry = context.archiveTarget(backup, "mods", absent);
    fs.writeFileSync(absent, "partial new file");
    context.restoreEntries(backup, [entry]);
    assert.equal(fs.existsSync(absent), false);
    assert.match(source, /\.\.\.runtimeSidecarNames/u);
    assert.match(source, /catch \(error\) \{\s*restoreEntries\(backup, entries\)/u);
  } finally { fs.rmSync(temporary, { recursive: true, force: true }); }
});
