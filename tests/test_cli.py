from __future__ import annotations

import json
from pathlib import Path

from nsworkflow.cli import main


def test_init_and_doctor(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["nsw", "init", str(tmp_path)])
    assert main() == 0
    assert (tmp_path / ".nsworkflow" / "config.json").is_file()
    capsys.readouterr()

    monkeypatch.setattr("sys.argv", ["nsw", "doctor", "--workspace", str(tmp_path)])
    assert main() == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["workspace_exists"] is True
    assert data["mcp_dependency"] is True


def test_demo_produces_observed_pass(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["nsw", "demo"])
    assert main() == 0
    out = capsys.readouterr().out
    assert "RESULT  PASS" in out
    assert "requested state was observed" in out


def test_connect_prints_five_tool_surface(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        ["nsw", "connect", "--client", "generic", "--workspace", str(tmp_path)],
    )
    assert main() == 0
    data = json.loads(capsys.readouterr().out)
    assert data["transport"] == "stdio"
    assert data["tools"] == ["ns_status", "ns_read", "ns_write", "ns_run", "ns_verify"]


def test_cli_rejects_shell_command(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["nsw", "init", str(tmp_path)])
    assert main() == 0
    capsys.readouterr()
    monkeypatch.setattr(
        "sys.argv",
        ["nsw", "run", "--workspace", str(tmp_path), "--", "sh", "-c", "echo nope"],
    )
    assert main() == 2
    err = capsys.readouterr().err
    assert "not allowed" in err
