from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from norm_runtime.plugin_settings import _backup, _dedupe_backup_dir, _migrate_legacy_backup_dir


def test_identical_content_does_not_create_new_backup_slot():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        settings = root / "config" / "plugins.ini"
        settings.parent.mkdir(parents=True)
        settings.write_text("[x]\na = 1\n", encoding="utf-8")
        backups = root / "backups" / "plugins-ini"
        first = _backup(settings, backup_dir=backups, limit=12)
        second = _backup(settings, backup_dir=backups, limit=12)
        files = list(backups.glob("plugins-*.ini"))
        assert first == second
        assert len(files) == 1


def test_duplicate_history_collapses_to_newest_copy():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        backups = root / "backups" / "plugins-ini"
        backups.mkdir(parents=True)
        older = backups / "plugins-20260101-000000-a.ini"
        newer = backups / "plugins-20260102-000000-b.ini"
        older.write_text("same", encoding="utf-8")
        newer.write_text("same", encoding="utf-8")
        os.utime(older, (1, 1))
        os.utime(newer, (2, 2))
        kept = _dedupe_backup_dir(backups)
        assert len(list(backups.glob("plugins-*.ini"))) == 1
        assert next(iter(kept.values())).name == newer.name


def test_retention_limit_counts_unique_content_only():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        settings = root / "config" / "plugins.ini"
        settings.parent.mkdir(parents=True)
        backups = root / "backups" / "plugins-ini"
        for i in range(14):
            settings.write_text(f"[x]\na = {i}\n", encoding="utf-8")
            _backup(settings, backup_dir=backups, limit=12)
        assert len(list(backups.glob("plugins-*.ini"))) == 12
        settings.write_text("[x]\na = 13\n", encoding="utf-8")
        _backup(settings, backup_dir=backups, limit=12)
        assert len(list(backups.glob("plugins-*.ini"))) == 12


def test_legacy_config_backup_location_migrates_to_runtime_backups():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        settings = root / "config" / "plugins.ini"
        settings.parent.mkdir(parents=True)
        settings.write_text("[x]\na = 1\n", encoding="utf-8")
        legacy = root / "config" / "backups" / "plugins-ini"
        legacy.mkdir(parents=True)
        old = legacy / "plugins-20260101-000000-abc.ini"
        old.write_text("old", encoding="utf-8")
        backups = root / "backups" / "plugins-ini"
        _migrate_legacy_backup_dir(settings, backups)
        assert (backups / old.name).is_file()
        assert not old.exists()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
        print("PASS", fn.__name__)
