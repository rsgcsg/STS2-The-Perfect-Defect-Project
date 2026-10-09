import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "..");

function packageJson(relative = ".") {
  return JSON.parse(fs.readFileSync(path.join(root, relative, "package.json"), "utf8"));
}

test("nested npm Host entrypoints preserve the script argument boundary", () => {
  const workspace = packageJson();
  const hostScripts = Object.entries(workspace.scripts)
    .filter(([name]) => name.startsWith("host:"));

  assert.ok(hostScripts.length > 0);
  for (const [name, command] of hostScripts) {
    if (command.startsWith("npm --prefix components/host-runtime run ")) {
      assert.match(command, / --$/u, `${name} must forward caller arguments after npm's -- separator`);
    }
  }
});

test("every nested npm workspace script targets an existing package script", () => {
  const workspace = packageJson();
  const cache = new Map();
  const references = [];
  for (const [rootScript, command] of Object.entries(workspace.scripts)) {
    for (const match of command.matchAll(/\bnpm\s+--prefix\s+([^\s]+)\s+run\s+([A-Za-z0-9:_-]+)/gu)) {
      references.push({ rootScript, prefix: match[1], script: match[2] });
    }
  }

  assert.ok(references.length > 0);
  for (const { rootScript, prefix, script } of references) {
    let target = cache.get(prefix);
    if (!target) {
      const targetPath = path.join(root, prefix, "package.json");
      assert.ok(fs.existsSync(targetPath), `${rootScript} targets missing package ${prefix}`);
      target = JSON.parse(fs.readFileSync(targetPath, "utf8"));
      cache.set(prefix, target);
    }
    assert.equal(
      typeof target.scripts?.[script],
      "string",
      `${rootScript} targets missing ${prefix} script ${script}`
    );
  }
});

test("workspace package test entrypoints do not require shell glob expansion", () => {
  for (const [relative, script] of [
    ["apps/game-mod", "check"],
    ["apps/ingame-ui", "check"],
    ["apps/workbench", "test"]
  ]) {
    const target = packageJson(relative);
    assert.equal(target.scripts[script], "node --test", `${relative}/package.json`);
  }
});

test("exact-game validation builds the Connector before the dependent Annotator", () => {
  const workspace = packageJson();
  const command = workspace.scripts["check:exact-game"];
  const connectorBuild = command.indexOf("npm --prefix components/connector run build");
  const annotatorCheck = command.indexOf("npm --prefix components/annotator run check");

  assert.ok(connectorBuild >= 0, "exact-game validation must produce the exact Connector artifact");
  assert.ok(annotatorCheck > connectorBuild,
    "Annotator validation must consume the already-built Connector artifact");
});


test("S0 and native collection regressions participate in full and Python owner gates", () => {
  const { scripts } = packageJson();
  for (const name of ["check", "check:python-scope"]) {
    assert.ok(scripts[name].includes("npm run check:s0"), `${name} omits S0 consumers`);
    assert.ok(scripts[name].indexOf("npm run check:s0") < scripts[name].indexOf("npm run check:python"),
      `${name} must fail fast on S0 consumers before the long Python suite`);
  }
  assert.ok(scripts["precheck:s0"].includes("components/connector/sdk/typescript run build"));
  assert.ok(scripts["precheck:s0"].includes("components/policy-runtime run build"),
    "native collection requires the public Runtime build in a fresh Python owner gate");
  assert.ok(scripts["precheck:s0"].indexOf("components/connector/sdk/typescript run build")
    < scripts["precheck:s0"].indexOf("components/policy-runtime run build"),
  "the Runtime build consumes the preceding SDK declarations");
  assert.ok(scripts["check:s0"].includes("tools/test/baseline-s0-runner.test.mjs"));
  assert.ok(scripts["check:s0"].includes("tools/test/native-source3-collector.test.mjs"),
    "native collection lifecycle regressions must run in the selected portable gates");
  assert.ok(scripts["check:s0"].includes("tools/test/native-source3-collector-sdk.test.mjs"),
    "native collection must exercise the actual SDK request and reply grammar");
  assert.ok(scripts["check:s0"].includes("tools/test/test_baseline_s0_dataset.py"));
});
