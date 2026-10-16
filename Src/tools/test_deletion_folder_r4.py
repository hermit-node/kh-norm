"""Isolated reversible trash lifecycle tests; never touches live Redis/files."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
from norm_runtime.deletion_queue import RedisDeletionQueue

class MemoryRedis:
    def __init__(self):
        self.hashes = {}
    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[str(field)] = value
    def hget(self, key, field):
        return self.hashes.get(key, {}).get(str(field))
    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))
    def hlen(self, key):
        return len(self.hashes.get(key, {}))
    def hdel(self, key, *fields):
        for field in fields:
            self.hashes.get(key, {}).pop(str(field), None)
    def hexists(self, key, field):
        return str(field) in self.hashes.get(key, {})
    def xrange(self, *args, **kwargs):
        return []
    def delete(self, *keys):
        for key in keys:
            self.hashes.pop(key, None)

class FolderTrashTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="norm-trash-r4-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.redis = MemoryRedis()
        with patch("norm_runtime.deletion_queue.redis.Redis", return_value=self.redis):
            self.queue = RedisDeletionQueue(host="localhost", port=6379, db=2, stream="test",
                trash_root=str(self.root / "trash"))

    def test_file_and_folder_full_lifecycle(self):
        file = self.root / "readme.txt"
        file.write_text("example")
        folder = self.root / "nested"
        (folder / "empty").mkdir(parents=True)
        (folder / "sub").mkdir(parents=True)
        (folder / "sub" / "data.bin").write_bytes(bytes(range(100)))
        first = self.queue.stage_path(file, reason="test")
        second = self.queue.stage_path(folder, reason="test")
        self.assertFalse(file.exists())
        self.assertFalse(folder.exists())
        self.assertEqual(second["kind"], "directory")
        self.assertEqual(second["size_bytes"], 100)
        items = self.queue.list_items()
        self.assertEqual({v["kind"] for v in items}, {"file", "directory"})
        index = json.loads(self.queue.index_path.read_text("utf8"))
        self.assertEqual(len(index["items"]), 2)
        self.assertEqual(len(self.queue.restore(second["deletion_id"])["restored"]), 1)
        self.assertEqual((folder / "sub" / "data.bin").read_bytes(), bytes(range(100)))
        self.assertTrue((folder / "empty").is_dir())
        self.assertEqual(len(self.queue.restore(first["deletion_id"])["restored"]), 1)
        self.assertEqual(file.read_text(), "example")
        self.assertEqual(self.queue.list_items(), [])

    def test_directory_purge_known_origin(self):
        directory = self.root / "discard"
        directory.mkdir()
        (directory / "file").write_text("data")
        item = self.queue.stage_path(directory, reason="test")
        self.assertEqual(self.queue.purge_all()["purged"], 1)
        self.assertFalse((self.queue.trash_root / item["trash_name"]).exists())
        self.assertEqual(self.queue.list_items(), [])

    def test_folder_conflict_does_not_overwrite(self):
        folder = self.root / "keep"
        folder.mkdir()
        (folder / "note").write_text("old")
        item = self.queue.stage_path(folder, reason="test")
        folder.mkdir()
        (folder / "note").write_text("new")
        ret = self.queue.restore(item["deletion_id"])
        self.assertEqual(ret["conflicts"][0]["reason"], "destination_exists")
        self.assertEqual((folder / "note").read_text(), "new")
        self.assertTrue((self.queue.trash_root / item["trash_name"]).exists())

    def test_legacy_orphan_folder_is_visible_and_held(self):
        orphan = self.queue.trash_root / "historical-unknown"
        (orphan / "child").mkdir(parents=True)
        (orphan / "child" / "file").write_text("keep")
        items = self.queue.list_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["kind"], "directory")
        self.assertEqual(items[0]["state"], "orphaned")
        self.assertEqual(items[0]["original_path"], "")
        self.assertEqual(len(json.loads(self.queue.index_path.read_text())["items"]), 1)
        self.assertEqual(self.queue.restore(items[0]["deletion_id"])["conflicts"][0]["reason"],
                         "original_path_unknown")
        self.assertEqual(self.queue.purge_all()["held"], 1)
        self.assertTrue((orphan / "child" / "file").is_file())

    def test_cross_volume_folder_copy_verified(self):
        source = self.root / "otherdrive"
        (source / "a").mkdir(parents=True)
        (source / "a" / "x").write_bytes(b"xyz")
        with patch.object(self.queue, "_same_volume", return_value=False):
            record = self.queue.stage_path(source, reason="test")
        self.assertFalse(source.exists())
        self.assertEqual(record["kind"], "directory")
        self.assertEqual(len(self.queue.restore(record["deletion_id"])["restored"]), 1)
        self.assertEqual((source / "a" / "x").read_bytes(), b"xyz")

    def test_cannot_stage_trash_or_ancestor(self):
        with self.assertRaises(PermissionError):
            self.queue.stage_path(self.queue.trash_root, reason="test")
        with self.assertRaises(PermissionError):
            self.queue.stage_path(self.root, reason="test")

if __name__ == "__main__":
    unittest.main(verbosity=2)
