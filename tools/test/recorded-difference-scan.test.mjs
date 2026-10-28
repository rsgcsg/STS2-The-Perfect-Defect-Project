import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { gunzipSync } from 'node:zlib';
import * as core from '../../components/connector/sdk/typescript/dist/p1Global/index.js';
import { selectorForwardPair } from '../../components/connector/sdk/typescript/test/fixtures/selector-forward.mjs';
import { rewardForwardPair } from '../../components/connector/sdk/typescript/test/fixtures/reward-forward.mjs';
import { collectObservationDifferences } from '../recorded-difference-collector.mjs';
import { RecordedActionLocator } from '../recorded-action-locator.mjs';
import { CompressedDiagnosticRecorder } from '../compressed-diagnostic-recorder.mjs';
import { buildRecordedInputTimeline, scanRecordedDifferences } from '../recorded-difference-scan.mjs';
const reduce = basis => core.reduce(core.createSession('diagnostic-test', 'reference'), basis);
const complete = basis => ({ ...structuredClone(basis), capture_status: 'complete', missing: [],
  snapshot: { ...structuredClone(basis.snapshot), information_policy: { ...basis.snapshot.information_policy, includes_hidden_information: false } } });
test('collects every independent leaf, including an approved toggle that still enters the model', () => {
  const a = reduce(selectorForwardPair().outer).observation, b = structuredClone(a);
  b.player.content.gold++; b.player.content.hp--; b.scene.content.surface.showing_upgrade_previews = !a.scene.content.surface.showing_upgrade_previews;
  const before = JSON.stringify([a,b]), result = collectObservationDifferences(a,b);
  assert.equal(result.differences.length, 3);
  assert.deepEqual(new Set(result.differences.map(x => x.category)),new Set(['reward_or_resources','combat_state_or_order','upgrade_toggle_in_model_input']));
  assert.ok(result.differences.every(x => x.classification === 'blocking' && x.affects_model_input));
  assert.equal(result.differences.find(x => x.category === 'upgrade_toggle_in_model_input').existing_comparison_exception, 'M060');
  assert.equal(JSON.stringify([a,b]), before);
});
test('same graph with renamed aliases does not become leaf differences', () => {
  const a = reduce(selectorForwardPair().outer).observation, b = structuredClone(a);
  const policy=core.createPublicIdentityPolicy(a.scene.native_kind,a.extra_reads,a.scene.content);
  const rename = (value,path='$.observation') => {
    if (typeof value==='string'&&(policy.relation(path)||core.normalizePublicPath(path)==='catalog[].choice_id')) return 'renamed-'+value;
    if (Array.isArray(value)) return value.map((child,i)=>rename(child,`${path}[${i}]`));
    if (value&&typeof value==='object') return Object.fromEntries(Object.entries(value).map(([key,child])=>[key,rename(child,`${path}.${key}`)]));
    return value;
  };
  const result = collectObservationDifferences(a,rename(b));
  assert.equal(result.strict.verdict,'equal'); assert.equal(result.differences.length,0);
});
test('changed legal action and changed resource are both retained', () => {
  const a = reduce(selectorForwardPair().outer).observation,b=structuredClone(a); b.catalog.pop();b.player.content.gold++;
  const result=collectObservationDifferences(a,b);
  assert.ok(result.differences.some(x=>x.path.includes('catalog')));assert.ok(result.differences.some(x=>x.path.endsWith('.gold')));
});
test('native action remains locatable after unrelated resource/public differences', () => {
  const original=complete(rewardForwardPair().outer),actual=structuredClone(original);actual.snapshot.persistent.content.player.gold+=100;
  const bound_action=original.snapshot.bound_actions.actions[0];
  const found=new RecordedActionLocator().locate({basis:original,bound_action,original_terminal_phase:'finished'},actual);
  assert.equal(found.input.bound_action_id,bound_action.bound_action_id);assert.equal(found.evidence.whole_observation_equivalence_claim,false);
});
test('unknown original never reaches native action selection; ambiguous current bindings are not guessed', () => {
  const basis=complete(rewardForwardPair().outer),bound_action=basis.snapshot.bound_actions.actions[0],w={basis,bound_action,original_terminal_phase:'unknown'};
  assert.throws(()=>new RecordedActionLocator().locate(w,basis),/unknown/);
  const actual=structuredClone(basis);actual.snapshot.bound_actions.actions.push({...bound_action,bound_action_id:'duplicate'});actual.snapshot.bound_actions.materialized_count=actual.snapshot.bound_actions.total_count=actual.snapshot.bound_actions.actions.length;
  assert.throws(()=>new RecordedActionLocator().locate({...w,original_terminal_phase:'finished'},actual),/ambiguous/);
});
test('compressed log restores exact raw response bytes and sequence across gzip members', context => {
  const dir=mkdtempSync(join(tmpdir(),'scan-gzip-'));context.after(()=>rmSync(dir,{recursive:true,force:true}));
  const p=join(dir,'log.jsonl.gz'),rec=new CompressedDiagnosticRecorder(p,{scope:'test'}),raw=' { "x": "原样" }';
  rec.appendRaw('env_response',raw);const closed=rec.close({done:true});
  const rows=gunzipSync(readFileSync(p)).toString().trim().split('\n').map(JSON.parse);
  assert.equal(rows[1].raw,raw);assert.deepEqual(rows.map(x=>x.seq),[1,2,3]);assert.equal(closed.lines,3);
});
function scanFixture() {
  const {outer,child}=rewardForwardPair(),bases=[complete(outer),complete(child),complete(outer)];
  const files=new Map(),events=[],captures=[],actions=[];
  const choices=[bases[0].snapshot.bound_actions.actions.find(x=>x.bound_action_id==='open'),
    bases[1].snapshot.bound_actions.actions.find(x=>x.verb==='skip'),
    bases[2].snapshot.bound_actions.actions.find(x=>x.verb==='activate'&&x.bound_action_id!=='open')];
  // Use actual fixture members, never make a new native action.
  for(let i=0;i<3;i++) {
    const input=choices[i]??bases[i].snapshot.bound_actions.actions.at(-1),event_id=`e${i}`,decision_id=`d${i}`,ref={payload_ref:`b${i}`,sha256:`sha${i}`};
    files.set(ref.payload_ref,Buffer.from(JSON.stringify(bases[i])));
    events.push({kind:'decision_accepted',event_id,sequence:i*2+1,decision_id,run_id:'r',basis:ref,bound_action:input,native_receipt:{kind:'semantic_ui_accepted'}});
    events.push({kind:'lifecycle',event_id:`f${i}`,sequence:i*2+2,decision_id,run_id:'r',phase:'finished'});
    const observation=reduce(bases[i]).observation;observation.player.content.gold+=i+1;
    captures.push({event_id,sequence:i*2+1,origin:'human_decision_basis',complete:true,missing:[],public_basis:ref,observation});
    actions.push({decision_id,terminal_sequence:i*2+2,result:{status:'completed'},choice:null});
  }
  return {bases,original:{events,journal:[],bytes:ref=>files.get(ref)},conversion:{scope:{run_id:'r'},actions,captures,raw_unknown_count:0,native_cancellations:[]}};
}
test('one run continues after all three changed observations and executes every original input',async()=>{
  const {bases,original,conversion}=scanFixture();let steps=0,closes=0;const records=[];
  const driver={async start(){},newMutationId(){return `m${steps}`;},async close(){closes++;return{};},async request({command}){
    if(command==='reset')return{};if(command==='episode_identity')return{identity:{}};if(command==='capture')return{full:{basis:bases[Math.min(steps,2)]}};
    if(command==='step'){steps++;return{receipt:{delivery:'delivered'}};}throw Error(command);
  }};
  const result=await scanRecordedDifferences({original,conversion,driver,record:{append(k,v){records.push([k,v]);}},seed:'private',initialProgress:{}});
  assert.equal(result.error,undefined);assert.equal(steps,3);assert.equal(result.executed_original_inputs,3);assert.equal(result.blocking_differences,3);
  assert.equal(result.reached_original_timeline_end,true);assert.equal(result.full_p1_equivalence_complete,false);assert.equal(closes,1);
  assert.equal(records.filter(([kind])=>kind==='diagnostic_difference').length,3);
});
test('original unknown forms an evidence boundary without an attempt or fabricated successor',()=>{
  const {original,conversion}=scanFixture();original.events[3].phase='unknown';conversion.raw_unknown_count=1;
  const plan=buildRecordedInputTimeline(original,conversion);assert.equal(plan.known_inputs,1);assert.equal(plan.items.at(-1).stop,'original_unknown_no_reoffer');
});
test('actual unknown stops immediately, no later original input is retried',async()=>{
  const {bases,original,conversion}=scanFixture();let steps=0;
  const driver={async start(){},newMutationId(){return 'm';},async close(){return{};},async request({command}){
    if(command==='reset')return{};if(command==='episode_identity')return{identity:{}};if(command==='capture')return{full:{basis:bases[0]}};
    if(command==='step'){steps++;return{receipt:{delivery:'unknown'}};}
  }};
  const result=await scanRecordedDifferences({original,conversion,driver,record:{append(){}},seed:'private',initialProgress:{}});
  assert.equal(steps,1);assert.equal(result.unknown_delivery,true);assert.equal(result.stop_reason,'unknown_delivery_no_retry');
});

