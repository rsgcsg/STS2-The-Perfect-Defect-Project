#!/usr/bin/env node
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fetchOfficialRuntimeRelease, readOfficialRuntimeRelease } from "./runtime-release.mjs";
import { verifyRuntimeArchive, verifyNativeSealBinding, publishPreparedRuntimeRelease } from "./runtime-installation.mjs";

// Fixed official repository/asset/version route. No local seal, endpoint,
// archive or caller-supplied receipt can replace the official fetch.
const args = process.argv.slice(2);
let temporary;
try {
  if (args.length < 2 || args.length > 3) throw new Error("usage: prepare-runtime-release <version> <absolute-release-root> [python]");
  const [version, releases, selectedPython] = args;
  if (!path.isAbsolute(releases) || path.resolve(releases) !== releases) throw new Error("absolute_release_root_required");
  const tool = path.resolve(import.meta.dirname, "../../python/tools/install_developer_kit.py");
  const python = selectedPython ?? path.resolve(import.meta.dirname, "../../python/.venv",
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
  const bundle = await fetchOfficialRuntimeRelease(version);
  const release = readOfficialRuntimeRelease(bundle);
  temporary = fs.mkdtempSync(path.join(os.tmpdir(), "sts2-official-runtime-"));
  const archive = path.join(temporary, "package.zip");
  fs.writeFileSync(archive, release.archive, { flag: "wx", mode: 0o600 });
  const identity = verifyRuntimeArchive(python, tool, archive, release.digests.archive);
  verifyNativeSealBinding(identity, release.seal);
  const prepared = spawnSync(python, ["-I", tool, "prepare", "--archive", archive,
    "--sha256", release.digests.archive, "--releases", releases], {
    encoding: "utf8", timeout: 900000, maxBuffer: 1048576
  });
  if (prepared.error || prepared.status !== 0) throw new Error("official_release_prepare_failed");
  const result = publishPreparedRuntimeRelease(bundle, path.join(releases, release.digests.archive), identity);
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
} catch (error) {
  process.stderr.write(`${JSON.stringify({ status: "failed", code: error.message })}\n`);
  process.exitCode = 1;
} finally {
  if (temporary) fs.rmSync(temporary, { recursive: true, force: true });
}
