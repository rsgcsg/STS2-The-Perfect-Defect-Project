#!/usr/bin/env node
// Ordinary P1 Agent over stdin/stdout; the person reads and chooses through /dev/tty.
// A recorded manual protocol choice is not independently verified Human provenance.
import { openSync, closeSync, readSync, writeSync } from "node:fs";
import readline from "node:readline";
import { pathToFileURL } from "node:url";

const pretty = value => JSON.stringify(value, null, 2);
const content = part => part?.content ?? null;
const card = c => c ? `${c.name ?? c.definition_id ?? "?"}${c.is_upgraded ? "+" : ""}(${c.cost ?? "?"}) [${c.entity_id ?? "preview"}]` : "?";
const SECTIONS = ["scene", "run", "player", "deck", "map", "combat", "piles", "extra_reads", "commitments", "catalog", "terminal", "envelope"];
export const HELP = "输入编号或 choice_id 选择；help 帮助；catalog 完整动作目录；choice 编号 查看动作和关联实体；map 地图；scene 场景全文；player/run/deck/combat/piles/extra_reads/commitments 分区全文；detail 实体ID 所有同实体公开详情；find 文字 搜索公开事实；all 完整原始观察；q 停止。浏览不会提交动作。";

export function catalogText(obs) {
  return (obs.catalog ?? []).map((c, i) => `  [${i + 1}] ${c.choice_id} ${c.label} (${c.kind}) subject=${c.subject ?? "null"} target=${c.target ?? "null"}`).join("\n");
}
export function overview(obs) {
  const run = content(obs.run) ?? {}, player = content(obs.player) ?? {};
  const lines = [`\n========== occurrence ${obs.envelope?.occurrence ?? "?"} | 第 ${run.act ?? "?"} 幕 第 ${run.floor ?? "?"} 层 | HP ${player.hp}/${player.max_hp} | 金币 ${player.gold} | ${obs.scene?.native_kind ?? obs.status}`];
  if (player.relics?.length) lines.push(`遗物：${player.relics.map(r => `${r.name} [${r.entity_id}]`).join("、")}`);
  if (player.potions?.length) lines.push(`药水：${player.potions.map(p => `${p.name} [${p.entity_id}]`).join("、")}`);
  if (content(obs.deck)?.cards) lines.push(`牌组(${obs.deck.content.cards.length})：${obs.deck.content.cards.map(card).join(" ")}`);
  const combat = content(obs.combat);
  if (combat?.player) {
    lines.push(`能量 ${combat.player.energy}/${combat.player.max_energy} 格挡 ${combat.player.block} | 手牌：${(combat.player.hand ?? []).map(card).join(" ")}`);
    for (const e of combat.enemies ?? []) lines.push(`敌人 ${e.entity_id} ${e.name} HP ${e.hp}/${e.max_hp} 格挡 ${e.block}\n${pretty({ intents: e.intents, statuses: e.statuses })}`);
  }
  if (obs.scene?.prompt) lines.push(obs.scene.prompt);
  lines.push(catalogText(obs), HELP);
  return lines.join("\n");
}

function matchingFacts(value, predicate, path = "$", found = []) {
  if (value && typeof value === "object") {
    if (!Array.isArray(value) && predicate(value)) found.push({ path, content: value });
    for (const [key, child] of Object.entries(value)) matchingFacts(child, predicate, `${path}.${key}`, found);
  }
  return found;
}
export function inspectCommand(obs, answer) {
  const [command, ...rest] = answer.trim().split(/\s+/u), query = rest.join(" ");
  if (command === "help" || command === "?") return HELP;
  if (command === "all") return pretty(obs);
  if (command === "catalog") return pretty(obs.catalog);
  if (SECTIONS.includes(command)) return pretty(obs[command] ?? null);
  if (command === "detail") {
    if (!query) return "用法：detail 实体ID（编号动作的 subject/target 或卡牌旁的ID）";
    return pretty(matchingFacts(obs, value => value.entity_id === query || value.subject_entity_id === query));
  }
  if (command === "find") {
    if (!query) return "用法：find 文字（名称、描述或ID；只搜索本次公开观察）";
    return pretty(matchingFacts(obs, value => Object.values(value).some(v => typeof v === "string" && v.toLocaleLowerCase().includes(query.toLocaleLowerCase()))));
  }
  if (command === "choice") {
    const choice = (obs.catalog ?? []).find(c => c.choice_id === query) ?? (/^[1-9]\d*$/u.test(query) ? obs.catalog?.[Number(query) - 1] : null);
    if (!choice) return "没有该动作。用 choice 编号 或 choice choice_id 查看。";
    const ids = new Set([choice.subject, choice.target].filter(Boolean));
    return pretty({ choice, public_facts: matchingFacts(obs, value => ids.has(value.entity_id) || ids.has(value.subject_entity_id)) });
  }
  return null;
}

