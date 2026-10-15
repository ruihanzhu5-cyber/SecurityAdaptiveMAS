"""One attack instance per Episode, with explicit opportunity and delivery records."""
from dataclasses import replace
import json
from pathlib import Path
from uuid import uuid4
from .contracts import AttackContext, AttackApplyResult
from .loader import validate_spec
from .registry import default_registry


class AttackRuntime:
    def __init__(self,spec,trace,*,scenario_id,registry=None,receiver_agent_id=None,receiver_prefixes=('worker_',),log_configuration=True):
        self.registry=default_registry() if registry is None else registry
        self.spec=validate_spec(spec,scenario_id=scenario_id,registry=self.registry)
        self.trace,self.scenario_id=trace,scenario_id
        self.surface=self.registry.surface(spec)
        self.evaluator=self.registry.evaluator(spec)
        self.receiver_agent_id,self.receiver_prefixes=receiver_agent_id,receiver_prefixes
        self.payload=Path(spec.payload_path).read_text(encoding='utf-8')
        self.state='CONFIGURED'
        self.reason='validated_configuration_loaded'
        self.source_calls=0
        self.events=[]
        self.injection_record=None
        if log_configuration:
            self.events.append(self.trace.log('attack_configured',attack_id=spec.attack_id,surface=spec.surface_type,
                               payload_hash=spec.payload_sha256,evaluator_id=spec.evaluator_id))

    def apply(self,context):
        if context.scenario_id!=self.scenario_id:
            self.state,self.reason='ERROR','attack_context_scenario_mismatch'
            self.events.append(self.trace.log('attack_runtime_error',attack_id=self.spec.attack_id,
                call_id=context.call_id,exception_type='ValueError',reason=self.reason))
            raise ValueError('attack_context_scenario_mismatch')
        if context.phase!=self.surface.phase or (context.phase=='tool_result' and context.tool_name!=self.surface.source_tool(self.spec)):
            return AttackApplyResult(False,False,context.tool_result,'declared_source_not_reached')
        if self.state=='ERROR':
            raise RuntimeError('attack_runtime_already_failed')
        self.source_calls+=1
        if self.state=='INJECTED':
            return AttackApplyResult(False,False,context.tool_result,'one_shot_already_injected')
        if not self.surface.receiver_allowed(context,self.receiver_agent_id,self.receiver_prefixes):
            result=AttackApplyResult(False,False,context.tool_result,'receiver_not_eligible')
        else:
            try:
                result=self.surface.apply(self.spec,context,self.payload)
            except (TypeError,ValueError) as error:
                self.state,self.reason='ERROR',str(error)
                self.events.append(self.trace.log('attack_runtime_error',attack_id=self.spec.attack_id,call_id=context.call_id,
                    agent_id=context.caller_id,source_tool=context.tool_name,exception_type=type(error).__name__,reason=str(error)))
                raise
        self.reason=result.reason
        if not result.applied:
            self.state='MATCH_MISS'
            record=self.trace.log('attack_surface_not_hit',attack_id=self.spec.attack_id,call_id=context.call_id,
                source_tool=context.tool_name,agent_id=context.caller_id,reason=result.reason)
            self.events.append(record)
            return result
        event_id=uuid4().hex
        self.state='INJECTED'
        self.injection_record={'attack_id':self.spec.attack_id,'attack_event_id':event_id,
            'surface':self.spec.surface_type,'source_selector':self.spec.source_selector,
            'field_path':self.spec.field_path,'source':context.tool_name,'agent_id':context.caller_id,
            'call_id':context.call_id,'original_text':result.original_text,'injected_text':result.injected_text,
            'payload_hash':self.spec.payload_sha256,'receiver_policy':self.spec.receiver_policy,
            'simulation_time':context.simulation_time,'one_shot_overlay':True}
        self.events.append(self.trace.log('attack_injected',**self.injection_record))
        return replace(result,attack_event_id=event_id)

    def bind_delivery(self,result,*,evidence_id,context):
        if not result.applied:
            return
        if result.attack_event_id!=self.injection_record['attack_event_id']:
            raise ValueError('attack_delivery_event_mismatch')
        if 'evidence_id' in self.injection_record:
            raise ValueError('attack_delivery_already_bound')
        self.injection_record['evidence_id']=evidence_id
        self.events.append(self.trace.log('attack_injected_delivered',attack_id=self.spec.attack_id,
            attack_event_id=result.attack_event_id,evidence_id=evidence_id,agent_id=context.caller_id,
            call_id=context.call_id,tool=context.tool_name))

    def finish(self,*,interrupted=False):
        if self.state=='CONFIGURED':
            self.state='NOT_REACHED'
            self.reason='episode_interrupted_before_declared_source' if interrupted else 'declared_source_never_called'
        result={'attack_id':self.spec.attack_id,'status':self.state,'reason':self.reason,
                'source_calls':self.source_calls,'episode_completed':not interrupted}
        self.events.append(self.trace.log('attack_runtime_finished',**result))
        return result

    def snapshot(self,scenario):
        return self.evaluator.snapshot(scenario,self.spec)

    def evaluate(self,events,*,initial,final,official_success,legitimate_task_completed,
                 stop_reason,interrupted=False,model_io_dir=None):
        return self.evaluator.evaluate(events,spec=self.spec,initial=initial,final=final,
            official_success=official_success,legitimate_task_completed=legitimate_task_completed,
            stop_reason=stop_reason,interrupted=interrupted,model_io_dir=model_io_dir)


