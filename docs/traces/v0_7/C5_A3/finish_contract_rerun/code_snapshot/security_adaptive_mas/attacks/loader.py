"""Load trusted local configuration and fail before any model request."""
import hashlib
import json
from pathlib import Path
from .contracts import AttackSpec
from .registry import default_registry


def load_attack_spec(path, *, scenario_id=None, attack_mode=None, registry=None):
    path=Path(path)
    data=json.loads(path.read_text(encoding='utf-8'))
    if data.get('attack') == 'none':
        if attack_mode not in (None, 'none'):
            raise ValueError('attack_requested_but_configuration_is_none')
        return None
    if data.get('status') == 'BLOCKED_ATTACK_SURFACE':
        raise ValueError('BLOCKED_ATTACK_SURFACE:' + data['reason'])
    for canonical, legacy in {'scenario_id':'target_case', 'surface_type':'surface',
                              'payload_sha256':'payload_hash', 'evaluator_id':'evaluation_rule'}.items():
        if canonical in data:
            if legacy in data and data[legacy] != data[canonical]:
                raise ValueError('conflicting_attack_alias:' + canonical)
            data[legacy]=data.pop(canonical)
    required={'attack_id','surface','target_case','source_selector','payload_path','payload_hash',
              'desired_deviation','evaluation_rule'}
    if not required <= data.keys():
        raise ValueError(f'Attack spec missing keys: {required - data.keys()}')
    allowed=required|{'attack','protected_targets','target_email_id','source_tool','field_path',
        'receiver_policy','target_type','target_id','source_conversation_id','evaluator_params'}
    if set(data)-allowed:
        raise ValueError('new_attack_spec_requires_exact_declared_fields')
    params=data.get('evaluator_params',{})
    if not isinstance(params,dict):
        raise ValueError('evaluator_params_requires_object')
    for key in ('target_type','target_id','target_email_id'):
        if key in params:
            if key in data and data[key] != params[key]:
                raise ValueError('conflicting_evaluator_parameter:' + key)
            data[key]=params[key]
    payload=Path(data['payload_path'])
    if not payload.is_absolute():
        payload=path.parent/payload
    digest=hashlib.sha256(payload.read_bytes()).hexdigest()
    if digest != data['payload_hash']:
        raise ValueError('attack_payload_hash_mismatch')
    spec=AttackSpec(data['attack_id'],data['surface'],data['target_case'],data['source_selector'],
        str(payload.resolve()),digest,data['desired_deviation'],data['evaluation_rule'],
        tuple(data.get('protected_targets',())),data.get('target_email_id'),
        *[data.get(k) for k in ('source_tool','field_path','receiver_policy','target_type','target_id','source_conversation_id')],
        json.dumps(params,sort_keys=True))
    validate_spec(spec, scenario_id=scenario_id, attack_mode=attack_mode, registry=registry)
    if attack_mode is not None and data.get('attack',attack_mode) != attack_mode:
        raise ValueError('attack_mode_configuration_mismatch')
    return spec


def validate_spec(spec, *, scenario_id=None, attack_mode=None, registry=None):
    registry=default_registry() if registry is None else registry
    if scenario_id is not None and scenario_id != spec.scenario_id:
        raise ValueError('attack_target_case_does_not_match_scenario')
    surface=registry.surface(spec)
    if attack_mode is not None and attack_mode not in surface.modes:
        raise ValueError('attack_mode_surface_mismatch')
    surface.validate(spec)
    registry.evaluator(spec).validate(spec)
    if hashlib.sha256(Path(spec.payload_path).read_bytes()).hexdigest() != spec.payload_sha256:
        raise ValueError('attack_payload_hash_mismatch')
    return spec


def load_for_run(mode, path, scenario_id, *, registry=None):
    if mode == 'none':
        return None
    spec=load_attack_spec(path,scenario_id=scenario_id,attack_mode=mode,registry=registry)
    if spec is None:
        raise ValueError('attack_requested_but_no_spec')
    registry=default_registry() if registry is None else registry
    if registry.surface(spec).phase != 'tool_result':
        raise ValueError('BLOCKED_attack_phase_not_wired_in_modular_runtime')
    return spec
