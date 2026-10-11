import { spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

import {
  loadHostRuntimeWorkstationApi,
  resolveWorkstationInstallation
} from "../../components/annotator/tools/workstation-platform.mjs";
import { assertGameModVersions, sourceSetIdentity } from "./source-identity.mjs";
import { publicAssemblyIdentity } from "./public-provenance.mjs";

const appRoot = import.meta.dirname;
const platformRoot = path.resolve(appRoot, "../..");
assertGameModVersions(platformRoot);
const outputRoot = path.join(appRoot, "bin/Release/net9.0");
const artifact = path.join(outputRoot, "STS2_PLATFORM.dll");
const project = path.join(appRoot, "STS2Platform.GameMod.csproj");
const identityProject = path.join(
  platformRoot,
  "components/annotator/src/STS2HumanAnnotator.Tool/STS2HumanAnnotator.Tool.csproj"
);
const identityTool = path.join(
  platformRoot,
  "components/annotator/src/STS2HumanAnnotator.Tool/bin/Release/net9.0/sts2-human-annotator.dll"
);

function run(executable, args, cwd = platformRoot) {
  const result = spawnSync(executable, args, {
    cwd,
    env: process.env,
    stdio: "inherit"
  });
  if (result.status !== 0) process.exit(result.status ?? 1);
}

function sha256(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8").replace(/^\uFEFF/u, ""));
}

function exactIdentity(file) {
  const result = spawnSync("dotnet", [identityTool, "identity", file], {
    cwd: platformRoot,
    encoding: "utf8"
  });
  if (result.status !== 0) throw new Error(result.stderr || `identity tool exited with ${result.status}`);
  return publicAssemblyIdentity(JSON.parse(result.stdout));
}

const hostApi = await loadHostRuntimeWorkstationApi(
  path.join(platformRoot, "components", "annotator")
);
const installation = resolveWorkstationInstallation({ headlessApi: hostApi });
const gameDir = path.resolve(installation.game_dir);
if (!fs.existsSync(gameDir)) {
  process.stderr.write(`STS2 installation was not found at ${gameDir}; set STS2_GAME_DIR explicitly.\n`);
  process.exit(2);
}
const source = sourceSetIdentity(platformRoot);
const dataDirectory = installation.data_dir;
const releaseInfo = installation.release_info;

run("dotnet", ["build", identityProject, "--configuration", "Release"]);
run("dotnet", [
  "build", project,
  "--configuration", "Release",
  "--no-incremental",
  `-p:STS2GameDir=${gameDir}`,
  `-p:PlatformSourceRevision=${source.platform.source_revision}`,
  `-p:PlatformSourceDigestSha256=${source.platform.source_digest_sha256}`,
  `-p:NativeFoundationSourceRevision=${source.components.native_foundation.source_revision}`,
  `-p:NativeFoundationSourceDigestSha256=${source.components.native_foundation.source_digest_sha256}`,
  `-p:ConnectorSourceRevision=${source.components.connector.source_revision}`,
  `-p:ConnectorPlayerEnvironmentSourceDigest=${source.components.connector.source_digest_sha256}`,
  `-p:AnnotatorSourceRevision=${source.components.annotator.source_revision}`,
  `-p:AnnotatorSourceDigest=${source.components.annotator.source_digest_sha256}`,
  `-p:LiveUiSourceRevision=${source.components.live_ui.source_revision}`,
  `-p:LiveUiSourceDigestSha256=${source.components.live_ui.source_digest_sha256}`,
  "-p:UseSharedCompilation=false",
  `-p:PathMap=${platformRoot}=/_/sts2-ai-platform`
]);

const provenance = {
  schema: "sts2.platform/game-mod-build-provenance-1",
  built_at: new Date().toISOString(),
  platform: process.platform,
  architecture: process.arch,
  source,
  game: {
    release: readJson(releaseInfo),
    sts2: exactIdentity(path.join(dataDirectory, "sts2.dll")),
    godotsharp_sha256: sha256(path.join(dataDirectory, "GodotSharp.dll")),
    harmony_sha256: sha256(path.join(dataDirectory, "0Harmony.dll"))
  },
  artifact: exactIdentity(artifact),
  package: {
    manifest: readJson(path.join(appRoot, "mod_manifest.json")),
    files: ["STS2_PLATFORM.dll", "STS2_PLATFORM.json"]
  }
};
fs.mkdirSync(outputRoot, { recursive: true });
fs.copyFileSync(path.join(appRoot, "mod_manifest.json"), path.join(outputRoot, "STS2_PLATFORM.json"));
fs.writeFileSync(
  path.join(outputRoot, "build-provenance.json"),
  `${JSON.stringify(provenance, null, 2)}\n`
);
process.stdout.write(`${JSON.stringify(provenance, null, 2)}\n`);
