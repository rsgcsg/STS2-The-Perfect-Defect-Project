#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { resolveProjectPython } from "./project-python.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const [relativeCwd, ...args] = process.argv.slice(2);
if (!relativeCwd || args.length === 0) {
  throw new Error("usage: run-python-component.mjs <workspace-relative-cwd> <python args...>");
}
// STS2_EVIDENCE_PYTHON stays a compatible explicit alias of PYTHON.
const { executable } = resolveProjectPython({ aliases: ["STS2_EVIDENCE_PYTHON"] });
const result = spawnSync(executable, args, {
  cwd: path.join(root, relativeCwd),
  encoding: "utf8",
  stdio: "inherit"
});
process.exit(result.status ?? 1);
