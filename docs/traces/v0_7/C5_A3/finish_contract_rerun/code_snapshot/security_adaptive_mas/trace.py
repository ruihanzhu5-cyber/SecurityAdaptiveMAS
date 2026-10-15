from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class TraceLogger:
    def __init__(self, path: Path, mode: str = "offline_mock"):
        if mode not in {"offline_mock", "real_are_single", "real_are_multi",
                        "real_connectivity_probe"}:
            raise ValueError("Unknown execution mode")
        self.path = path
        self.mode = mode
        path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, kind: str, **fields):
        event = {"time_utc": datetime.now(timezone.utc).isoformat(),
                 "mode": self.mode, "kind": kind, **fields}
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        return event
