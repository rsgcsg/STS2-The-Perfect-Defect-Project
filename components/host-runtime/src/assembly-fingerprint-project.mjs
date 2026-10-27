import { copyFileSync, mkdtempSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";

/** Stage one shipped, reviewed .NET tool source outside an installed package. */
export function stageDotnetToolProject(root, name) {
  const source = path.join(root, "tools", "dotnet", name);
  const directory = mkdtempSync(path.join(os.tmpdir(), `sts2-host-${name.toLowerCase()}-`));
  try {
    for (const file of [`${name}.csproj`, "Program.cs"]) {
      copyFileSync(path.join(source, file), path.join(directory, file));
    }
    return {
      directory,
      project: path.join(directory, `${name}.csproj`),
      cleanup() { rmSync(directory, { recursive: true, force: true }); }
    };
  } catch (error) {
    rmSync(directory, { recursive: true, force: true });
    throw error;
  }
}

/** Stage the shipped, reviewed fingerprint source outside an installed package. */
export function stageAssemblyFingerprintProject(root) {
  return stageDotnetToolProject(root, "AssemblyFingerprint");
}
