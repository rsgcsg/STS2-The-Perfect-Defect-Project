import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { assertGameModVersions, evaluateGameModVersions } from "../source-identity.mjs";

const root = path.resolve(import.meta.dirname, "../../..");
const fixture = () => ({
  packageVersion: "0.2.0-rc.33", manifestVersion: "0.2.0-rc.33",
  nativeSource: 'public const string Version = "0.2.0-rc.33";',
  projectSource: "<Project><PropertyGroup><Version>0.2.0-rc.33</Version></PropertyGroup></Project>"
});

test("current Game Mod package, manifest, loaded literal and project version agree", () => {
  assert.equal(assertGameModVersions(root).ok, true);
});

test("each independent stale or absent version prevents build admission", () => {
  assert.equal(evaluateGameModVersions(fixture()).ok, true);
  for (const [key, value] of [
    ["packageVersion", "0.2.0-rc.32"], ["manifestVersion", "0.2.0-rc.32"],
    ["nativeSource", 'public const string Version = "0.2.0-rc.31";'],
    ["projectSource", "<Version>0.2.0-rc.31</Version>"],
    ["nativeSource", "// no native version"], ["projectSource", "<Project/>"],
    ["packageVersion", ""]
  ]) assert.equal(evaluateGameModVersions({ ...fixture(), [key]: value }).ok, false, key);
});

test("duplicate declarations and dynamic project versions cannot select a convenient value", () => {
  const value = fixture();
  assert.equal(evaluateGameModVersions({ ...value, nativeSource: value.nativeSource + "\n" + value.nativeSource }).ok, false);
  assert.equal(evaluateGameModVersions({ ...value, projectSource: value.projectSource + "<Version>other</Version>" }).ok, false);
  assert.equal(evaluateGameModVersions({ ...value, projectSource: "<Version>$(Version)</Version>" }).ok, false);
  for (const tag of [
    '<Version Condition="\'$(Configuration)\' == \'Release\'">0.2.0-rc.31</Version>',
    '<version>0.2.0-rc.31</version>', '<Version/>'
  ]) assert.equal(evaluateGameModVersions({ ...value, projectSource: value.projectSource + tag }).ok, false);
  assert.equal(evaluateGameModVersions({ ...value,
    projectSource: '<PropertyGroup Condition="\'$(Configuration)\' == \'Debug\'"><Version>0.2.0-rc.33</Version></PropertyGroup>'
  }).ok, false);
  assert.equal(evaluateGameModVersions({ ...value, projectSource: "<!-- <Version>old</Version> -->" + value.projectSource }).ok, true);
});
