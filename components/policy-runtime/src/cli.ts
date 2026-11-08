#!/usr/bin/env node
import process from "node:process";
import { main } from "./cli-main.js";

main(process.argv.slice(2)).catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.stack ?? error.message : String(error)}\n`);
  process.exitCode = 1;
});
