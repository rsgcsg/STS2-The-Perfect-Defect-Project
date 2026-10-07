import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { evaluateConnectorVersions } from "./connector-versions.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = relative => readFileSync(path.join(root, relative), "utf8");
const result = evaluateConnectorVersions({
  packageVersion: JSON.parse(read("package.json")).version,
  releaseVersion: JSON.parse(read("release-manifest.json")).release.version,
  modManifestVersion: JSON.parse(read("host/mod_manifest.json")).version,
  nativeSource: read("host/ConnectorMod.cs")
});
if (!result.ok) {
  console.error(`Connector product version checks failed:\n${result.errors.map(error => `- ${error}`).join("\n")}`);
  process.exitCode = 1;
} else {
  console.log(`Connector product versions agree (${result.nativeVersion}); SDK and protocol version independently.`);
}