test('partial original preview does not prevent a known original native input from being located',()=>{
  const original=complete(rewardForwardPair().outer),actual=structuredClone(original);
  original.capture_status='partial';original.missing=['original_preview_unavailable'];
  const bound_action=original.snapshot.bound_actions.actions[0];
  assert.equal(new RecordedActionLocator().locate({basis:original,bound_action,original_terminal_phase:'finished'},actual).input.bound_action_id,bound_action.bound_action_id);
  assert.equal(original.capture_status,'partial');
});
test('required target operand cannot silently become a targetless input',()=>{
  const basis=complete(rewardForwardPair().outer),bound_action=structuredClone(basis.snapshot.bound_actions.actions[0]);
  bound_action.arguments=[{role:'target',referent_id:'missing-original-target'}];basis.snapshot.bound_actions.actions[0]=bound_action;
  const actual=structuredClone(basis);actual.snapshot.bound_actions.actions[0].arguments=[];
  assert.throws(()=>new RecordedActionLocator().locate({basis,bound_action,original_terminal_phase:'finished'},actual),/not_executable/);
});
test('an unavailable current original action stops this run after retaining its earlier differences',async()=>{
  const {bases,original,conversion}=scanFixture();let steps=0;const modified=structuredClone(bases[1]);modified.snapshot.bound_actions.actions=modified.snapshot.bound_actions.actions.filter(input=>input.verb!=='skip');modified.snapshot.bound_actions.materialized_count=modified.snapshot.bound_actions.total_count=modified.snapshot.bound_actions.actions.length;
  const driver={async start(){},newMutationId(){return 'm';},async close(){return{};},async request({command}){
    if(command==='reset')return{};if(command==='episode_identity')return{identity:{}};if(command==='capture')return{full:{basis:steps?modified:bases[0]}};
    if(command==='step'){steps++;return{receipt:{delivery:'delivered'}};}
  }};
  const result=await scanRecordedDifferences({original,conversion,driver,record:{append(){}},seed:'private',initialProgress:{}});
  assert.equal(steps,1);assert.equal(result.executed_original_inputs,1);assert.equal(result.stop_reason,'recorded_action_not_executable');
  assert.ok(result.blocking_differences>=2);assert.equal(result.unknown_delivery,false);
});
test('owner pre-delivery stop is known unsent, rather than an unknown mutation',async()=>{
  const {bases,original,conversion}=scanFixture();let steps=0;
  const driver={async start(){},assertCanMutate(){throw Error('owner_stop_before_delivery');},newMutationId(){return'm';},async close(){return{};},async request({command}){
    if(command==='reset')return{};if(command==='episode_identity')return{identity:{}};if(command==='capture')return{full:{basis:bases[0]}};
    if(command==='step'){steps++;throw Error('should not send');}
  }};
  const result=await scanRecordedDifferences({original,conversion,driver,record:{append(){}},seed:'private',initialProgress:{}});
  assert.equal(steps,0);assert.equal(result.unknown_delivery,false);assert.equal(result.stop_reason,'owner_stop_before_delivery');
});

