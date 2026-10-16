#!/usr/bin/env node
// Human P1 chooser: an ordinary P1 Agent program (sts2.p1/agent-port-1 on stdin/stdout)
// that shows the complete P1 observation and catalog on the terminal and asks a person.
// Records made through it are protocol-origin human P1 choices (agent_id human-p1-chooser),
// distinct from native physical play recorded by the in-game Annotator.
import { openSync, readSync, writeSync } from "node:fs";
import readline from "node:readline";

const tty = openSync("/dev/tty", "r+");
const say = (text) => writeSync(tty, text + "\n");
function ask(prompt) {
  writeSync(tty, prompt);
  const buffer = Buffer.alloc(256); let line = "";
  for (;;) {
    const n = readSync(tty, buffer, 0, buffer.length, null);
    if (n <= 0) return line;
    line += buffer.subarray(0, n).toString("utf8");
    if (line.includes("\n")) return line.split("\n")[0].trim();
  }
}
const card = (c) => c ? `${c.name ?? c.definition_id}${c.is_upgraded ? "+" : ""}(${c.cost ?? "?"})` : "?";
function show(obs) {
  const run = obs.run.content ?? {}; const player = obs.player.content ?? {};
  say(`\n========== 第 ${run.act ?? "?"} 幕 第 ${run.floor ?? "?"} 层 | HP ${player.hp}/${player.max_hp} | 金币 ${player.gold} | 场景 ${obs.scene.family}/${obs.scene.stage}`);
  if (player.relics?.length) say(`遗物：${player.relics.map((r) => r.name).join("、")}`);
  if (player.potions?.length) say(`药水：${player.potions.map((p) => p.name).join("、")}`);
  if (obs.deck.content?.cards) say(`牌组(${obs.deck.content.cards.length})：${obs.deck.content.cards.map(card).join(" ")}`);
  const combat = obs.combat.content;
  if (combat?.player) {
    say(`能量 ${combat.player.energy}/${combat.player.max_energy} 格挡 ${combat.player.block} | 手牌：${(combat.player.hand ?? []).map(card).join(" ")}`);
    for (const e of combat.enemies ?? []) say(`  敌人 ${e.entity_id} ${e.name} HP ${e.hp}/${e.max_hp} 格挡 ${e.block} 意图 ${(e.intents ?? []).map((i) => `${i.type}${i.label ? " " + i.label : ""}`).join(",")}`);
  }
  if (obs.piles.content?.zones) for (const z of obs.piles.content.zones) say(`  ${z.zone}(${z.cards.length}${z.ordering === "unordered_multiset" ? "，无序" : ""})：${z.cards.map(card).join(" ")}`);
  const surface = obs.scene.content?.surface ?? {};
  if (surface.title) say(`事件：${surface.title}\n${surface.description ?? ""}`);
  if (Array.isArray(surface.rewards)) for (const r of surface.rewards) say(`  奖励 ${r.group_id ?? r.entity_id}：${r.label}${r.card_options ? " → " + r.card_options.map(card).join(" / ") : ""}`);
  if (obs.commitments.declined_reward_groups.length) say(`已放弃的奖励组：${obs.commitments.declined_reward_groups.join("、")}`);
  obs.catalog.forEach((c, i) => say(`  [${i + 1}] ${c.label}   (${c.kind})`));
}

const rl = readline.createInterface({ input: process.stdin });
for await (const line of rl) {
  const msg = JSON.parse(line);
  if (msg.type === "hello") {
    say(`人工 P1 选择器：episode ${msg.episode_id}（${msg.host_kind}）。输入动作编号回车；输入 q 停止本局。`);
    process.stdout.write(JSON.stringify({ type: "ready", protocol: msg.protocol, agent_id: "human-p1-chooser", agent_version: "1" }) + "\n");
  } else if (msg.type === "observation") {
    if (!msg.requires_choice) { if (msg.observation.status === "terminal") say(`\n终局：${msg.observation.terminal?.outcome}`); continue; }
    show(msg.observation);
    for (;;) {
      const answer = ask("选择编号 > ");
      if (answer === "q") { process.stdout.write(JSON.stringify({ type: "abstain", reason: "human_stopped" }) + "\n"); break; }
      const index = Number(answer) - 1;
      const choice = msg.observation.catalog[index];
      if (choice) { process.stdout.write(JSON.stringify({ type: "choose", occurrence: msg.observation.envelope.occurrence, choice_id: choice.choice_id }) + "\n"); break; }
      say("无效编号，请重输。");
    }
  } else if (msg.type === "result" && msg.result.status !== "completed") {
    say(`结果：${msg.result.status}`);
  } else if (msg.type === "episode_end") { say(`本局结束：${msg.outcome ?? msg.censor_reason}`); break; }
}
