// Programmed protocol conformance only. No numerical model or native backend.
import { readFileSync } from 'node:fs';
import readline from 'node:readline';
const manifest = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const scenario = process.argv[3] ?? 'normal';
let next = null, pending = null, lastUnit = null, version = 0, consumed = null, basis = null, serial = 0;
const send = (type, id, field, value, common = next) => process.stdout.write(JSON.stringify({schema:'sts2.policy-runtime/agent-session-1',message_type:type,session_id:common.session_id,recovery_epoch:common.recovery_epoch,request_id:id,[field]:value})+'\n');
const directive = value => send('directive', next.request_id, 'output', {continuity_token:next.input.continuity_token,consumption_id:consumed,state_version:version,directive:value});
process.stdout.write(JSON.stringify({schema:'sts2.policy-runtime/agent-session-1',message_type:'ready',adapter:manifest.adapter})+'\n');
for await (const line of readline.createInterface({input:process.stdin})) {
 const m=JSON.parse(line);
 if(m.message_type==='next') { next=m; send('query','child-query-'+(++serial),'input',{method:'current',arguments:{eager_scope:['persistent','interaction','referents','catalog'],expected_snapshot_id:null}}); }
 else if(m.message_type==='query_result') {
  const a=m.result, o=a.value.observation;
  if(scenario==='hang_after_query') continue;
  const unit=JSON.stringify([o.catalog.stream_generation,o.snapshot_id,o.owner_occurrence.occurrence_id,o.owner_occurrence.binding_revision,o.owner_occurrence.focus_occurrence,o.revision]);
  const terminal=o.interaction.kind==='game_over'&&o.interaction.stage==='summary';
  if(unit===lastUnit||(!a.value.catalog.length&&!terminal)) { directive({type:'await',after_cursor:next.input.received_cursor,condition:'any_event',timeout_ms:250}); continue; }
  pending={a,unit,terminal,report:{acquisition_id:a.acquisition_id,input_spec:manifest.input.input_spec,continuity_token:next.input.continuity_token,previous_consumption_id:consumed,consumption_id:'consumption-'+(++serial),state_version:version+1,advanced:true}};
  send('consumed','child-consume-'+(++serial),'completion',pending.report);
 }
 else if(m.message_type==='consume_ack') {
  if(scenario==='hang_after_ack') continue;
  if(!pending||m.completion.consumption_id!==pending.report.consumption_id) throw Error('ACK mismatch');
  version=m.completion.state_version;consumed=m.completion.consumption_id;basis=m.completion.acquisition_id;lastUnit=pending.unit;
  directive(pending.terminal?{type:'close',reason:'native_ready_summary_task_complete'}:{type:'act',basis_acquisition_id:basis,selection:{kind:'handle',action_id:pending.a.value.catalog[0].action_id},scores:null});pending=null;
 }
}
