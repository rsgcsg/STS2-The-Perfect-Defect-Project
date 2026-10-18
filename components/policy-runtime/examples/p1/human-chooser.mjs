#!/usr/bin/env node
// Ordinary P1 Agent over stdin/stdout; the person reads and chooses through /dev/tty.
// A recorded manual protocol choice is not independently verified Human provenance.
import { openSync, closeSync, readSync, writeSync } from "node:fs";
import readline from "node:readline";
import { pathToFileURL } from "node:url";

const pretty = value => JSON.stringify(value, null, 2);
const content = part => part?.content ?? null;
const card = c => c ? `${c.name ?? c.definition_id ?? "?"}(${c.cost ?? "?"})${c.is_upgraded ? "〔已升级〕" : ""} [${c.entity_id ?? "preview"}]` : "?";
const SECTIONS = ["scene", "run", "player", "deck", "map", "combat", "piles", "extra_reads", "commitments", "catalog", "terminal", "envelope"];
export const HELP = "输入编号或 choice_id 选择；help 帮助；catalog 完整动作目录；choice 编号 查看动作和关联实体；map 地图；scene 场景全文；player/run/deck/combat/piles/extra_reads/commitments 分区全文；detail 实体ID 所有同实体公开详情；find 文字 搜索公开事实；all 完整原始观察；q 停止。浏览不会提交动作。";

