import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { resolveCliPath } from "./cli-paths.mjs";
import { semVerAtLeast } from "../../../tools/check-platform-bom.mjs";

const root = path.resolve(import.meta.dirname, "..");

test("evidence CLI paths are resolved from the caller working directory", () => {
  const caller = path.join(path.parse(root).root, "workspace");

  assert.equal(
    resolveCliPath("components/annotator/.local/recordings/session-1", caller),
    path.join(caller, "components/annotator/.local/recordings/session-1")
  );
  assert.equal(resolveCliPath(path.join(caller, "absolute"), root), path.join(caller, "absolute"));
});

test("Annotator CLI exposes portable and exact-game entry points", () => {
  const result = spawnSync(process.execPath, [path.join(import.meta.dirname, "annotator.mjs"), "--help"], {
    encoding: "utf8"
  });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /Exact-game lifecycle:/u);
  assert.match(result.stdout, /pack-session/u);
});

test("Annotator package and native Mod agree; Connector dependency is a valid minimum", () => {
  const packageMetadata = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8"));
  const manifest = JSON.parse(fs.readFileSync(
    path.join(root, "src", "STS2HumanAnnotator.Mod", "mod_manifest.json"),
    "utf8"
  ));
  const contracts = fs.readFileSync(
    path.join(root, "src", "STS2HumanAnnotator.Core", "CurrentContracts.cs"),
    "utf8"
  );
  const connectorManifest = JSON.parse(fs.readFileSync(
    path.join(root, "..", "connector", "host", "mod_manifest.json"),
    "utf8"
  ));
  const nativeVersion = contracts.match(/public const string ProductVersion = "([^"]+)";/u)?.[1];
  const connectorDependencies = manifest.dependencies.filter(({ id }) => id === "STS2_MCP");

  assert.equal(manifest.version, packageMetadata.version);
  assert.equal(nativeVersion, packageMetadata.version);
  assert.equal(connectorDependencies.length, 1);
  assert.equal(semVerAtLeast(connectorManifest.version, connectorDependencies[0].min_version), true);
  assert.equal(semVerAtLeast(connectorManifest.version, "1.3.0-rc.10"), false);
  assert.equal(semVerAtLeast(connectorManifest.version, "1.3.0-rc.06"), false);
});
