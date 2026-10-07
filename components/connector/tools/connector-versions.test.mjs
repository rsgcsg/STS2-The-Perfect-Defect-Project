import assert from "node:assert/strict";
import test from "node:test";
import { assertConnectorVersions, evaluateConnectorVersions } from "./connector-versions.mjs";

const source = version => `namespace STS2Connector;\npublic static partial class ConnectorMod\n{\n    public const string Version = "${version}";\n}\n`;
const candidate = () => ({ packageVersion: "1.3.0-rc.10", releaseVersion: "1.3.0-rc.10",
  modManifestVersion: "1.3.0-rc.10", nativeSource: source("1.3.0-rc.10") });

test("matching product declarations pass without requiring SDK or protocol equality", () => {
  const versions = { ...candidate(), sdkVersion: "1.3.0-rc.7", protocolVersion: "1.0.0" };
  assert.deepEqual(evaluateConnectorVersions(versions), { ok: true, nativeVersion: "1.3.0-rc.10", errors: [] });
  assert.equal(assertConnectorVersions(versions).ok, true);
});

test("historical rc.8 packaging with native rc.6 is a real product mismatch", () => {
  const versions = { packageVersion: "1.3.0-rc.8", releaseVersion: "1.3.0-rc.8",
    modManifestVersion: "1.3.0-rc.8", nativeSource: source("1.3.0-rc.6") };
  assert.deepEqual(evaluateConnectorVersions(versions).errors,
    ["Native implementation version 1.3.0-rc.6 does not match package version 1.3.0-rc.8"]);
  assert.throws(() => assertConnectorVersions(versions), /Native implementation version/);
});

for (const field of ["releaseVersion", "modManifestVersion"]) {
  test(`${field} mismatch fails both portable evaluation and release assertion`, () => {
    const versions = { ...candidate(), [field]: "1.3.0-rc.9" };
    assert.equal(evaluateConnectorVersions(versions).ok, false);
    assert.throws(() => assertConnectorVersions(versions), /does not match package version/);
  });
}

test("missing, nonliteral, commented or duplicate native declarations fail closed", () => {
  for (const nativeSource of ["", 'public const string Version = BuildVersion;',
    '// public const string Version = "1.3.0-rc.10";',
    '/*\npublic const string Version = "1.3.0-rc.10";\n*/',
    source("1.3.0-rc.10") + source("1.3.0-rc.9")]) {
    const versions = { ...candidate(), nativeSource };
    assert.equal(evaluateConnectorVersions(versions).nativeVersion, null);
    assert.throws(() => assertConnectorVersions(versions), /exactly one literal declaration/);
  }
});

test("missing product identity cannot pass by matching absent fields", () => {
  assert.equal(evaluateConnectorVersions({ nativeSource: "" }).ok, false);
  assert.throws(() => assertConnectorVersions({ nativeSource: source("1.3.0-rc.10") }), /package version is missing/);
});
