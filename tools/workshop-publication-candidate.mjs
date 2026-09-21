import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { sourceSetIdentity } from "../apps/game-mod/source-identity.mjs";
import { PAYLOAD, regularFile, safePath, sha256, inspectWorkshopBuild } from "./workshop-stage.mjs";
import { verifyToolEvolution } from "./workshop-finalize.mjs";
import { fileInventory, git } from "./workshop-uploader.mjs";

const TOOL_FILES = new Set(["tools/workshop-uploader.mjs", "tools/workshop-publication-candidate.mjs",
  "tools/workshop-publish.mjs", "tools/workshop-publish.test.mjs", "tools/workshop-uploader.test.mjs",
  "tools/workshop-boundary.test.mjs", "README.md", "workshop/README.md", "package.json"]);
export function verifyPublicationEvolution(repo, preparedHead, currentHead) {
  git(repo, ["merge-base", "--is-ancestor", preparedHead, currentHead]);
  const changed = git(repo, ["diff", "--name-only", "--no-renames", preparedHead, currentHead]).split(/\r?\n/u).filter(Boolean);
  for (const name of changed) assert.ok(TOOL_FILES.has(name), `publication_source_drift:${name}`);
  if (changed.includes("package.json")) {
    const before = JSON.parse(git(repo, ["show", `${preparedHead}:package.json`]));
    const after = JSON.parse(regularFile(path.join(repo, "package.json")));
    assert.equal(after.scripts["workshop:uploader"], "node tools/workshop-uploader.mjs");
    assert.equal(after.scripts["workshop:publish"], "node tools/workshop-publish.mjs");
    delete after.scripts["workshop:uploader"]; delete after.scripts["workshop:publish"];
    after.scripts["check:boundaries"] = after.scripts["check:boundaries"].replace(" tools/workshop-publish.test.mjs tools/workshop-uploader.test.mjs", "");
    assert.deepEqual(after, before, "unapproved_package_evolution");
  }
  return changed;
}
const read = (file) => JSON.parse(regularFile(file));
const inventory = (directory, names) => names.map((name) => {
  const b = regularFile(path.join(directory, name)); return { name, size_bytes: b.length, sha256: sha256(b) };
});
// Read-only consumer. Never restage, rewrite a receipt or calculate a new approval.
export function verifyPrepared({ repositoryRoot, preparedRoot, approvedProvenanceSha256, preparedReceiptSha256,
  readSource = sourceSetIdentity, inspect = inspectWorkshopBuild,
  verifyPrepareEvolution = verifyToolEvolution, verifyEvolution = verifyPublicationEvolution }) {
  const repo = safePath(repositoryRoot), candidate = safePath(preparedRoot);
  const w = path.join(candidate, "workshop");
  assert.match(preparedReceiptSha256 ?? "", /^[a-f0-9]{64}$/u, "prepared_receipt_pin_required");
  assert.match(approvedProvenanceSha256 ?? "", /^[a-f0-9]{64}$/u, "provenance_pin_required");
  const receiptBytes = regularFile(path.join(w, "prepare-receipt.json"));
  assert.equal(sha256(receiptBytes), preparedReceiptSha256, "prepare_receipt_drift");
  const r = JSON.parse(receiptBytes), p = read(path.join(w, "build-proposal.json"));
  assert.equal(r.result, "PREPARED_CANDIDATE");
  assert.equal(p.schema, "spireagent/workshop-build-proposal-1");
  assert.equal(p.result, "AWAITING_APPROVAL"); assert.equal(p.approved, false);
  assert.equal(p.evidence_level, "checked_build_proposal_only");
  assert.equal(p.provenance_sha256, approvedProvenanceSha256, "approved_provenance_mismatch");
  const preparedSource = readSource(candidate), current = readSource(repo);
  assert.equal(preparedSource.platform.workspace_worktree_status, "clean");
  assert.equal(current.platform.workspace_worktree_status, "clean");
  assert.equal(preparedSource.platform.workspace_revision, r.prepare_workspace_revision, "prepare_tool_revision_drift");
  assert.equal(p.workspace_revision, p.source.platform.workspace_revision);
  for (const actual of [preparedSource, current]) {
    assert.deepEqual(actual.components, p.source.components, "compiled_source_drift");
    assert.deepEqual({ ...actual.platform, workspace_revision: p.workspace_revision }, p.source.platform);
  }
  const preparationPaths = verifyPrepareEvolution(candidate, p.workspace_revision, r.prepare_workspace_revision);
  const publicationPaths = verifyEvolution(repo, r.prepare_workspace_revision, current.platform.workspace_revision);
  const source = path.join(candidate, "apps/game-mod/bin/Release/net9.0");
  assert.equal(path.resolve(p.source_directory), source);
  assert.deepEqual(inventory(source, fs.readdirSync(source).sort()), p.build_inventory, "build_inventory_drift");
  assert.equal(sha256(regularFile(path.join(source, "build-provenance.json"))), approvedProvenanceSha256);
  const verified = inspect({ repositoryRoot: repo, sourceDirectory: source, readSource,
    identityTool: path.join(candidate, "components/annotator/src/STS2HumanAnnotator.Tool/bin/Release/net9.0/sts2-human-annotator.dll") });
  assert.deepEqual(verified.provenance.source, p.source);
  assert.deepEqual(verified.actual, p.artifact);
  assert.deepEqual(verified.provenance.game, p.game);
  assert.equal(verified.provenance.platform, p.build_platform);
  assert.equal(verified.provenance.architecture, p.build_architecture);
  assert.equal(verified.manifest.version, p.game_mod_version);
  assert.equal(sha256(verified.manifestBytes), p.manifest_sha256);
  for (const name of ["workshop.json", "image.png"]) {
    const bytes = regularFile(path.join(w, name));
    assert.equal(sha256(bytes), p.metadata_sha256[name], `metadata_drift:${name}`);
    assert.ok(bytes.equals(regularFile(path.join(repo, "workshop", name))), `current_metadata_drift:${name}`);
  }
  assert.equal(read(path.join(w, "workshop.json")).visibility, "private", "private_visibility_required");
  const allowed = [".gitignore", "README.md", "workshop.json", "image.png", "content", "build-proposal.json", "staging-receipt.json", "prepare-receipt.json", "mod_id.txt", ".publication"];
  for (const name of fs.readdirSync(w)) {
    assert.ok(allowed.includes(name), `unexpected_workspace_file:${name}`);
    safePath(path.join(w, name));
  }
  assert.deepEqual(fs.readdirSync(path.join(w, "content")).sort(), [...PAYLOAD].sort(), "payload_inventory_drift");
  assert.deepEqual(inventory(path.join(w, "content"), PAYLOAD), p.proposed_payload);
  for (const name of PAYLOAD) assert.ok(regularFile(path.join(w, "content", name)).equals(regularFile(path.join(source, name))), "source_staged_bytes_differ");
  const stagingBytes = regularFile(path.join(w, "staging-receipt.json"));
  const staging = JSON.parse(stagingBytes);
  assert.deepEqual(staging, { schema: "spireagent/workshop-stage-1", result: "staged_candidate",
    workspace_revision: r.prepare_workspace_revision, producer_workspace_revision: p.workspace_revision,
    source: p.source, game_mod_version: p.game_mod_version, source_artifact: path.join(source, PAYLOAD[0]),
    artifact: p.artifact, provenance_sha256: approvedProvenanceSha256, manifest_sha256: p.manifest_sha256,
    game: p.game, build_platform: p.build_platform, build_architecture: p.build_architecture,
    inventory: p.proposed_payload, excluded_build_files: verified.entries.filter((name) => !PAYLOAD.includes(name)),
    evidence_level: "byte_preserving_staging_only" }, "staging_receipt_drift");
  assert.deepEqual(r, { schema: "spireagent/workshop-prepare-1", result: "PREPARED_CANDIDATE",
    evidence_level: "approved_byte_preserving_workshop_candidate_only",
    producer_workspace_revision: p.workspace_revision, prepare_workspace_revision: preparedSource.platform.workspace_revision,
    orchestration_changed_paths: preparationPaths, source: p.source,
    proposal_sha256: sha256(regularFile(path.join(w, "build-proposal.json"))), approved_provenance_sha256: approvedProvenanceSha256,
    artifact: p.artifact, manifest_sha256: p.manifest_sha256, metadata_sha256: p.metadata_sha256,
    game_mod_version: p.game_mod_version, game: p.game, build_platform: p.build_platform,
    build_architecture: p.build_architecture, inventory: p.proposed_payload,
    staging_receipt_schema: staging.schema, staging_receipt_sha256: sha256(stagingBytes) }, "prepared_contract_mismatch");
  return { receipt: r, prepared_receipt_sha256: preparedReceiptSha256,
    publication_workspace_revision: current.platform.workspace_revision, publication_changed_paths: publicationPaths,
    workspace: w, payload: fileInventory(path.join(w, "content")) };
}