export async function runHumanChooser({ input, send, say, ask }) {
  for await (const line of input) {
    if (!line.trim()) continue;
    const msg = JSON.parse(line);
    if (msg.type === "hello") {
      say(`人工 P1 选择器：episode ${msg.episode_id}。${HELP}\n本次等待选择上限 ${Number(process.env.P1_AGENT_TIMEOUT_MS ?? 30000) / 1000} 秒（浏览也计时；总预算或 Stop 可更早结束），超时会停止；没有自动代选。\n会话桥不转发 Receipt/result；屏幕只确认你提交的意图。实际交付与后继请查 Runtime 原始记录。`);
      send({ type: "ready", protocol: msg.protocol, agent_id: "human-p1-chooser", agent_version: "2" });
    } else if (msg.type === "observation") {
      if (!msg.requires_choice) {
        say(msg.observation.status === "terminal" ? `终局：${msg.observation.terminal?.outcome}` : overview(msg.observation));
        continue;
      }
      const obs = msg.observation;
      say(overview(obs));
      for (;;) {
        const answer = ask("浏览命令 / 动作编号 > ");
        if (answer === null || answer.trim() === "q") { send({ type: "abstain", reason: answer === null ? "human_terminal_closed" : "human_stopped" }); return; }
        const text = answer.trim(), shown = inspectCommand(obs, text);
        if (shown !== null) { say(shown); continue; }
        const choice = (obs.catalog ?? []).find(c => c.choice_id === text) ?? (/^[1-9]\d*$/u.test(text) ? obs.catalog?.[Number(text) - 1] : null);
        if (!choice) { say("无效输入；输入 help 查看命令。没有提交动作。"); continue; }
        say(`提交人工意图：${choice.choice_id} ${choice.label}；等待 Runtime 后继观察（此处没有 Receipt）。`);
        send({ type: "choose", occurrence: obs.envelope.occurrence, choice_id: choice.choice_id });
        break;
      }
    } else if (msg.type === "result") {
      // The direct agent-port protocol can provide Results; session-bridge currently does not.
      say(`Runtime Result（仅实际收到时显示）：${pretty(msg.result)}`);
    } else if (msg.type === "episode_end") { say(`本局结束：${msg.outcome ?? msg.censor_reason ?? "原因未提供"}`); return; }
  }
}

async function main() {
  let tty;
  try { tty = openSync("/dev/tty", "r+"); }
  catch { process.stderr.write("人工选择器需要可用的控制终端 /dev/tty；请在本机 Terminal 中启动，不能只接管道或后台运行。\n"); process.exitCode = 2; return; }
  let pending = "";
  const ask = prompt => {
    writeSync(tty, prompt);
    for (;;) {
      const end = pending.indexOf("\n");
      if (end >= 0) { const line = pending.slice(0, end).replace(/\r$/u, ""); pending = pending.slice(end + 1); return line; }
      const buffer = Buffer.alloc(4096), n = readSync(tty, buffer, 0, buffer.length, null);
      if (n <= 0) return null;
      pending += buffer.subarray(0, n).toString("utf8");
    }
  };
  try {
    await runHumanChooser({ input: readline.createInterface({ input: process.stdin }),
      send: value => process.stdout.write(JSON.stringify(value) + "\n"),
      say: text => writeSync(tty, text + "\n"), ask });
  } finally { closeSync(tty); }
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
