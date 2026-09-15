from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .core import Workspace


class FlowError(RuntimeError):
    pass


_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
Handler = Callable[[dict, dict, dict], Any]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_run_id(run_id: str) -> str:
    value = str(run_id)
    if not _RUN_ID_RE.fullmatch(value):
        raise ValueError("run_id must be 1-128 safe filename characters: A-Z a-z 0-9 . _ -")
    return value


def load_flow(path: str | Path) -> dict:
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise FlowError("flow JSON must be an object")
    return spec


def verified_change_template() -> dict:
    return {
        "id": "verified-change",
        "version": "1",
        "max_transitions": 20,
        "steps": [
            {"id": "read_current", "kind": "call", "uses": "workspace.read", "with": {"path": "status.txt"}, "save_as": "before"},
            {"id": "approve_change", "kind": "approval", "message": "Apply the proposed change to status.txt?", "accept_decisions": ["approve"]},
            {
                "id": "apply_change",
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
                "id": "verify_change",
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


class FileRunStore:
    """Project-local checkpoint and append-only event evidence."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def state_path(self, run_id: str) -> Path:
        return self.root / f"{validate_run_id(run_id)}.json"

    def events_path(self, run_id: str) -> Path:
        return self.root / f"{validate_run_id(run_id)}.events.jsonl"

    def exists(self, run_id: str) -> bool:
        return self.state_path(run_id).exists()

    def load(self, run_id: str) -> dict:
        path = self.state_path(run_id)
        if not path.exists():
            raise KeyError(f"unknown run_id: {run_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, state: dict) -> None:
        path = self.state_path(state["run_id"])
        payload = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def event(self, state: dict, event_type: str, **data: Any) -> None:
        event = {
            "ts": now_iso(),
            "run_id": validate_run_id(state["run_id"]),
            "workflow_id": state["workflow_id"],
            "type": event_type,
            **data,
        }
        with self.events_path(state["run_id"]).open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


class FlowRunner:
    """Small durable workflow engine with explicit approval and verification semantics."""

    def __init__(self, spec: dict, workspace: str | Path):
        self.spec = spec
        self.workspace = Workspace(workspace)
        self.store = FileRunStore(self.workspace.root / ".nsworkflow" / "runs")
        self.order = [step["id"] for step in spec.get("steps", [])]
        self.steps = {step["id"]: step for step in spec.get("steps", [])}
        self.max_transitions = int(spec.get("max_transitions", 100))
        self.handlers: dict[str, Handler] = {
            "workspace.read": self._handle_read,
            "workspace.write": self._handle_write,
            "workspace.run": self._handle_run,
            "workspace.verify": self._handle_verify,
        }
        self._validate()

    def _validate(self) -> None:
        if not self.spec.get("id") or not self.spec.get("version"):
            raise FlowError("flow id/version required")
        if not self.order or len(self.order) != len(self.steps):
            raise FlowError("unique steps required")
        if self.max_transitions < 1 or self.max_transitions > 1000:
            raise FlowError("max_transitions must be between 1 and 1000")
        allowed = {"call", "condition", "approval", "verify", "end"}
        for step in self.spec["steps"]:
            sid = step.get("id")
            kind = step.get("kind")
            if kind not in allowed:
                raise FlowError(f"unsupported kind: {kind}")
            if kind in {"call", "verify"}:
                uses = step.get("uses")
                if uses not in self.handlers:
                    raise FlowError(f"{sid}: unsupported handler: {uses}")
            if kind == "condition" and not all(k in step for k in ("path", "then", "else")):
                raise FlowError(f"{sid}: condition fields missing")
            if kind == "approval":
                accepted = step.get("accept_decisions", ["approve"])
                if not isinstance(accepted, list) or not accepted:
                    raise FlowError(f"{sid}: invalid accept_decisions")
                if any(d not in {"approve", "edit", "respond"} for d in accepted):
                    raise FlowError(f"{sid}: invalid accept_decisions")
            if kind == "call" and step.get("mode") == "action":
                effect = step.get("effect", "none")
                if effect in {"external_reversible", "external_commit", "high_impact"} and not step.get("idempotency_key"):
                    raise FlowError(f"{sid}: idempotency_key required")
            for key in ("next", "then", "else"):
                target = step.get(key)
                if target is not None and target not in self.steps:
                    raise FlowError(f"{sid}: unknown target {target}")

    @staticmethod
    def _get(obj: Any, path: str) -> Any:
        cur = obj
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return None
            cur = cur[part]
        return cur

    def _resolve(self, value: Any, state: dict) -> Any:
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            key = value[2:-1]
            if key == "run_id":
                return state["run_id"]
            if key.startswith("context."):
                return self._get(state["context"], key[8:])
            return None
        if isinstance(value, list):
            return [self._resolve(v, state) for v in value]
        if isinstance(value, dict):
            return {k: self._resolve(v, state) for k, v in value.items()}
        return value

    def _next(self, step_id: str) -> str | None:
        idx = self.order.index(step_id)
        return self.order[idx + 1] if idx + 1 < len(self.order) else None

    def _handle_read(self, context: dict, params: dict, meta: dict) -> dict:
        return self.workspace.read_text(params["path"])

    def _handle_write(self, context: dict, params: dict, meta: dict) -> dict:
        return self.workspace.write_text(
            params["path"],
            params["content"],
            expected_sha256=params.get("expected_sha256"),
        )

    def _handle_run(self, context: dict, params: dict, meta: dict) -> dict:
        return self.workspace.run(params["argv"], timeout=params.get("timeout"))

    def _handle_verify(self, context: dict, params: dict, meta: dict) -> dict:
        return self.workspace.verify(params["argv"], timeout=params.get("timeout"))

    def start(self, inputs: dict | None = None, run_id: str | None = None) -> dict:
        rid = validate_run_id(run_id or uuid.uuid4().hex)
        if self.store.exists(rid):
            raise FlowError(f"run_id already exists: {rid}")
        state = {
            "run_id": rid,
            "workflow_id": self.spec["id"],
            "workflow_version": self.spec["version"],
            "status": "PENDING",
            "current_step": self.spec.get("start") or self.order[0],
            "context": {"inputs": inputs or {}},
            "completed_steps": [],
            "approval_decisions": {},
            "waiting": None,
            "transition_count": 0,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        self.store.save(state)
        self.store.event(state, "run.started", start=state["current_step"])
        return self.run(rid)

    def status(self, run_id: str) -> dict:
        return self.store.load(run_id)

    def decide(self, run_id: str, decision: str, value: Any = None, actor: str = "human") -> dict:
        state = self.store.load(run_id)
        if state.get("status") != "WAITING_APPROVAL" or not state.get("waiting"):
            raise FlowError("run is not waiting for approval")
        if decision not in {"approve", "edit", "reject", "respond"}:
            raise FlowError("invalid decision")
        step_id = state["waiting"]["step_id"]
        state["approval_decisions"][step_id] = {
            "decision": decision,
            "value": value,
            "actor": actor,
            "ts": now_iso(),
        }
        state["waiting"] = None
        state["status"] = "RUNNING"
        state["updated_at"] = now_iso()
        self.store.save(state)
        self.store.event(state, "approval.resolved", step_id=step_id, decision=decision, actor=actor)
        return self.run(run_id)

    def _fail(self, state: dict, reason: str) -> dict:
        state["status"] = "FAILED"
        state["failure"] = reason
        state["updated_at"] = now_iso()
        self.store.event(state, "run.failed", reason=reason)
        self.store.save(state)
        return state

    def run(self, run_id: str) -> dict:
        state = self.store.load(run_id)
        if state["status"] in {"COMPLETED", "FAILED", "BLOCKED", "CANCELLED", "WAITING_APPROVAL"}:
            return state
        state["status"] = "RUNNING"
        while state.get("current_step"):
            if state["transition_count"] >= self.max_transitions:
                return self._fail(state, "MAX_TRANSITIONS_EXCEEDED")
            step = self.steps[state["current_step"]]
            sid = step["id"]
            state["transition_count"] += 1
            self.store.event(state, "step.started", step_id=sid, kind=step["kind"])

            if step["kind"] == "approval":
                decision = state["approval_decisions"].get(sid)
                if decision is None:
                    state["status"] = "WAITING_APPROVAL"
                    state["waiting"] = {"step_id": sid, "message": step.get("message", "Approval required")}
                    state["updated_at"] = now_iso()
                    self.store.save(state)
                    self.store.event(state, "approval.requested", step_id=sid, message=state["waiting"]["message"])
                    return state
                if decision["decision"] == "reject":
                    state["status"] = "BLOCKED"
                    state["blocked_reason"] = "APPROVAL_REJECTED"
                    self.store.event(state, "run.blocked", step_id=sid, reason=state["blocked_reason"])
                    self.store.save(state)
                    return state
                accepted = step.get("accept_decisions", ["approve"])
                if decision["decision"] not in accepted:
                    state["status"] = "BLOCKED"
                    state["blocked_reason"] = f"APPROVAL_DECISION_NOT_ACCEPTED:{decision['decision']}"
                    self.store.event(state, "run.blocked", step_id=sid, reason=state["blocked_reason"])
                    self.store.save(state)
                    return state
                state["context"].setdefault("approvals", {})[sid] = decision
                nxt = step.get("next") or self._next(sid)

            elif step["kind"] == "condition":
                actual = self._get(state["context"], step["path"])
                matched = actual == step.get("equals")
                state["context"].setdefault("conditions", {})[sid] = {"actual": actual, "matched": matched}
                nxt = step["then"] if matched else step["else"]

            elif step["kind"] in {"call", "verify"}:
                name = step["uses"]
                params = self._resolve(step.get("with", {}), state)
                meta = {
                    "run_id": run_id,
                    "step_id": sid,
                    "mode": step.get("mode"),
                    "effect": step.get("effect", "none"),
                    "idempotency_key": self._resolve(step.get("idempotency_key"), state),
                }
                try:
                    result = self.handlers[name](state["context"], params, meta)
                except Exception as exc:
                    state["context"].setdefault("errors", {})[sid] = {"type": type(exc).__name__}
                    self.store.event(state, "handler.failed", step_id=sid, error_type=type(exc).__name__)
                    return self._fail(state, f"HANDLER_ERROR:{sid}:{type(exc).__name__}")
                if step["kind"] == "verify":
                    ok = result.get("ok") if isinstance(result, dict) else bool(result)
                    state["context"].setdefault("verifications", {})[sid] = result
                    self.store.event(state, "verification.completed", step_id=sid, ok=bool(ok))
                    if not ok:
                        return self._fail(state, f"VERIFICATION_FAILED:{sid}")
                elif step.get("save_as"):
                    state["context"][step["save_as"]] = result
                nxt = step.get("next") or self._next(sid)

            elif step["kind"] == "end":
                outcome = step.get("outcome", "COMPLETED")
                if outcome == "VERIFIED_COMPLETE":
                    verifications = state["context"].get("verifications", {})
                    if not verifications or not all(bool(v.get("ok")) for v in verifications.values() if isinstance(v, dict)):
                        return self._fail(state, "VERIFIED_COMPLETE_WITHOUT_VERIFICATION")
                state["status"] = "COMPLETED"
                state["outcome"] = outcome
                state["completed_steps"].append(sid)
                state["current_step"] = None
                state["updated_at"] = now_iso()
                self.store.event(state, "step.completed", step_id=sid)
                self.store.event(state, "run.completed", outcome=outcome)
                self.store.save(state)
                return state

            if sid not in state["completed_steps"]:
                state["completed_steps"].append(sid)
            state["current_step"] = nxt
            state["updated_at"] = now_iso()
            self.store.event(state, "step.completed", step_id=sid, next=nxt)
            self.store.save(state)

        return self._fail(state, "NO_END_STEP_REACHED")
