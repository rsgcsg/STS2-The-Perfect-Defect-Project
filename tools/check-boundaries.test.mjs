import assert from "node:assert/strict";
import test from "node:test";
import { packageEntrypointErrors, textBoundaryErrors } from "./check-boundaries.mjs";
import { managedSelectorArtifact, managedSelectorArtifactErrors, generatedPath } from "./generate-managed-selector-validator.mjs";
import { mkdtempSync, mkdirSync, copyFileSync, writeFileSync, rmSync } from "node:fs";
import path from "node:path";
import os from "node:os";

test("packaged Managed validator drift rejects without changing the canonical contract", () => {
  const temporary = mkdtempSync(path.join(os.tmpdir(), "selector-contract-artifact-"));
  try {
    for (const name of ["nativeCardSelection.ts", "json.ts"]) {
      const relative = `components/connector/sdk/typescript/src/${name}`;
      mkdirSync(path.dirname(path.join(temporary, relative)), { recursive: true });
      copyFileSync(new URL(`../${relative}`, import.meta.url), path.join(temporary, relative));
    }
    const artifact = path.join(temporary, generatedPath);
    mkdirSync(path.dirname(artifact), { recursive: true });
    writeFileSync(artifact, managedSelectorArtifact(temporary));
    assert.deepEqual(managedSelectorArtifactErrors(temporary), []);
    writeFileSync(artifact, "export function nativeCardSelectionProblems() { return []; }\n");
    assert.match(managedSelectorArtifactErrors(temporary)[0], /stale/u);
  } finally { rmSync(temporary, { recursive: true, force: true }); }
});

test("active production code rejects predecessor release authority", () => {
  const errors = textBoundaryErrors(
    "components/host-runtime/src/release.mjs",
    'const url = "https://github.com/rsgcsg/STS2-Connector/releases/download/v1/file";'
  );
  assert.equal(errors.length, 1);
  assert.match(errors[0], /predecessor/u);
});

test("migration provenance may name a predecessor repository", () => {
  assert.deepEqual(textBoundaryErrors(
    "migration/source-manifest.json",
    '"url": "https://github.com/rsgcsg/STS2-Connector"'
  ), []);
});

test("production code rejects user-specific paths and reverse component imports", () => {
  assert.equal(textBoundaryErrors(
    "components/connector/tools/bad.mjs",
    'import value from "../../host-runtime/src/value.mjs"; const root = "/Users/dev/project";'
  ).length, 2);
});

test("declared package entrypoints must exist in the Git source authority", () => {
  const packageJson = { bin: { "fixture-tool": "bin/tool.mjs" } };
  assert.deepEqual(packageEntrypointErrors(
    "apps/fixture/package.json",
    packageJson,
    new Set(),
    () => true
  ), ["apps/fixture/package.json: bin target is not tracked: apps/fixture/bin/tool.mjs"]);
  assert.deepEqual(packageEntrypointErrors(
    "apps/fixture/package.json",
    packageJson,
    new Set(["apps/fixture/bin/tool.mjs"]),
    () => false
  ), ["apps/fixture/package.json: bin target is missing: apps/fixture/bin/tool.mjs"]);
  assert.deepEqual(packageEntrypointErrors(
    "apps/fixture/package.json",
    packageJson,
    new Set(["apps/fixture/bin/tool.mjs"]),
    () => true
  ), []);
});
