#!/usr/bin/env node
// Diagnostic deterministic chooser. It retains every catalog instance. Opaque IDs
// and order are Host-specific: this program makes no cross-Host semantic replay claim.
// Use a record-derived, bijection-checked replay for a same-action comparison.
import { createHash } from "node:crypto";
import readline from "node:readline";
let seed = ""; let decisions = 0;
for await (const line of readline.createInterface({ input: process.stdin })) {
  const msg = JSON.parse(line);
  if (msg.type === "hello") {
    seed = String(msg.episode_id).split("-").at(-1); decisions = 0;
    console.log(JSON.stringify({ type: "ready", protocol: msg.protocol, agent_id: "external-instance-choice-diagnostic", agent_version: "2" }));
  } else if (msg.type === "observation" && msg.requires_choice) {
    const catalog = msg.observation.catalog;
    if (!Array.isArray(catalog) || catalog.length === 0) {
      console.log(JSON.stringify({ type: "abstain", reason: "empty_catalog" })); continue;
    }
    const pick = parseInt(createHash("sha256").update(`${seed}:${decisions++}`).digest("hex").slice(0, 8), 16) % catalog.length;
    console.log(JSON.stringify({ type: "choose", occurrence: msg.observation.envelope.occurrence, choice_id: catalog[pick].choice_id }));
  } else if (msg.type === "episode_end") break;
}
