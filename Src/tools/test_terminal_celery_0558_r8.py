from __future__ import annotations
import hashlib
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("NORM_ROOT", r"C:\Norm")
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"core"))

from norm_runtime.terminal_celery import DB, check_deterministically
import norm_runtime.terminal_conclusion as terminal


def test_db5_and_no_executor():
    assert DB==5
    import inspect
    assert "ThreadPoolExecutor" not in inspect.getsource(terminal.TerminalConclusion._verify)
    assert "submit_verify" in inspect.getsource(terminal.TerminalConclusion._verify)
    assert "time.sleep(3)" in inspect.getsource(terminal.TerminalConclusion._verify)


def test_file_check_true_false(tmp_path):
    file=tmp_path/"example.txt"
    file.write_text("hello world",encoding="utf-8")
    assert check_deterministically("file_exists",{"path":str(file)})[0]=="TRUE"
    assert check_deterministically("file_exists",{"path":str(tmp_path/"missing.txt")})[0]=="FALSE"
    assert check_deterministically("directory_exists",{"path":str(tmp_path)})[0]=="TRUE"
    assert check_deterministically("directory_exists",{"path":str(file)})[0]=="FALSE"


def test_hash_and_literal_checks(tmp_path):
    file=tmp_path/"example.txt"
    data="hello world".encode("utf-8")
    file.write_bytes(data)
    digest=hashlib.sha256(data).hexdigest()
    assert check_deterministically("sha256_matches",{"path":str(file),"sha256":digest})[0]=="TRUE"
    assert check_deterministically("sha256_matches",{"path":str(file),"sha256":"0"*64})[0]=="FALSE"
    assert check_deterministically("sha256_matches",{"path":str(file),"sha256":"bad"})[0]=="UNKNOWN"
    assert check_deterministically("text_contains",{"path":str(file),"text":"hello"})[0]=="TRUE"
    assert check_deterministically("text_contains",{"path":str(file),"text":"goodbye"})[0]=="FALSE"


def test_relative_path_not_checked():
    assert check_deterministically("file_exists",{"path":"relative/path"})[0]=="UNKNOWN"


def test_portable_manifest_celery_pins():
    root=Path(__file__).resolve().parents[1]
    m=json.loads((root/"package-manifest.json").read_text(encoding="utf-8"))
    assert m["version"]=="0.55.8r8"
    assert m["terminal_conclusion"]["redis_db"]==5
    assert m["terminal_conclusion"]["worker_processes"]==5
    req=(root/"tools"/"requirements-lock.txt").read_text(encoding="utf-8")
    assert "celery==5.6.3" in req
    assert "redis==6.4.0" in req
    assert "redis==8.1.0" not in req
    assert (root/"tools"/"start_terminal_workers.py").is_file()
