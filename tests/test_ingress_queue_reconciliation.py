from __future__ import annotations

import sys
import threading
import types
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
SRC = HERE / "src" / "Norm" / "core"
sys.path.insert(0, str(SRC))

redis_mod = types.ModuleType("redis")
class ResponseError(Exception):
    pass
redis_mod.ResponseError = ResponseError
redis_mod.Redis = object
sys.modules.setdefault("redis", redis_mod)

from norm_runtime.prompt_ingress import GuiPromptDispatcher


class FakeRedis:
    def __init__(self):
        self.rows = {
            "4-0": {"kind": "prompt", "prompt_id": "p4", "message": "live", "enqueued_at": "now"}
        }
        self.pending = {
            "1-0": {"message_id": "1-0", "consumer": "old"},
            "2-0": {"message_id": "2-0", "consumer": "old"},
            "3-0": {"message_id": "3-0", "consumer": "same"},
            "4-0": {"message_id": "4-0", "consumer": "same"},
        }
        self.acked = []
        self.dispatching = {}
        self.reads = []

    def xgroup_create(self, *args, **kwargs):
        raise ResponseError("BUSYGROUP Consumer Group name already exists")

    def xpending_range(self, *args, **kwargs):
        return list(self.pending.values())

    def xrange(self, stream, min="-", max="+", count=None, **kwargs):
        if min == "-" and max == "+":
            ids = sorted(self.rows)
        elif min == max:
            ids = [min] if min in self.rows else []
        else:
            ids = []
        if count is not None:
            ids = ids[:count]
        return [(item_id, self.rows[item_id]) for item_id in ids]

    def hkeys(self, key):
        return list(self.dispatching)

    def hlen(self, key):
        return 0

    def smembers(self, key):
        return set()

    def sismember(self, key, value):
        return False

    def xinfo_groups(self, stream):
        return [{"name": "g", "pending": len(self.pending), "lag": 0, "last-delivered-id": "4-0"}]

    def xack(self, stream, group, *ids):
        removed = 0
        for item_id in ids:
            if item_id in self.pending:
                del self.pending[item_id]
                removed += 1
                self.acked.append(item_id)
        return removed

    def hdel(self, key, *ids):
        for item_id in ids:
            self.dispatching.pop(item_id, None)
        return 0

    def hget(self, key, item_id):
        return self.dispatching.get(item_id)

    def xclaim(self, *args, **kwargs):
        return []

    def xreadgroup(self, group, consumer, streams, count=1, block=None):
        if self.reads:
            return [("s", [self.reads.pop(0)])]
        return []


def dispatcher(fake: FakeRedis) -> GuiPromptDispatcher:
    obj = GuiPromptDispatcher.__new__(GuiPromptDispatcher)
    obj.redis = fake
    obj.stream = "s"
    obj.group = "g"
    obj.dispatching_key = "d"
    obj.uncertain_key = "u"
    obj.suppressed_prompt_ids_key = "sup"
    obj.consumer = "same"
    obj.stop_event = threading.Event()
    return obj


fake = FakeRedis()
d = dispatcher(fake)
queued, uncertain = d.queue_stats()
assert (queued, uncertain) == (1, 0), (queued, uncertain)
assert set(fake.acked) == {"1-0", "2-0", "3-0"}, fake.acked
assert set(fake.pending) == {"4-0"}, fake.pending
print("PASS: raw pending=4 reconciles to one stream-backed live prompt")

fake.pending = {
    "9-0": {"message_id": "9-0", "consumer": "same"},
    "10-0": {"message_id": "10-0", "consumer": "same"},
}
fake.reads = [
    ("9-0", None),
    ("10-0", {"kind": "prompt", "prompt_id": "p10", "message": "ok"}),
]
item = d._next_entry()
assert item[0] == "10-0" and item[1]["message"] == "ok", item
assert "9-0" not in fake.pending
print("PASS: deleted pending payload is ACKed/skipped instead of killing dispatcher")
