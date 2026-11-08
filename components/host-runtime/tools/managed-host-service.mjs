#!/usr/bin/env node
import path from "node:path";
import { fileURLToPath } from "node:url";
import { runManagedHostServiceTool } from "../src/managed-host-service-tool.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
await runManagedHostServiceTool(process.argv.slice(2), { root: ROOT });
