from __future__ import annotations

import json
from pathlib import Path

import pytest

from nsworkflow.config import init_config
from nsworkflow.core import PolicyError, Workspace


def make_ws(tmp_path: Path) -> Workspace:
    init_config(tmp_path, overwrite=True)
    return Workspace(tmp_path)


def test_path_escape_is_denied(tmp_path: Path):
    ws = make_ws(tmp_path)
    with pytest.raises(PolicyError, match="escapes workspace"):
        ws.read_text("../outside.txt")


def test_absolute_path_is_denied(tmp_path: Path):
    ws = make_ws(tmp_path)
    with pytest.raises(PolicyError, match="absolute paths"):
        ws.read_text("/etc/passwd")


def test_secret_like_paths_are_denied(tmp_path: Path):
    ws = make_ws(tmp_path)
    (tmp_path / ".env").write_text("TOKEN=x\n", encoding="utf-8")
    with pytest.raises(PolicyError, match="secret-like"):
        ws.read_text(".env")
    with pytest.raises(PolicyError, match="secret-like"):
        ws.write_text("private.pem", "x")


def test_atomic_write_and_cas(tmp_path: Path):
    ws = make_ws(tmp_path)
    first = ws.write_text("hello.txt", "one\n")
    second = ws.write_text("hello.txt", "two\n", expected_sha256=first["after_sha256"])
    assert second["ok"] is True
    assert (tmp_path / "hello.txt").read_text() == "two\n"
    with pytest.raises(PolicyError, match="compare-and-swap"):
        ws.write_text("hello.txt", "three\n", expected_sha256=first["after_sha256"])


def test_symlink_escape_is_denied(tmp_path: Path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "x.txt").write_text("nope", encoding="utf-8")
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    ws = make_ws(tmp_path)
    with pytest.raises(PolicyError, match="escapes workspace"):
        ws.read_text("link/x.txt")


def test_unlisted_command_is_denied(tmp_path: Path):
    ws = make_ws(tmp_path)
    with pytest.raises(PolicyError, match="not allowed"):
        ws.run(["sh", "-c", "echo nope"])


def test_allowlisted_command_runs_and_writes_receipt(tmp_path: Path):
    ws = make_ws(tmp_path)
    result = ws.verify(["python3", "-c", "print('ok')"])
    assert result["ok"] is True
    assert result["stdout"].strip() == "ok"
    receipt = tmp_path / result["receipt"]
    assert receipt.is_file()
    data = json.loads(receipt.read_text())
    assert data["action"] == "verify"
    assert data["exit_code"] == 0


def test_read_receipt_contains_digest(tmp_path: Path):
    ws = make_ws(tmp_path)
    (tmp_path / "a.txt").write_text("abc", encoding="utf-8")
    result = ws.read_text("a.txt")
    assert len(result["sha256"]) == 64
    assert result["content"] == "abc"


def test_status_reports_guardrail_boundary(tmp_path: Path):
    ws = make_ws(tmp_path)
    status = ws.status()
    assert status["workspace"] == str(tmp_path.resolve())
    assert status["note"] == "guardrails are not an OS sandbox"
