#!/usr/bin/env node
// Deterministic external P1 Agent: always the first catalog entry (catalog order is canonical P1 order).
import readline from "node:readline";
const rl = readline.createInterface({ input: process.stdin });
for await (const line of rl) {
  const msg = JSON.parse(line);
  if (msg.type === "hello") console.log(JSON.stringify({ type: "ready", protocol: msg.protocol, agent_id: "external-first-choice", agent_version: "1" }));
  else if (msg.type === "observation" && msg.requires_choice) console.log(JSON.stringify({ type: "choose", occurrence: msg.observation.envelope.occurrence, choice_id: msg.observation.catalog[0].choice_id }));
  else if (msg.type === "episode_end") break;
}
