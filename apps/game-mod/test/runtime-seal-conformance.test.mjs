import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import path from "node:path";
import test from "node:test";

test("C# runtime-seal guard consumes shared raw JSON golden and rejects drift", () => {
  const result = spawnSync("dotnet", [
    "run", "--project", path.join(import.meta.dirname, "runtime-seal-conformance/RuntimeSeal.Conformance.csproj"),
    "--configuration", "Release", "--", path.join(import.meta.dirname, "fixtures/runtime-seal-golden.json")
  ], { encoding: "utf8", timeout: 60000 });
  assert.equal(result.status, 0, result.stderr || result.stdout || String(result.error));
  assert.match(result.stdout, /C# shared runtime-seal contract conformance passed/u);
});
