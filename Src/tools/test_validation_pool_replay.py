from __future__ import annotations

import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))


class WatchError(Exception):
    pass


redis_stub = types.ModuleType("redis")
redis_stub.Redis = object
redis_stub.WatchError = WatchError
sys.modules.setdefault("redis", redis_stub)

from norm_runtime.live_log import RedisTaskLog


class FakePipeline:
    def __init__(self, client):
        self.client = client
        self.ops = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def watch(self, *_args):
        return None

    def hget(self, key, field):
        return self.client.hget(key, field)

    def multi(self):
        return None

    def hset(self, key, field, value):
        self.ops.append(("hset", key, field, value))
        return self

    def hdel(self, key, *fields):
        self.ops.append(("hdel", key, tuple(fields), None))
        return self

    def execute(self):
        for op, key, field, value in self.ops:
            if op == "hset":
                self.client.hset(key, field, value)
            elif op == "hdel":
                self.client.hdel(key, *field)
        self.ops.clear()
        return []


class FakeRedis:
    def __init__(self):
        self.hashes = {}

    def pipeline(self, transaction=True):
        return FakePipeline(self)

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(str(field))

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[str(field)] = value
        return 1

    def hscan_iter(self, key):
        yield from list(self.hashes.get(key, {}).items())

    def hdel(self, key, *fields):
        bucket = self.hashes.get(key, {})
        removed = 0
        for field in fields:
            if str(field) in bucket:
                removed += 1
                del bucket[str(field)]
        return removed


def main() -> None:
    client = FakeRedis()
    live = RedisTaskLog(client, prefix="test:task", validation_prefix="test:validation")
    target = r"C:\\docs\\manual.pdf"
    desc = "requested PDF contents"
    scope1 = 'read_file:{"max_bytes":20000,"path":"C:\\\\docs\\\\manual.pdf","start_byte":0}'
    raw1 = {"ok": True, "tool": "read_file", "path": target, "content": "FIRST", "start_byte": 0}
    saved1 = live.record_global_validations("t", "s", [{
        "record_id": "new", "tool": "read_file", "target": target, "description": desc,
        "scope": scope1, "value": '{"fingerprint":"abc","start":0}',
        "replay_result": raw1, "replay_bytes": 123,
    }])[0]
    rid1 = saved1["record_id"]
    assert saved1["replay_available"] is True and saved1["replay_bytes"] == 123
    candidates = live.validation_candidates(tool="read_file", target=target, description=desc)
    assert candidates[0]["record_id"] == rid1
    assert candidates[0]["replay_available"] is True
    assert "FIRST" not in json.dumps(candidates[0])
    metadata_raw = client.hget(live._global_validation_key(), rid1)
    replay_raw = client.hget(live._global_validation_replay_key(), rid1)
    assert "FIRST" not in str(metadata_raw)
    assert "FIRST" in str(replay_raw)
    assert isinstance(candidates[0]["age_seconds"], int)
    assert live.validation_replay(rid1) == raw1

    raw1b = {"ok": True, "tool": "read_file", "path": target, "content": "FIRST-NEW-COPY", "start_byte": 0}
    saved1b = live.record_global_validations("t", "s", [{
        "record_id": rid1, "tool": "read_file", "target": target, "description": desc,
        "scope": scope1, "value": '{"fingerprint":"abc","start":0}',
        "replay_result": raw1b, "replay_bytes": 140,
    }])[0]
    assert saved1b["num_checks"] == 2
    assert live.validation_replay(rid1)["content"] == "FIRST-NEW-COPY"

    scope2 = 'read_file:{"max_bytes":20000,"path":"C:\\\\docs\\\\manual.pdf","start_byte":20000}'
    saved2 = live.record_global_validations("t", "s", [{
        "record_id": "new", "tool": "read_file", "target": target, "description": desc,
        "scope": scope2, "value": '{"fingerprint":"abc","start":20000}',
        "replay_result": {"ok": True, "content": "SECOND"}, "replay_bytes": 80,
    }])[0]
    assert saved2["record_id"] != rid1
    assert len(live.validation_candidates(tool="read_file", target=target, description=desc)) == 2

    # A changed canonical value must never inherit the old raw replay if the fresh
    # observation could not be retained as a replay payload.
    changed = live.record_global_validations("t", "s", [{
        "record_id": rid1, "tool": "read_file", "target": target, "description": desc,
        "scope": scope1, "value": '{"fingerprint":"changed","start":0}',
    }])[0]
    assert changed["num_checks"] == 1 and changed["previous_value"]
    assert changed["replay_available"] is False
    assert live.validation_replay(rid1) is None

    assert live.invalidate_validation_target(target) == 2
    assert live.validation_candidates(tool="read_file", target=target, description=desc) == []
    print("PASS Redis verification pool owns scoped hot replay payloads without exposing them to N1 candidate context")


if __name__ == "__main__":
    main()
