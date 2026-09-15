from __future__ import annotations

import json
from pathlib import Path

from nsworkflow.config import init_config
from nsworkflow.flow import FlowRunner


SPEC = {
    "id": "verified-change",
    "version": "1",
    "steps": [
        {"id": "read", "kind": "call", "uses": "workspace.read", "with": {"path": "status.txt"}, "save_as": "before"},
        {"id": "approve", "kind": "approval", "message": "Apply change?", "accept_decisions": ["approve"]},
        {
            "id": "write",
            "kind": "call",
            "uses": "workspace.write",
            "mode": "action",
            "effect": "local_mutation",
            "with": {
                "path": "status.txt",
                "content": "status=fixed\n",
                "expected_sha256": "${context.before.sha256}",
            },
        },
        {
            "id": "verify",
            "kind": "verify",
            "uses": "workspace.verify",
            "with": {
                "argv": [
                    "python3",
                    "-c",
                    "from pathlib import Path; assert Path('status.txt').read_text() == 'status=fixed\\n'",
                ]
            },
        },
        {"id": "done", "kind": "end", "outcome": "VERIFIED_COMPLETE"},
    ],
}


def setup_workspace(tmp_path: Path) -> Path:
    init_config(tmp_path, overwrite=True)
    (tmp_path / "status.txt").write_text("status=broken\n", encoding="utf-8")
    return tmp_path


def test_flow_pauses_then_verifies_real_change(tmp_path: Path):
    root = setup_workspace(tmp_path)
    runner = FlowRunner(SPEC, root)
    waiting = runner.start(run_id="demo-approve")
    assert waiting["status"] == "WAITING_APPROVAL"
    assert (root / "status.txt").read_text() == "status=broken\n"

    done = runner.decide("demo-approve", "approve", actor="test")
    assert done["status"] == "COMPLETED"
    assert done["outcome"] == "VERIFIED_COMPLETE"
    assert done["context"]["verifications"]["verify"]["ok"] is True
    assert (root / "status.txt").read_text() == "status=fixed\n"
    assert (root / ".nsworkflow" / "runs" / "demo-approve.events.jsonl").exists()


def test_reject_blocks_without_mutation(tmp_path: Path):
    root = setup_workspace(tmp_path)
    runner = FlowRunner(SPEC, root)
    waiting = runner.start(run_id="demo-reject")
    assert waiting["status"] == "WAITING_APPROVAL"

    blocked = runner.decide("demo-reject", "reject", actor="test")
    assert blocked["status"] == "BLOCKED"
    assert blocked["blocked_reason"] == "APPROVAL_REJECTED"
    assert (root / "status.txt").read_text() == "status=broken\n"


def test_verified_complete_requires_verification(tmp_path: Path):
    root = setup_workspace(tmp_path)
    spec = {
        "id": "bad-finish",
        "version": "1",
        "steps": [{"id": "done", "kind": "end", "outcome": "VERIFIED_COMPLETE"}],
    }
    runner = FlowRunner(spec, root)
    state = runner.start(run_id="bad-finish")
    assert state["status"] == "FAILED"
    assert state["failure"] == "VERIFIED_COMPLETE_WITHOUT_VERIFICATION"


def test_run_state_is_json_and_resumable(tmp_path: Path):
    root = setup_workspace(tmp_path)
    runner = FlowRunner(SPEC, root)
    runner.start(run_id="persisted")
    state_path = root / ".nsworkflow" / "runs" / "persisted.json"
    state = json.loads(state_path.read_text())
    assert state["status"] == "WAITING_APPROVAL"

    new_runner = FlowRunner(SPEC, root)
    done = new_runner.decide("persisted", "approve")
    assert done["outcome"] == "VERIFIED_COMPLETE"
