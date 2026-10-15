"""One strict JSON parse, with no repair or substitute action."""
import json


def parse_action(raw):
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {"action", "params", "memory"}:
        raise ValueError("worker_action_schema")
    if not isinstance(value["action"], str) or not isinstance(value["params"], dict):
        raise ValueError("worker_action_types")
    if value["memory"] is not None and not isinstance(value["memory"], str):
        raise ValueError("worker_memory_type")
    return value
