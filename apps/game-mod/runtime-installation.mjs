import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { parseRuntimeSeal, parseReleaseProvenance, readOfficialRuntimeRelease } from "./runtime-release.mjs";
import { sourceSetIdentity, sourceSetMatches } from "./source-identity.mjs";

export const runtimeSidecarNames = Object.freeze([
  "STS2_PLATFORM.runtime-seal.json", "STS2_PLATFORM.release-provenance.json"
]);
const preparedNames = [...runtimeSidecarNames, "runtime-evidence.json", "checksums.sha256"];
const hash = (raw) => crypto.createHash("sha256").update(raw).digest("hex");
const fail = (code) => { throw new Error(code); };

function regularBytes(file, limit) {
  const descriptor = fs.openSync(file, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW ?? 0));
  try {
    const stat = fs.fstatSync(descriptor);
    if (!stat.isFile() || stat.size <= 0 || stat.size > limit || fs.lstatSync(file).isSymbolicLink())
      fail("runtime_release_regular_bounded_file_required");
    const buffer = Buffer.alloc(limit + 1);
    let length = 0;
    while (length < buffer.length) {
      const count = fs.readSync(descriptor, buffer, length, buffer.length - length, null);
      if (count === 0) break;
      length += count;
    }
    if (length > limit) fail("runtime_release_file_too_large");
    return buffer.subarray(0, length);
  } finally { fs.closeSync(descriptor); }
}

export function verifyNativeSealBinding(identity, seal) {
  const native = identity?.provenance;
  const manifest = identity?.manifest;
  const host = seal.host;
  const environment = seal.environment;
  const pairs = [
    [identity?.status, "verified_runtime_archive_identity"],
    [identity?.archive_sha256, seal.release.archive_sha256],
    [native?.schema, "sts2.platform/game-mod-build-provenance-1"],
    [manifest?.id, "STS2_PLATFORM"], [manifest?.version, seal.release.version],
    [native?.artifact?.sha256, host.artifact_sha256],
    [native?.artifact?.module_version_id, host.artifact_mvid],
    [native?.source?.platform?.source_revision, host.platform_source_revision],
    [native?.source?.platform?.source_digest_sha256, host.compiled_source_digest_sha256],
    [native?.source?.components?.connector?.source_revision, host.connector_source_revision],
    [native?.platform, environment.platform], [native?.architecture, environment.architecture],
    [native?.game?.release?.version, environment.game_version],
    [native?.game?.release?.commit, environment.game_commit],
    // release_info.main_assembly_hash is declared metadata, not the runtime
    // AssemblyHasher value. Only bootstrap observation and the loaded Host
    // can bind that runtime field; do not derive it from package metadata.
    [native?.game?.sts2?.sha256, environment.main_assembly_sha256],
    [native?.game?.sts2?.module_version_id, environment.main_assembly_mvid]
  ];
  if (pairs.some(([actual, expected]) => actual !== expected)) fail("runtime_seal_archive_native_binding_mismatch");
  return identity;
}

export function verifyRuntimeArchive(python, tool, archive, sha256) {
  const result = spawnSync(python, ["-I", tool, "verify-runtime-archive", "--archive", archive,
    "--sha256", sha256], { encoding: "utf8", timeout: 20000, maxBuffer: 1048576 });
  if (result.error || result.status !== 0) fail("runtime_archive_verification_failed");
  return JSON.parse(result.stdout);
}

// Only the opaque bundle returned by the fixed official fetch can enter this
// publication path. A caller-supplied receipt, hash or boolean is insufficient.
export function publishPreparedRuntimeRelease(bundle, directory, identity) {
  const release = readOfficialRuntimeRelease(bundle);
  verifyNativeSealBinding(identity, release.seal);
  if (path.basename(directory) !== release.digests.archive || path.resolve(directory) !== directory
      || fs.lstatSync(directory).isSymbolicLink()) fail("runtime_release_prepared_root_invalid");
  const archive = regularBytes(path.join(directory, "package.zip"), 268435456);
  if (hash(archive) !== release.digests.archive) fail("runtime_release_prepared_archive_changed");
  const destination = path.join(directory, "runtime-release");
  if (fs.existsSync(destination)) fail("runtime_release_already_published");
  const temporary = fs.mkdtempSync(path.join(directory, ".runtime-release-"));
  try {
    fs.writeFileSync(path.join(temporary, runtimeSidecarNames[0]), release.seal_bytes, { flag: "wx", mode: 0o600 });
    fs.writeFileSync(path.join(temporary, runtimeSidecarNames[1]), release.provenance_bytes, { flag: "wx", mode: 0o600 });
    fs.writeFileSync(path.join(temporary, preparedNames[2]), release.evidence, { flag: "wx", mode: 0o600 });
    fs.writeFileSync(path.join(temporary, preparedNames[3]), release.checksums, { flag: "wx", mode: 0o600 });
    fs.renameSync(temporary, destination);
  } finally { fs.rmSync(temporary, { recursive: true, force: true }); }
  return { status: "official_runtime_release_prepared", directory, version: release.version,
    release_id: release.release_id, archive_sha256: release.digests.archive,
    non_claims: ["not_installed", "not_loaded", "ordinary_launch_matrix_required"] };
}