test('recorded native execution input maps only with its exact original semantic-catalog witness',()=>{
  const basis=complete(rewardForwardPair().outer),action=basis.snapshot.bound_actions.actions[0];
  const input={action_key:'original-key',verb:action.verb,subject_referent_id:action.subject_referent_id,arguments:{},label:action.label};
  const space={status:'captured',observed_membership:'exact_once',observed_match_count:1,observed_action_key:input.action_key,actions:[{key:input.action_key,verb:input.verb,subject_referent_id:input.subject_referent_id,arguments:{}}]};
  const witness={basis,native_input:input,original_execution_action_space:space,original_terminal_phase:'finished'};
  assert.equal(new RecordedActionLocator().locate(witness,basis).input.bound_action_id,action.bound_action_id);
  const broken=structuredClone(witness);broken.original_execution_action_space.observed_match_count=0;
  assert.throws(()=>new RecordedActionLocator().locate(broken,basis),/native_catalog_unproven/);
});

test('alias rename with a true gold difference never becomes a fake alias value difference',()=>{
  const a=reduce(selectorForwardPair().outer).observation;const policy=core.createPublicIdentityPolicy(a.scene.native_kind,a.extra_reads,a.scene.content);
  function rename(value,path='$.observation'){
    if(typeof value==='string'&&(policy.relation(path)||core.normalizePublicPath(path)==='catalog[].choice_id'))return'review-'+value;
    if(Array.isArray(value))return value.map((child,i)=>rename(child,`${path}[${i}]`));
    if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([key,child])=>[key,rename(child,`${path}.${key}`)]));return value;
  }
  const b=rename(a);b.player.content.gold++;const result=collectObservationDifferences(a,b);
  const real=result.differences.filter(row=>!row.reason.includes('alignment_ambiguous'));
  assert.deepEqual(real.map(row=>row.path),['$.observation.player.content.gold']);
});
test('unordered catalog count loss is a blocking fact even if members cannot be aligned',()=>{
  const a=reduce(selectorForwardPair().outer).observation,b=structuredClone(a);b.catalog=[];const result=collectObservationDifferences(a,b);
  assert.ok(result.differences.some(row=>row.path==='$.observation.catalog'&&row.reason==='collection_multiplicity_changed'&&row.classification==='blocking'));
});
test('graph equality and raw model view equality are reported independently',()=>{
  const a=reduce(selectorForwardPair().outer).observation,b=structuredClone(a);b.deck.content.cards.reverse();const result=collectObservationDifferences(a,b);
  assert.equal(result.strict.verdict,'equal');assert.equal(result.raw_model_input_changed,true);assert.equal(result.model_input_frame_check_required,true);
});
test('an incomplete current finite native catalog cannot establish uniqueness',()=>{
  const basis=complete(rewardForwardPair().outer),actual=structuredClone(basis),bound_action=basis.snapshot.bound_actions.actions[0];
  actual.capture_status='partial';actual.snapshot.bound_actions.status='partial';actual.snapshot.bound_actions.total_count++;
  assert.throws(()=>new RecordedActionLocator().locate({basis,bound_action,original_terminal_phase:'finished'},actual),/catalog_incomplete/);
});
test('short writes are completed before a successful gzip record receipt',context=>{
  const dir=mkdtempSync(join(tmpdir(),'scan-short-write-'));context.after(()=>rmSync(dir,{recursive:true,force:true}));
  const p=join(dir,'log.gz');let calls=0;const rec=new CompressedDiagnosticRecorder(p,{test:true},{write(fd,bytes,offset,length){calls++;return writeSync(fd,bytes,offset,Math.max(1,Math.floor(length/2)));}});
  rec.append('test',{content:'all original bytes'});rec.close({done:true});assert.ok(calls>3);
  assert.equal(gunzipSync(readFileSync(p)).toString().trim().split('\n').length,3);
});
test('an unrepresented original input issue is an evidence boundary rather than a silently skipped action',()=>{
  const {original,conversion}=scanFixture();original.events.splice(2,0,{kind:'issue',event_id:'gap',sequence:2.5,run_id:'r',reason:'selector_decision_pre_or_lineage_unavailable',native_action_type:'MissingSelectorClick'});
  const plan=buildRecordedInputTimeline(original,conversion);assert.equal(plan.known_inputs,1);assert.equal(plan.items.at(-1).stop,'original_unrecorded_input_gap');
});

