import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { componentGitState } from "../../../tools/component-git.mjs";
import { createDeterministicTarGzip } from "./deterministic-archive.mjs";
import { assertConnectorVersions } from "./connector-versions.mjs";

const root = process.cwd();
function npmExecFileSync(args, options) {
  const npmExecPath = process.env.npm_execpath;
  if (typeof npmExecPath === "string" && npmExecPath.length > 0) {
    return execFileSync(process.execPath, [npmExecPath, ...args], options);
  }
  return execFileSync(process.platform === "win32" ? "npm.cmd" : "npm", args, {
    ...options,
    shell: process.platform === "win32"
  });
}

const workspace = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8"));
const release = JSON.parse(fs.readFileSync(path.join(root, "release-manifest.json"), "utf8"));
const version = workspace.version;
const modManifest = JSON.parse(fs.readFileSync(path.join(root, "host", "mod_manifest.json"), "utf8"));
const connectorMod = fs.readFileSync(path.join(root, "host", "ConnectorMod.cs"), "utf8");
assertConnectorVersions({ packageVersion: version, releaseVersion: release.release.version,
  modManifestVersion: modManifest.version, nativeSource: connectorMod });
const source = componentGitState(root);
if (source.componentWorktreeStatus !== "clean") {
  throw new Error("Release packaging requires a clean Connector component worktree");
}
const head = source.componentSourceRevision;
let taggedHead = null;
try {
  taggedHead = execFileSync("git", ["rev-list", "-n", "1", `connector/v${version}`], {
    cwd: root,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "ignore"]
  }).trim() || null;
} catch {
  // A not-yet-tagged version may be packaged for its pre-publication gates.
}
if (taggedHead && taggedHead !== head) {
  throw new Error(
    `Release connector/v${version} is immutable at ${taggedHead}; bump the version before packaging ${head}`
  );
}

const hostOut = path.join(root, "host", "out", "STS2_MCP");
const identityPath = path.join(hostOut, "build-identity.json");
if (!fs.existsSync(identityPath)) throw new Error("Run npm run build before packaging a release");
const identity = JSON.parse(fs.readFileSync(identityPath, "utf8"));
if (identity.source_revision !== head
    || identity.source_worktree_status !== "clean") {
  throw new Error("Host build identity is not a clean build of the current commit");
}
const pdbPath = path.join(hostOut, "STS2_MCP.pdb");
if (fs.existsSync(pdbPath)
    && fs.readFileSync(pdbPath).includes(Buffer.from("raw.githubusercontent.com/rsgcsg/STS2-AI-PLATFORM/"))) {
  throw new Error("Host PDB embeds workspace SourceLink; the binary is not component-reproducible");
}

const releaseRoot = path.join(root, ".local", "release", `v${version}`);
const stageRoot = path.join(releaseRoot, "stage");
const payloadRoot = path.join(stageRoot, "payload");
const toolsRoot = path.join(stageRoot, "tools");
fs.rmSync(releaseRoot, { recursive: true, force: true });
fs.mkdirSync(payloadRoot, { recursive: true });
fs.mkdirSync(toolsRoot, { recursive: true });

for (const name of ["STS2_MCP.dll", "STS2_MCP.pdb", "STS2_MCP.deps.json", "build-identity.json"]) {
  const source = path.join(hostOut, name);
  if (fs.existsSync(source)) fs.copyFileSync(source, path.join(payloadRoot, name));
}
fs.copyFileSync(path.join(root, "host", "mod_manifest.json"), path.join(payloadRoot, "STS2_MCP.json"));
for (const name of ["install-release.mjs", "verify-release.mjs", "steam-paths.mjs"]) {
  fs.copyFileSync(path.join(root, "tools", name), path.join(toolsRoot, name));
}
fs.copyFileSync(path.join(root, "docs", "INSTALLATION.md"), path.join(stageRoot, "INSTALL.md"));
fs.copyFileSync(path.join(root, "contracts", "player-environment-contract.json"), path.join(stageRoot, "player-environment-contract.json"));
fs.copyFileSync(path.join(root, "contracts", "host-compatibility.json"), path.join(stageRoot, "host-compatibility.json"));
fs.copyFileSync(path.join(root, "release-manifest.json"), path.join(stageRoot, "release-manifest.json"));

for (const required of [
  path.join(payloadRoot, "STS2_MCP.dll"),
  path.join(payloadRoot, "STS2_MCP.json"),
  path.join(payloadRoot, "build-identity.json"),
  path.join(toolsRoot, "install-release.mjs"),
  path.join(toolsRoot, "verify-release.mjs")
]) {
  if (!fs.existsSync(required)) throw new Error(`Release layout is missing ${path.relative(stageRoot, required)}`);
}

const hostArchive = `STS2-Connector-${version}-host.tar.gz`;
createDeterministicTarGzip(stageRoot, path.join(releaseRoot, hostArchive));
const packOutput = npmExecFileSync(
  ["pack", "--pack-destination", releaseRoot, "--json"],
  { cwd: path.join(root, "sdk", "typescript"), encoding: "utf8" }
);
const packReport = JSON.parse(packOutput)[0];
if (packReport?.name !== "@rsgcsg/sts2-connector-client") {
  throw new Error(`Release SDK pack resolved the wrong package: ${packReport?.name ?? "missing"}`);
}
const sdkArchive = packReport.filename;

function sha256(file) {
  return createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

const assets = [
  hostArchive,
  sdkArchive,
  "release-manifest.json",
  "player-environment-contract.json",
  "host-compatibility.json"
];
fs.copyFileSync(path.join(root, "release-manifest.json"), path.join(releaseRoot, "release-manifest.json"));
fs.copyFileSync(
  path.join(root, "contracts", "player-environment-contract.json"),
  path.join(releaseRoot, "player-environment-contract.json")
);
fs.copyFileSync(
  path.join(root, "contracts", "host-compatibility.json"),
  path.join(releaseRoot, "host-compatibility.json")
);
const checksums = assets
  .map((name) => `${sha256(path.join(releaseRoot, name))}  ${name}`)
  .join("\n") + "\n";
fs.writeFileSync(path.join(releaseRoot, "checksums.sha256"), checksums);

console.log(JSON.stringify({
  status: "release_assets_ready",
  source_revision: head,
  workspace_revision: source.workspaceRevision,
  component_tree_revision: source.componentTreeRevision,
  release: version,
  protocol: release.player_environment.protocol,
  host_artifact_sha256: identity.artifact_sha256,
  host_artifact_mvid: identity.artifact_mvid,
  output: releaseRoot,
  assets: [...assets, "checksums.sha256"]
}, null, 2));
