import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { globalRuntimeFixture } from "./global-runtime-fixtures.js";
import { replayGlobalAgentRun } from "../src/global-replay.js";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { inspectCommand, overview, runHumanChooser } from "../examples/p1/human-chooser.mjs";
const script = fileURLToPath(new URL("../examples/p1/human-chooser.mjs", import.meta.url));
const observation = () => ({ status: "decision", envelope: { occurrence: 17 },
  scene: { native_kind: "card_reward_selection", content: { surface: { cards: [{ entity_id: "card-one", name: "压缩", description: "完整描述", card_previews: [{ definition_id: "FUEL", description: "燃料完整描述" }] }] } } },
  map: { content: { nodes: [{ col: 3, row: 1, children: [{ col: 4, row: 2 }], point_type: "boss", extension: "map-extra" }] } },
  run: { content: { act: 1, floor: 16 } }, player: { content: { hp: 50, max_hp: 75, gold: 20 } },
  extra_reads: [{ kind: "surface_card", content: { entity_id: "card-one", keywords: [{ name: "变化", description: "关键词完整描述" }] } }],
  commitments: { declined_reward_groups: [] }, catalog: [{ choice_id: "c7", label: "选择压缩", kind: "card_reward.take", subject: "card-one", target: null }] });

describe("manual P1 chooser", () => {
  it("makes every original observation field, map relation, card preview, and detailed Read inspectable", () => {
    const o = observation();
    expect(JSON.parse(inspectCommand(o, "all"))).toEqual(o);
    expect(JSON.parse(inspectCommand(o, "map"))).toEqual(o.map);
    expect(inspectCommand(o, "detail card-one")).toContain("燃料完整描述");
    expect(inspectCommand(o, "detail card-one")).toContain("关键词完整描述");
    expect(inspectCommand(o, "choice 1")).toContain("燃料完整描述");
    expect(inspectCommand(o, "find map-extra")).toContain("boss");
    expect(overview(o)).toContain("c7");
  });
  it("shows event consequences, selected cards and current enchantments without mutating input or IDs", () => {
    const o = observation();
    o.scene = { native_kind: "event_option", content: { context: { body: "事件完整正文" },
      surface: { options: [{ entity_id: "e1", title: "吃蛋", description: "获得7点最大生命值。" }] } } };
    const before = JSON.stringify(o);
    expect(overview(o, { width: 80 })).toContain("获得7点最大生命值");
    expect(JSON.stringify(o)).toBe(before);
    o.scene = { native_kind: "deck_upgrade_selection", content: { surface: {
      selected_count: 1, max_select: 4, selected_card_entity_ids: ["e1"],
      selection_progress: { minimum_additional_cards: 3, remaining_slots: 3, mode: "forward_append_only" },
      cards: [{ entity_id: "e1", name: "电击+", cost: "0", is_upgraded: true,
        existing_enchantment: { name: "播种", amount: 1, description: "首次打出获得能量。" } }]
    } } };
    expect(overview(o)).toContain("已选 1/4：电击+ [e1]");
    o.combat = { content: { player: { hand: o.scene.content.surface.cards }, enemies: [] } };
    const shown = overview(o, { width: 120 });
    expect(shown).toContain("附魔 播种 ×1：首次打出获得能量");
    expect(shown).not.toContain("电击++");
    expect(overview(o)).toContain("c7");
  });
  it("keeps an enchantment after-preview separate from the original card with the same entity", () => {
    const o = observation();
    const original = { entity_id: "e67", name: "防御", description: "获得5点格挡。" };
    const after = { ...original, existing_enchantment: { name: "播种", amount: 1, description: "首次打出获得能量。" } };
    o.scene = { native_kind: "deck_enchant_selection", content: { surface: {
      cards: [original], selected_count: 0, max_select: 1, selected_card_entity_ids: [],
      selection_progress: { minimum_additional_cards: 1, remaining_slots: 1 },
      preview_options: [{ subject_entity_id: "e67", display_context: "native_single_enchantment_after", after }]
    } } };
    o.extra_reads = [{ display_context: "selector_original:deck_enchant", content: original },
      { display_context: "native_single_enchantment_after", content: after }];
    const shown = overview(o, { width: 120 });
    expect(shown.split("附魔后")[0]).not.toContain("附魔");
    expect(shown.split("附魔后")[1]).toContain("附魔 播种");
  });
  it("browses without dispatching, rejects invalid input, and submits only the offered id at its occurrence", async () => {
    const o = observation(), sent = [], terminal = [], answers = ["map", "detail card-one", "choice 1", "all", "", "1.1", "c999", "c7"];
    async function* input() { yield JSON.stringify({ type: "hello", protocol: "sts2.p1/agent-port-1", episode_id: "manual-test" }); yield JSON.stringify({ type: "observation", requires_choice: true, observation: o }); yield JSON.stringify({ type: "episode_end", outcome: "loss" }); }
    await runHumanChooser({ input: input(), send: value => sent.push(value), say: value => terminal.push(value), ask: () => answers.shift() });
    expect(sent).toEqual([{ type: "ready", protocol: "sts2.p1/agent-port-1", agent_id: "human-p1-chooser", agent_version: "2" }, { type: "choose", occurrence: 17, choice_id: "c7" }]);
    expect(terminal.join("\n")).toContain("此处没有 Receipt");
    expect(terminal.join("\n")).not.toContain("Runtime Result（仅实际收到时显示）");
  });
  it("stops on an explicit human quit without selecting or inventing a result", async () => {
    const sent = [];
    async function* input() { yield JSON.stringify({ type: "observation", requires_choice: true, observation: observation() }); }
    await runHumanChooser({ input: input(), send: value => sent.push(value), say() {}, ask: () => "q" });
    expect(sent).toEqual([{ type: "abstain", reason: "human_stopped" }]);
  });
  it.skipIf(process.platform === "win32").each(["direct", "session-bridge"])("uses a real controlling PTY with %s protocol binding", mode => {
    // No Host or game: Python only assigns the child's controlling terminal.
    const python = String.raw`import os,pty,fcntl,termios,subprocess,json,sys,select,time
master,slave=pty.openpty()
def terminal():
 os.setsid();fcntl.ioctl(slave,termios.TIOCSCTTY,0)
mode=sys.argv[4]
if mode!='direct':
 import tempfile
 manifest=json.load(open(sys.argv[6]))['manifest'];manifest['manifest_id']='pty-manual'
 temp=tempfile.NamedTemporaryFile(mode='w',suffix='.json');json.dump(manifest,temp);temp.flush();sys.argv[6]=temp.name
command=[sys.argv[1],sys.argv[2]] if mode=='direct' else [sys.argv[1],sys.argv[5],sys.argv[6],sys.argv[1],sys.argv[2]]
p=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,pass_fds=(slave,),preexec_fn=terminal,text=True)
os.close(slave)
def protocol(m):p.stdin.write(json.dumps(m)+'\n');p.stdin.flush()
text=b''
def line():
 global text
 end=time.monotonic()+5
 while time.monotonic()<end:
  ready,_,_=select.select([p.stdout,master],[],[],0.1)
  if master in ready:text+=os.read(master,65536)
  if p.stdout in ready:return json.loads(p.stdout.readline())
 raise RuntimeError('protocol timeout')
o=json.loads(sys.argv[3])
def outer(kind,input):protocol({'schema':'sts2.policy-runtime/agent-session-1','message_type':kind,'session_id':'pty-session','recovery_epoch':0,'request_id':'test-request','input':input})
def consume(observation,unit):
 outer('consume',{'acquisition_id':'acq-'+unit,'continuity_token':'token','consumption_unit':{'id':unit},'observation':observation})
 consumed=line();outer('consume_ack',{});outer('next',{'continuity_token':'token'});return consumed
if mode=='direct':
 protocol({'type':'hello','protocol':'sts2.p1/agent-port-1','episode_id':'real-pty'});ready=line()
 protocol({'type':'observation','requires_choice':True,'observation':o})
else:
 ready=line();consume(o,'one')
os.write(master,b'map\nchoice 1\nc7\n');choice=line()
if mode=='direct':protocol({'type':'episode_end','outcome':'loss'})
else:
 terminal_observation={**o,'status':'terminal','terminal':{'outcome':'loss'}}
 consume(terminal_observation,'two');closed=line()
 if closed['output']['directive']['type']!='close':raise RuntimeError('terminal not closed')
p.stdin.close()
end=time.monotonic()+5
while p.poll() is None and time.monotonic()<end:
 if select.select([master],[],[],0.1)[0]:
  try:text+=os.read(master,65536)
  except OSError:break
p.wait(timeout=1)
while select.select([master],[],[],0.2)[0]:
 try:
  chunk=os.read(master,65536)
  if not chunk:break
  text+=chunk
 except OSError:break
os.close(master)
print(json.dumps({'ready':ready,'choice':choice,'exit':p.returncode,'terminal':text.decode('utf8')}))
`;
    const result = spawnSync("python3", ["-c", python, process.execPath, script, JSON.stringify(observation()), mode,
      fileURLToPath(new URL("../examples/p1/session-bridge.mjs", import.meta.url)),
      fileURLToPath(new URL("../contracts/fixtures/agent-session-v1.json", import.meta.url))], { encoding: "utf8", timeout: 10000 });
    expect(result.status, result.stderr).toBe(0);
    const out = JSON.parse(result.stdout);
    expect(out.exit).toBe(0);
    if (mode === "direct") expect(out.choice).toEqual({ type: "choose", occurrence: 17, choice_id: "c7" });
    else expect(out.choice.output.directive).toEqual({ type: "act", basis_acquisition_id: "acq-one",
      selection: { kind: "handle", action_id: "c7" }, scores: null });
    expect(out.terminal).toContain("map-extra");
    expect(out.terminal).toContain("燃料完整描述");
    expect(out.terminal).toContain("没有 Receipt");
  });
  it.skipIf(process.platform === "win32")("records terminal-selected intent, actual dispatch, delivered Receipt and successor through the existing Runtime", async () => {
    // The only synthetic parts are the existing HTTP game fixture and typed key;
    // chooser, controlling PTY, session bridge, Runtime, and evidence are real.
    const bridge = fileURLToPath(new URL("../examples/p1/session-bridge.mjs", import.meta.url));
    const python = String.raw`import os,pty,fcntl,termios,subprocess,sys,select
master,slave=pty.openpty()
def terminal():os.setsid();fcntl.ioctl(slave,termios.TIOCSCTTY,0)
p=subprocess.Popen(sys.argv[1:],stdin=sys.stdin,stdout=sys.stdout,stderr=sys.stderr,pass_fds=(slave,),preexec_fn=terminal)
os.close(slave);text=b'';selected=False
while p.poll() is None:
 if select.select([master],[],[],0.1)[0]:
  try:chunk=os.read(master,65536)
  except OSError:break
  if not chunk:break
  text+=chunk
  if not selected and '浏览命令 / 动作编号 > '.encode() in text:
   os.write(master,b'1\n');selected=True
os.close(master);sys.exit(p.wait())
`;
    const f = await globalRuntimeFixture({ program: { command: "python3",
      args: ["-c", python, process.execPath, bridge, "{manifest}", process.execPath, script],
      artifact: Buffer.from("synthetic PTY human chooser conformance"),
      adapter: { id: "p1-session-bridge", version: "1", protocol: "sts2.policy-runtime/agent-session-ndjson-1",
        code_sha256: createHash("sha256").update(readFileSync(bridge)).digest("hex") } } });
    try {
      expect((await f.runtime.tick()).type).toBe("delivered");
      expect((await f.runtime.tick()).type).toBe("closed");
      const events = await f.events();
      const dispatch = events.find(e => e.kind === "global_dispatch").payload;
      const receipt = events.find(e => e.kind === "global_receipt").payload.receipt;
      expect(receipt.delivery).toBe("delivered");
      expect(receipt.request_id).toBe(dispatch.request_id);
      expect(receipt.action.bound_action_id).toBe(dispatch.bound_action.bound_action_id);
      expect(events.some(e => e.kind === "global_source" && e.sequence > events.find(e => e.kind === "global_receipt").sequence)).toBe(true);
      expect(events.find(e => e.kind === "global_result").payload.result.status).toBe("completed");
      expect((await replayGlobalAgentRun(f.evidence.directory)).verdict).toBe("verified");
      expect(f.manifest.claims.human_origin).toBe(false);
    } finally { await f.close(); }
  });

});