test('filtered continuation children still commit the uniquely selected parent intention at its real terminal',async()=>{
  const {bases,original,conversion}=scanFixture();let steps=0;
  const parent=conversion.actions[0];parent.choice={kind:'reward.take_card'};parent.terminal_sequence=4;parent.continuation_decision_id='d1';
  parent.native_continuations=[{decision_id:'d1',result:{status:'completed'},capture:1}];
  conversion.actions=conversion.actions.filter(action=>action.decision_id!=='d1');
  original.events.find(event=>event.kind==='decision_accepted'&&event.decision_id==='d1').bound_action=bases[1].snapshot.bound_actions.actions.find(input=>input.bound_action_id==='select-0');
  const driver={async start(){},newMutationId(){return`m${steps}`;},async close(){return{};},async request({command}){
    if(command==='reset')return{};if(command==='episode_identity')return{identity:{}};if(command==='capture')return{full:{basis:bases[Math.min(steps,2)]}};
    if(command==='step'){steps++;return{receipt:{delivery:'delivered'}};}
  }};
  const result=await scanRecordedDifferences({original,conversion,driver,record:{append(){}},seed:'private',initialProgress:{}});
  assert.equal(result.error,undefined);assert.equal(result.executed_original_inputs,3);
  assert.equal(result.source_gaps.filter(gap=>gap.reason==='diagnostic_protocol_commitment_not_uniquely_mapped').length,0);
});
