/** Side-effect-free Connector product identity check shared by portable and release gates.
 * The independently versioned SDK and wire protocol are deliberately not operands. */
export function evaluateConnectorVersions({ packageVersion, releaseVersion, modManifestVersion, nativeSource }) {
  const source = String(nativeSource).replace(/\/\*[\s\S]*?\*\//gu, "");
  const declarations = [...source.matchAll(/^\s*public const string Version = "([^"\r\n]+)";/gmu)];
  const nativeVersion = declarations.length === 1 ? declarations[0][1] : null;
  const errors = [];
  if (typeof packageVersion !== "string" || packageVersion.length === 0)
    errors.push("Connector package version is missing");
  if (releaseVersion !== packageVersion)
    errors.push(`release-manifest version ${releaseVersion ?? "missing"} does not match package version ${packageVersion ?? "missing"}`);
  if (modManifestVersion !== packageVersion)
    errors.push(`Mod manifest version ${modManifestVersion ?? "missing"} does not match package version ${packageVersion ?? "missing"}`);
  if (nativeVersion === null)
    errors.push("Native ConnectorMod.Version requires exactly one literal declaration");
  else if (nativeVersion !== packageVersion)
    errors.push(`Native implementation version ${nativeVersion} does not match package version ${packageVersion ?? "missing"}`);
  return { ok: errors.length === 0, nativeVersion, errors };
}

export function assertConnectorVersions(versions) {
  const result = evaluateConnectorVersions(versions);
  if (!result.ok) throw new Error(`Connector product version identity failed:\n${result.errors.join("\n")}`);
  return result;
}
