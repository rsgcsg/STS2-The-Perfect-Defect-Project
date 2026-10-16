#!/usr/bin/env node
// Deterministic, locale-free external P1 Agent for cross-Host pairing. It knows only the public
// sts2.p1/agent-port-1 protocol. It groups the catalog by (kind, subject/target public identity)
// and picks one group by a hash of (run seed, own decision count), so two Hosts showing the same
// public state make the same semantic choice even though aliases, display text and the number of
// forced (continue-only) pages differ.
import { createHash } from "node:crypto";
import readline from "node:readline";

function names(observation) {
  const out = new Map();
  const visit = (value) => {
    if (Array.isArray(value)) { value.forEach(visit); return; }
    if (value === null || typeof value !== "object") return;
    if (typeof value.entity_id === "string" && !out.has(value.entity_id)) {
      if (typeof value.definition_id === "string") out.set(value.entity_id, `${value.definition_id}${value.is_upgraded ? "+" : ""}`);
      else if (Number.isInteger(value.col) && Number.isInteger(value.row)) out.set(value.entity_id, `${value.point_type ?? "node"}@${value.col},${value.row}`);
      else if (typeof value.kind === "string") out.set(value.entity_id, value.kind);
      else if (Number.isInteger(value.index)) out.set(value.entity_id, `option#${value.index}`);
    }
    if (typeof value.group_id === "string" && typeof value.kind === "string" && !out.has(value.group_id)) out.set(value.group_id, value.kind);
    Object.values(value).forEach(visit);
  };
  const { envelope: _envelope, ...view } = observation;
  visit(view);
  return out;
}

let seed = "";
let decisions = 0;
for await (const line of readline.createInterface({ input: process.stdin })) {
  const msg = JSON.parse(line);
  if (msg.type === "hello") {
    seed = String(msg.episode_id).split("-").at(-1); decisions = 0;
    console.log(JSON.stringify({ type: "ready", protocol: msg.protocol, agent_id: "external-signature-choice", agent_version: "1" }));
  } else if (msg.type === "observation" && msg.requires_choice) {
    const observation = msg.observation;
    const who = names(observation);
    const label = (alias) => alias === null ? "-" : who.get(alias) ?? "?";
    const groups = new Map();
    for (const choice of observation.catalog) {
      const key = `${choice.kind}|${label(choice.subject)}|${label(choice.target)}`;
      if (!groups.has(key)) groups.set(key, choice.choice_id);
    }
    const keys = [...groups.keys()].sort();
    const pick = parseInt(createHash("sha256").update(`${seed}:${decisions++}`).digest("hex").slice(0, 8), 16) % keys.length;
    console.log(JSON.stringify({ type: "choose", occurrence: observation.envelope.occurrence, choice_id: groups.get(keys[pick]) }));
  } else if (msg.type === "episode_end") break;
}