// Parsing these local documents is identity checking, not an official fetch
// receipt. Only publishPreparedRuntimeRelease accepts the opaque fetch bundle.
export function readRuntimeReleaseDocuments(documents, archiveSha256) {
  if (fs.lstatSync(documents).isSymbolicLink() || !fs.statSync(documents).isDirectory()
      || fs.readdirSync(documents).sort().join("\0") !== [...preparedNames].sort().join("\0"))
    fail("runtime_release_document_inventory_invalid");
  const sealBytes = regularBytes(path.join(documents, runtimeSidecarNames[0]), 65536);
  const provenanceBytes = regularBytes(path.join(documents, runtimeSidecarNames[1]), 65536);
  const seal = parseRuntimeSeal(sealBytes);
  const provenance = parseReleaseProvenance(provenanceBytes);
  const evidence = regularBytes(path.join(documents, preparedNames[2]), 262144);
  if (provenance.archive_sha256 !== archiveSha256
      || seal.release.archive_sha256 !== provenance.archive_sha256
      || provenance.seal_sha256 !== hash(sealBytes)
      || provenance.version !== seal.release.version || provenance.tag !== seal.release.tag
      || provenance.evidence_sha256 !== hash(evidence)
      || seal.evidence.record_sha256 !== provenance.evidence_sha256)
    fail("runtime_release_prepared_binding_mismatch");
  const checksums = regularBytes(path.join(documents, preparedNames[3]), 65536).toString("utf8");
  const rows = checksums.replaceAll("\r\n", "\n").split("\n");
  if (rows.at(-1) === "") rows.pop();
  rows.sort();
  const expected = [[provenance.archive_sha256, provenance.archive_asset],
    [provenance.seal_sha256, provenance.seal_asset], [provenance.evidence_sha256, provenance.evidence_asset]]
    .map(([digest, name]) => `${digest}  ${name}`).sort();
  if (JSON.stringify(rows) !== JSON.stringify(expected)) fail("runtime_release_prepared_checksums_changed");
  return { seal, provenance, sealBytes, provenanceBytes };
}

export function readPreparedRuntimeSeal(platformRoot, build) {
  const directory = path.dirname(platformRoot);
  const documents = path.join(directory, "runtime-release");
  if (!fs.existsSync(documents)) return null;
  if (path.basename(platformRoot) !== "source" || !/^[0-9a-f]{64}$/u.test(path.basename(directory))
      || fs.lstatSync(directory).isSymbolicLink() || fs.lstatSync(documents).isSymbolicLink()
      || fs.readdirSync(documents).sort().join("\0") !== [...preparedNames].sort().join("\0"))
    fail("runtime_release_prepared_root_invalid");
  const { seal, provenance, sealBytes, provenanceBytes } =
    readRuntimeReleaseDocuments(documents, path.basename(directory));
  const python = path.join(platformRoot, "python/.venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
  const identity = verifyRuntimeArchive(python, path.join(platformRoot, "python/tools/install_developer_kit.py"),
    path.join(directory, "package.zip"), provenance.archive_sha256);
  verifyNativeSealBinding(identity, seal);
  const current = sourceSetIdentity(platformRoot);
  if (current.platform.workspace_worktree_status !== "clean" || !sourceSetMatches(build.source, current))
    fail("runtime_release_prepared_source_changed");
  verifyNativeSealBinding({ ...identity, manifest: build.manifest ?? identity.manifest,
    provenance: { ...build, schema: "sts2.platform/game-mod-build-provenance-1",
      platform: process.platform, architecture: process.arch } }, seal);
  return { seal, provenance, sealBytes, provenanceBytes };
}

export function verifyInstalledRuntimePair(modsDirectory, prepared) {
  const actual = runtimeSidecarNames.map((name) => path.join(modsDirectory, name));
  if (!prepared) {
    if (actual.some((file) => fs.existsSync(file))) fail("unexpected_installed_runtime_seal");
    return;
  }
  for (const [index, expected] of [prepared.sealBytes, prepared.provenanceBytes].entries())
    if (!regularBytes(actual[index], 65536).equals(expected)) fail("installed_runtime_seal_drift");
}
