#!/usr/bin/env node
// Mechanically rebind the BOM's *current* component source pointers to the
// committed component identities. Historical, loaded, artifact and Human
// records are never touched, and no check is relaxed: check:bom still decides.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { readIdentityReport } from "./component-identity.mjs";

const PLATFORM_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const BOM_REFRESH_COMMAND = "npm run bom:refresh";
export const BOM_REFRESH_HINT = `component identity drift: commit the component change, then run \`${BOM_REFRESH_COMMAND}\` ` +
  "and commit platform-bom.json (current pointers only; historical/loaded records are never rewritten)";
export const BOM_COMPONENTS = Object.freeze({
  native_foundation: "native-foundation", connector: "connector", host_runtime: "host-runtime",
  annotator: "annotator", evidence: "evidence", policy_runtime: "policy-runtime", workbench: "workbench",
  live_ui: "live-ui", game_mod: "game-mod",
});
const IDENTITY_FIELDS = ["version", "source_revision", "component_tree_revision", "component_source_digest_sha256"];

export function identityDrift(errors) {
  return errors.some(error => /^[a-z_]+\.(?:version|source_revision|component_tree_revision|component_source_digest_sha256):/u.test(error)
    || / current .* (?:source|digest)/u.test(error));
}

// Pure transformation over a parsed BOM; returns the changed field paths.
export function refreshBomIdentity(bom, identities) {
  const changes = [];
  for (const [bomKey, identityKey] of Object.entries(BOM_COMPONENTS)) {
    const identity = identities[identityKey], component = bom.components?.[bomKey];
    if (!identity || !component) throw new Error(`BOM or identity report lacks ${bomKey}`);
    if (identity.source_worktree_status !== "clean")
      throw new Error(`${identityKey} has uncommitted source; commit it first (source_revision is commit provenance)`);
    const expected = { version: identity.component_version, source_revision: identity.source_revision,
      component_tree_revision: identity.component_tree_revision,
      component_source_digest_sha256: identity.component_source_digest_sha256 };
    for (const field of IDENTITY_FIELDS) if (component[field] !== expected[field]) {
      changes.push(`components.${bomKey}.${field}`); component[field] = expected[field];
    }
  }
  // Every "current_component_source_*" pointer mirrors components.<same key>.
  const visit = (value, trail) => {
    if (!value || typeof value !== "object") return;
    for (const [key, child] of Object.entries(value)) {
      if (child && typeof child === "object" && key in BOM_COMPONENTS && "current_component_source_revision" in child) {
        for (const [field, source] of [["current_component_source_revision", "source_revision"],
          ["current_component_source_digest_sha256", "component_source_digest_sha256"]]) {
          if (field in child && child[field] !== bom.components[key][source]) {
            changes.push([...trail, key, field].join(".")); child[field] = bom.components[key][source];
          }
        }
      }
      if (key !== "components" || trail.length) visit(child, [...trail, key]);
    }
  };
  visit(bom, []);
  return changes;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const file = path.join(PLATFORM_ROOT, "platform-bom.json");
  const text = fs.readFileSync(file, "utf8");
  const bom = JSON.parse(text);
  const changes = refreshBomIdentity(bom, readIdentityReport(PLATFORM_ROOT).components);
  if (changes.length) fs.writeFileSync(file, `${JSON.stringify(bom, null, 2)}\n`);
  process.stdout.write(`${JSON.stringify({ changed: changes, next: changes.length ? "npm run check:bom" : "nothing to refresh" }, null, 2)}\n`);
}