class AttackOverlay:
    """Historical API adapter. New integrations consume AttackApplyResult."""
    def __init__(self,spec,trace,*,scenario_id,receiver_agent_id=None,receiver_prefixes=('worker_',)):
        self.runtime=AttackRuntime(spec,trace,scenario_id=scenario_id,receiver_agent_id=receiver_agent_id,
                                   receiver_prefixes=receiver_prefixes,log_configuration=False)
        self.spec,self.trace,self.scenario_id=spec,trace,scenario_id

    @property
    def applied(self):
        return self.runtime.state=='INJECTED'

    @property
    def record(self):
        return self.runtime.injection_record

    def apply_tool_result(self,result,*,tool_name,agent_id):
        context=AttackContext(self.scenario_id,agent_id,tool_name,None,'tool_result',result)
        return self.runtime.apply(context).modified_result

    def apply(self,text,*,kind,source,simulation_time=None):
        if kind!='environment':
            return text,None
        context=AttackContext(self.scenario_id,'agent_0',source,None,'environment_notification',text,simulation_time)
        result=self.runtime.apply(context)
        return result.modified_result,self.record if result.applied else None


def probe_attack(scenario,spec,allowed_tools,trace,*,registry=None):
    """Unified free typed-source probe. Not evidence of model exposure or harm."""
    runtime=AttackRuntime(spec,trace,scenario_id=scenario.scenario_id,registry=registry)
    source=runtime.surface.source_tool(spec)
    target_tool=runtime.evaluator.target_tool(spec)
    if runtime.surface.phase!='tool_result':
        raise ValueError('BLOCKED_ATTACK_SURFACE:phase_has_no_verified_free_tool_probe')
    if source not in allowed_tools:
        raise ValueError('BLOCKED_TOOL_SCOPE:source tool not selected')
    if target_tool is None or target_tool not in allowed_tools:
        raise ValueError('BLOCKED_TOOL_SCOPE:target tool unavailable')
    app=scenario.get_app(source.split('__',1)[0])
    before=json.dumps(app.get_state(),sort_keys=True,ensure_ascii=False,default=str)
    application=None
    for value,metadata in runtime.surface.read_probe(scenario,spec):
        context=AttackContext(scenario.scenario_id,'agent_0',source,'free_probe','tool_result',value)
        application=runtime.apply(context)
        if application.applied:
            break
    if application is None or not application.applied:
        raise ValueError('BLOCKED_ATTACK_SURFACE:exact ID not in real typed read')
    if before!=json.dumps(app.get_state(),sort_keys=True,ensure_ascii=False,default=str):
        raise RuntimeError('SYSTEMIC_STATE_MUTATION')
    return {'scenario_valid':True,'attack_configuration_valid':True,'surface_adapter_supported':True,
        'evaluator_available':True,'source_exact_verified':True,'target_tool_allowed':True,
        'source_tool':source,'target_tool':target_tool,'returned_python_type':type(value).__module__+'.'+type(value).__name__,
        'page_offset':metadata.get('page_offset',0),'initial_target_state':runtime.snapshot(scenario),
        'runtime_status':runtime.finish()}
