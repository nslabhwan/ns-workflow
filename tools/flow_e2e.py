from __future__ import annotations

import tempfile
from pathlib import Path

from nsworkflow.config import init_config
from nsworkflow.flow import FlowRunner, verified_change_template


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="nsw-flow-e2e-"))
    init_config(root, overwrite=True)
    target = root / "status.txt"
    target.write_text("status=broken\n", encoding="utf-8")

    runner = FlowRunner(verified_change_template(), root)
    waiting = runner.start()
    assert waiting["status"] == "WAITING_APPROVAL"
    assert target.read_text(encoding="utf-8") == "status=broken\n"

    done = runner.decide(waiting["run_id"], "approve", actor="clean-room-e2e")
    assert done["status"] == "COMPLETED"
    assert done["outcome"] == "VERIFIED_COMPLETE"
    assert target.read_text(encoding="utf-8") == "status=fixed\n"
    verify = done["context"]["verifications"]["verify_change"]
    assert verify["ok"] is True

    state_path = root / ".nsworkflow" / "runs" / f"{waiting['run_id']}.json"
    events_path = root / ".nsworkflow" / "runs" / f"{waiting['run_id']}.events.jsonl"
    assert state_path.exists()
    assert events_path.exists()

    print("FLOW_E2E PASS")
    print(f"run_id={waiting['run_id']}")
    print(f"status={done['status']}")
    print(f"outcome={done['outcome']}")
    print(f"verify_receipt={verify['receipt']}")
    print(f"durable_state={state_path.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