const displayLabel = choice => ({
  "reward.decline_group": "跳过本组牌奖（仍留在奖励页）",
  "reward.leave_remaining": "放弃本页所有剩余奖励并继续",
  "treasure.skip": "放弃当前宝箱遗物",
}[choice.kind] ?? choice.label);
const text = value => String(value ?? "").replace(/\[\/?(?:blue|gold|red|green|yellow|purple|b|i)\]/gu, "");
function cardLines(c, obs, prefix = "  ") {
  const result = [`${prefix}${card(c)}：${text(c.description)}`];
  // The same entity may also have an after-preview Read. Keep each card in its
  // own semantic context instead of merging details by entity_id.
  if (c.existing_enchantment) {
    const e = c.existing_enchantment;
    result.push(`${prefix}  附魔 ${e.name ?? e.definition_id} ×${e.amount}：${text(e.description)}`);
  }
  for (const k of c.keywords ?? []) result.push(`${prefix}  ${k.name}：${text(k.description)}`);
  return result;
}
export function mapText(obs) {
  const m = content(obs.map);
  if (!m?.nodes) return `地图：${obs.map?.availability ?? "未提供"}`;
  const kinds = { monster: "战", elite: "精", rest_site: "营", shop: "商", treasure: "箱", boss: "首领预告", unknown: "?" };
  const coord = n => `${n.col},${n.row}`;
  const nodes = [...m.nodes].sort((a,b) => a.row - b.row || a.col - b.col);
  const lines = [`地图：当前 ${m.current_position ? coord(m.current_position) : "尚未进入"}；*为原生本次可选节点，连线为已给出的公开边`];
  for (const n of nodes) lines.push(`  ${n.state === "travelable" ? "*" : " "} (${coord(n)}) ${kinds[n.point_type] ?? n.point_type} [${n.entity_id}] → ${(n.children ?? []).map(coord).join(" / ") || "—"}`);
  return lines.join("\n");
}
function sceneLines(obs) {
  const scene = content(obs.scene) ?? {}, s = scene.surface ?? {}, lines = [];
  if (scene.context?.name) lines.push(`场景：${scene.context.name}`);
  if (scene.context?.body) lines.push(text(scene.context.body));
  if (obs.scene?.prompt) lines.push(text(obs.scene.prompt));
  for (const option of s.options ?? []) {
    lines.push(`  [${option.entity_id}] ${text(option.title)}：${text(option.description)}`);
    for (const tip of option.tooltips ?? []) {
      if (tip.card) lines.push(...cardLines(tip.card, obs, "    "));
      else if (tip.description) lines.push(`    ${tip.name ?? "说明"}：${text(tip.description)}`);
    }
  }
  if (s.selection_progress) {
    const ids = s.selected_card_entity_ids ?? [];
    const selected = ids.map(id => { const c = s.cards?.find(c => c.entity_id === id); return `${c?.name ?? id} [${id}]`; });
    lines.push(`已选 ${s.selected_count}/${s.max_select}：${selected.join("、") || "无"}；尚需 ${s.selection_progress.minimum_additional_cards}，剩余名额 ${s.selection_progress.remaining_slots}`);
    if (s.selection_progress.mode === "forward_append_only") lines.push("P1逐张向前选择，已选牌保留，不提供撤销；达到要求时由Runtime完成机械确认。");
    for (const c of s.cards ?? []) if (!ids.includes(c.entity_id)) {
      lines.push(...cardLines(c, obs));
      const preview = s.preview_options?.find(p => p.subject_entity_id === c.entity_id);
      if (preview?.after) {
        lines.push(`    ${obs.scene?.native_kind === "deck_upgrade_selection" ? "升级后" : obs.scene?.native_kind === "deck_enchant_selection" ? "附魔后" : "选择后"}：`);
        lines.push(...cardLines(preview.after, obs, "      "));
      }
    }
  } else if (s.cards) for (const c of s.cards) lines.push(...cardLines(c, obs));
  for (const group of s.rewards ?? []) {
    lines.push(`奖励组 [${group.group_id}] ${group.kind}`);
    for (const c of group.card_options ?? []) lines.push(...cardLines(c, obs));
    if (group.description) lines.push(text(group.description));
  }
  for (const relic of s.relics ?? []) lines.push(`遗物 ${relic.name} [${relic.entity_id}]：${text(relic.description)}`);
  if (obs.scene?.native_kind === "map_navigation") lines.push(mapText(obs));
  return lines;
}
function wrapLines(value, width) {
  return value.split("\n").map(line => {
    let out = "", row = "", columns = 0;
    for (const ch of line) {
      const n = /\p{Mark}/u.test(ch) ? 0 : /[\u2e80-\ua4cf\uac00-\ud7a3\uf900-\ufaff\ufe10-\ufe6f\uff01-\uff60]|\p{Extended_Pictographic}/u.test(ch) ? 2 : 1;
      if (columns + n > width && row) { out += row + "\n"; row = "    "; columns = 4; }
      row += ch; columns += n;
    }
    return out + row;
  }).join("\n");
}
export function catalogText(obs) {
  return (obs.catalog ?? []).map((c, i) => `  [${i + 1}] ${c.choice_id} ${displayLabel(c)} (${c.kind}) subject=${c.subject ?? "null"} target=${c.target ?? "null"}`).join("\n");
}
export function overview(obs, { width = Math.max(40, Number(process.env.COLUMNS) || 100) } = {}) {
  const run = content(obs.run) ?? {}, player = content(obs.player) ?? {};
  const lines = [`\n========== occurrence ${obs.envelope?.occurrence ?? "?"} | 第 ${run.act ?? "?"} 幕 第 ${run.floor ?? "?"} 层 | HP ${player.hp}/${player.max_hp} | 金币 ${player.gold} | ${obs.scene?.native_kind ?? obs.status}`];
  if (player.relics?.length) lines.push(`遗物：${player.relics.map(r => `${r.name} [${r.entity_id}]`).join("、")}`);
  if (player.potions?.length) lines.push(`药水：${player.potions.map(p => `${p.name} [${p.entity_id}]`).join("、")}`);
  if (content(obs.deck)?.cards) lines.push(`牌组(${obs.deck.content.cards.length})：deck 查看完整牌组；detail 实体ID 查看全部公开详情`);
  const combat = content(obs.combat);
  if (combat?.player) {
    lines.push(`能量 ${combat.player.energy}/${combat.player.max_energy} 格挡 ${combat.player.block} | 手牌：${(combat.player.hand ?? []).map(card).join(" ")}`);
    for (const c of combat.player.hand ?? []) lines.push(...cardLines(c, obs));
    for (const e of combat.enemies ?? []) lines.push(`敌人 ${e.entity_id} ${e.name} HP ${e.hp}/${e.max_hp} 格挡 ${e.block}\n${pretty({ intents: e.intents, statuses: e.statuses })}`);
  }
  lines.push(...sceneLines(obs));
  lines.push(catalogText(obs), HELP);
  return wrapLines(lines.join("\n"), width);
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
