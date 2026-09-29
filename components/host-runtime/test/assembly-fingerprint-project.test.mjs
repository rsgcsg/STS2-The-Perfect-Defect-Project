import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { stageAssemblyFingerprintProject } from "../src/assembly-fingerprint-project.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("fingerprint source stages outside the immutable package and cleans its build root", () => {
  const staged = stageAssemblyFingerprintProject(ROOT);
  try {
    assert.equal(staged.directory.startsWith(`${ROOT}${path.sep}`), false);
    for (const name of ["AssemblyFingerprint.csproj", "Program.cs"]) {
      assert.deepEqual(readFileSync(path.join(staged.directory, name)),
        readFileSync(path.join(ROOT, "tools", "dotnet", "AssemblyFingerprint", name)));
    }
  } finally {
    staged.cleanup();
  }
  assert.equal(existsSync(staged.directory), false);
});
