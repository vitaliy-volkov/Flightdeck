#!/usr/bin/env python3
import json
import sys

request = json.loads(sys.stdin.readline())
print(json.dumps({
    "ok": True,
    "output": {"heading": "Safe plugin", "run_id": request["run_id"]},
    "events": [],
    "error": None,
}))
