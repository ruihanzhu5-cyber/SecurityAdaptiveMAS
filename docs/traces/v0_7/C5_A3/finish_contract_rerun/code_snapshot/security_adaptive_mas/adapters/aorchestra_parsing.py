"""One strict JSON parse, with no repair or substitute action."""
import json


def parse_action(raw):
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("worker_action_schema")
    fields=set(value)
    if fields != {"action", "params", "memory"} and not (fields == {"action", "params"} and value.get("action") == "finish"):
        raise ValueError("worker_action_schema")
    if not isinstance(value["action"], str) or not isinstance(value["params"], dict):
        raise ValueError("worker_action_types")
    if "memory" in value and value["memory"] is not None and not isinstance(value["memory"], str):
        raise ValueError("worker_memory_type")
    return value
