import {readFileSync} from 'node:fs'; import {createInterface} from 'node:readline';
const manifest=JSON.parse(readFileSync(process.argv[2])), behavior=process.argv[3];
let version=0,consumption=null,basis=null,pending=null,unit=null,next=0;
const send=v=>process.stdout.write(JSON.stringify(v)+'\r\n');
const reply=(m,message_type,key,value)=>send({schema:m.schema,message_type,session_id:m.session_id,recovery_epoch:m.recovery_epoch,request_id:m.request_id,[key]:value});
send({schema:'sts2.policy-runtime/agent-session-1',message_type:'ready',adapter:manifest.adapter});
for await(const line of createInterface({input:process.stdin})) {
 const m=JSON.parse(line);
 if(m.message_type==='consume'){const advanced=unit!==m.input.consumption_unit.id;
  pending={unit:m.input.consumption_unit.id,input:m.input,version:version+Number(advanced),consumption:advanced?`c-${version+1}`:consumption};
  reply(m,'consumed','completion',{acquisition_id:m.input.acquisition_id,input_spec:manifest.input.input_spec,continuity_token:m.input.continuity_token,
    previous_consumption_id:consumption,consumption_id:pending.consumption,state_version:pending.version,advanced});
 }else if(m.message_type==='consume_ack'){({unit,version,consumption}=pending);basis=pending.input;pending=null;
 }else if(m.message_type==='next'){
  next++;if(behavior==='hang_next'||behavior==='hang_second_next'&&next===2)continue;
  if(behavior==='late_choice')await new Promise(r=>setTimeout(r,500));
  reply(m,'directive','output',{continuity_token:m.input.continuity_token,consumption_id:consumption,state_version:version,
    directive:basis.observation.status==='terminal'?{type:'close',reason:'native_terminal_observed'}:{type:'act',basis_acquisition_id:basis.acquisition_id,
      selection:{kind:'handle',action_id:basis.catalog[0].choice_id},scores:null}});
 }
}
