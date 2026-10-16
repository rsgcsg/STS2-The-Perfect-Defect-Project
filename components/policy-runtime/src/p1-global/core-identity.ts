import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { P1G_PROFILE, P1G_REVISION, sha256Hex } from "@rsgcsg/sts2-connector-client/p1-global";

function coreDirectory(): string {
  const resolve = (import.meta as { resolve?: (specifier: string) => string }).resolve;
  if (typeof resolve === "function") return dirname(fileURLToPath(resolve("@rsgcsg/sts2-connector-client/p1-global")));
  // Test runners without import.meta.resolve: the same installed package, found the Node way.
  for (let directory = dirname(fileURLToPath(import.meta.url)); ; directory = dirname(directory)) {
    const candidate = join(directory, "node_modules", "@rsgcsg", "sts2-connector-client", "dist", "p1Global");
    if (existsSync(candidate)) return candidate;
    if (dirname(directory) === directory) throw new Error("p1_core_not_found");
  }
}

/** Identity of the exact shared P1 core used to produce or convert a record. */
export function p1CoreIdentity(): { profile: string; revision: string; module_digest: string } {
  const directory = coreDirectory();
  const files = readdirSync(directory).filter((name) => name.endsWith(".js")).sort();
  const digest = sha256Hex(files.map((name) => `${name}\u0000${sha256Hex(readFileSync(join(directory, name)))}`).join("\n"));
  return { profile: P1G_PROFILE, revision: P1G_REVISION, module_digest: digest };
}
