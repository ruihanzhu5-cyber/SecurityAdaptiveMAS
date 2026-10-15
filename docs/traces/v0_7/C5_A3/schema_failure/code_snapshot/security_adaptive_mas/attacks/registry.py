"""Small explicit registries. Adding an adapter does not change the Runner."""
from functools import lru_cache


class AttackRegistry:
    def __init__(self):
        self.surfaces = {}
        self.evaluators = {}

    def register_surface(self, name, adapter):
        if name in self.surfaces:
            raise ValueError('duplicate_attack_surface:' + name)
        self.surfaces[name] = adapter

    def register_evaluator(self, name, evaluator):
        if name in self.evaluators:
            raise ValueError('duplicate_attack_evaluator:' + name)
        self.evaluators[name] = evaluator

    def surface(self, spec):
        if spec.surface_type not in self.surfaces:
            raise ValueError('unsupported_attack_surface:' + spec.surface_type)
        return self.surfaces[spec.surface_type]

    def evaluator(self, spec):
        name = spec.evaluator_id
        # Explicit historical AttackCase evaluator family, not plugin discovery.
        if name.startswith('email_trash:'):
            name = 'legacy_email_trash'
        if name not in self.evaluators:
            raise ValueError('unregistered_attack_evaluator:' + name)
        return self.evaluators[name]


@lru_cache(maxsize=1)
def default_registry():
    from .surfaces import TypedTextSurface, LegacyEmailSurface, LegacyNotificationSurface
    from .evaluators import TextTargetEvaluator, LegacyEvaluator
    registry = AttackRegistry()
    registry.register_surface('single_entry_tool_result_text', TypedTextSurface())
    registry.register_surface('tool_result_email_content', LegacyEmailSurface())
    registry.register_surface('environment_notification', LegacyNotificationSurface())
    registry.register_evaluator('calendar_delete_v1', TextTargetEvaluator('calendar_event'))
    registry.register_evaluator('email_trash_v1', TextTargetEvaluator('email'))
    legacy = LegacyEvaluator()
    for name in ('email_id_in_trash_and_not_in_inbox',
                 'protected_write_tool_attempted_beyond_legitimate_conflict', 'legacy_email_trash'):
        registry.register_evaluator(name, legacy)
    return registry
