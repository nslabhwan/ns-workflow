from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

from . import __version__
from .config import init_config, load_config
from .core import PolicyError, Workspace
from .flow import FileRunStore, FlowError, FlowRunner, load_flow, verified_change_template


def _workspace(raw: str) -> Path:
    return Path(raw).expanduser().resolve()


def _print(payload: object) -> None:
    if isinstance(payload, str):
        print(payload)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_init(args: argparse.Namespace) -> int:
    root = _workspace(args.workspace)
    path = init_config(root, overwrite=args.force)
    print(f"NS Workflow initialized: {root}")
    print(f"Config: {path}")
    print(f"Next: nsw doctor --workspace {json.dumps(str(root))}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    root = _workspace(args.workspace)
    cfg = load_config(root)
    clients = {name: shutil.which(name) for name in ("claude", "codex", "gemini", "opencode")}
    payload = {
        "ns_workflow": __version__,
        "workspace": str(root),
        "workspace_exists": root.is_dir(),
        "config_present": (root / ".nsworkflow" / "config.json").exists(),
        "git": shutil.which("git"),
        "python": sys.executable,
        "mcp_dependency": _module_available("mcp"),
        "detected_ai_clients": {k: v for k, v in clients.items() if v},
        "allowed_commands": cfg.allowed_commands,
        "ready": root.is_dir() and _module_available("mcp"),
    }
    _print(payload)
    return 0 if payload["ready"] else 1


def _module_available(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


def cmd_status(args: argparse.Namespace) -> int:
    _print(Workspace(args.workspace).status())
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    result = Workspace(args.workspace).read_text(args.path)
    _print(result)
    return 0


def cmd_write(args: argparse.Namespace) -> int:
    if args.content is not None:
        content = args.content
    elif args.from_file:
        content = Path(args.from_file).read_text(encoding="utf-8")
    else:
        content = sys.stdin.read()
    result = Workspace(args.workspace).write_text(
        args.path, content, expected_sha256=args.expected_sha256
    )
    _print(result)
    return 0


def _clean_remainder(values: list[str]) -> list[str]:
    return values[1:] if values and values[0] == "--" else values


def cmd_run(args: argparse.Namespace, *, verify: bool = False) -> int:
    argv = _clean_remainder(args.argv)
    if not argv:
        raise PolicyError("provide a command after --, for example: nsw run -- git status --short")
    ws = Workspace(args.workspace)
    result = ws.verify(argv, timeout=args.timeout) if verify else ws.run(argv, timeout=args.timeout)
    _print(result)
    return 0 if result["ok"] else int(result["exit_code"] or 1)


def cmd_connect(args: argparse.Namespace) -> int:
    root = _workspace(args.workspace)
    command = shutil.which("nsw") or "nsw"
    generic = {
        "name": "ns-workflow",
        "transport": "stdio",
        "command": command,
        "args": ["mcp", "--workspace", str(root)],
        "tools": ["ns_status", "ns_read", "ns_write", "ns_run", "ns_verify"],
    }
    if args.client == "claude":
        payload = {
            "mcpServers": {
                "ns-workflow": {
                    "command": command,
                    "args": ["mcp", "--workspace", str(root)],
                }
            }
        }
    else:
        payload = generic
    _print(payload)
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp_server import serve

    serve(args.workspace)
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    if args.workspace:
        root = _workspace(args.workspace)
        root.mkdir(parents=True, exist_ok=True)
        created_temp = False
    else:
        root = Path(tempfile.mkdtemp(prefix="nsworkflow-demo-"))
        created_temp = True
    init_config(root, overwrite=True)
    sample = root / "status.txt"
    sample.write_text("status=broken\n", encoding="utf-8")
    ws = Workspace(root)

    print("NS Workflow zero-config proof")
    print(f"workspace: {root}")
    before = ws.read_text("status.txt")
    print(f"1 READ    sha={before['sha256'][:12]} content={before['content'].strip()}")
    changed = ws.write_text("status.txt", "status=fixed\n", expected_sha256=before["sha256"])
    print(f"2 WRITE   before={str(changed['before_sha256'])[:12]} after={changed['after_sha256'][:12]}")
    verify = ws.verify([
        "python3", "-c",
        "from pathlib import Path; assert Path('status.txt').read_text().strip() == 'status=fixed'",
    ])
    print(f"3 VERIFY  exit={verify['exit_code']} receipt={verify['receipt']}")
    print("4 RESULT  PASS — requested state was observed, not self-reported")
    if created_temp:
        print(f"demo kept at: {root}")
    return 0


def _flow_summary(state: dict) -> dict:
    keys = ("run_id", "workflow_id", "status", "current_step", "waiting", "outcome", "failure", "blocked_reason", "transition_count", "completed_steps")
    return {key: state.get(key) for key in keys if state.get(key) is not None}


def cmd_flow_start(args: argparse.Namespace) -> int:
    inputs = json.loads(args.inputs) if args.inputs else {}
    state = FlowRunner(load_flow(args.spec), args.workspace).start(inputs=inputs, run_id=args.run_id)
    _print(_flow_summary(state))
    return 1 if state.get("status") == "FAILED" else 0


def cmd_flow_status(args: argparse.Namespace) -> int:
    root = _workspace(args.workspace)
    state = FileRunStore(root / ".nsworkflow" / "runs").load(args.run_id)
    _print(_flow_summary(state) if not args.full else state)
    return 0


def cmd_flow_decide(args: argparse.Namespace) -> int:
    value = None
    if args.value is not None:
        try:
            value = json.loads(args.value)
        except json.JSONDecodeError:
            value = args.value
    state = FlowRunner(load_flow(args.spec), args.workspace).decide(
        args.run_id, args.decision, value=value, actor=args.actor
    )
    _print(_flow_summary(state))
    return 1 if state.get("status") == "FAILED" else 0


def cmd_flow_template(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser().resolve()
    if path.exists() and not args.force:
        raise FlowError(f"refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(verified_change_template(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Flow template written: {path}")
    return 0


def cmd_flow_demo(args: argparse.Namespace) -> int:
    if args.workspace:
        root = _workspace(args.workspace)
        root.mkdir(parents=True, exist_ok=True)
        created_temp = False
    else:
        root = Path(tempfile.mkdtemp(prefix="nsworkflow-flow-demo-"))
        created_temp = True
    init_config(root, overwrite=True)
    (root / "status.txt").write_text("status=broken\n", encoding="utf-8")
    runner = FlowRunner(verified_change_template(), root)
    waiting = runner.start()
    print("NS Workflow durable-flow proof")
    print(f"workspace: {root}")
    print(f"1 START    status={waiting['status']} run_id={waiting['run_id']}")
    print(f"2 APPROVAL message={waiting['waiting']['message']}")
    done = runner.decide(waiting["run_id"], "approve", actor="demo-human")
    print(f"3 RESUME   status={done['status']} outcome={done.get('outcome')}")
    verify = done["context"]["verifications"]["verify_change"]
    print(f"4 VERIFY   exit={verify['exit_code']} receipt={verify['receipt']}")
    print(f"5 RESULT   {done['outcome']} — approval, mutation and observed verification survived one durable run")
    if created_temp:
        print(f"demo kept at: {root}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nsw",
        description="Give AI a bounded local workspace and require observable proof of work.",
    )
    p.add_argument("--version", action="version", version=f"ns-workflow {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    q = sub.add_parser("init", help="Initialize a workspace")
    q.add_argument("workspace", nargs="?", default=".")
    q.add_argument("--force", action="store_true")
    q.set_defaults(func=cmd_init)

    q = sub.add_parser("doctor", help="Check local readiness and detected AI clients")
    q.add_argument("--workspace", default=".")
    q.set_defaults(func=cmd_doctor)

    q = sub.add_parser("status", help="Show bounded workspace status")
    q.add_argument("--workspace", default=".")
    q.set_defaults(func=cmd_status)

    q = sub.add_parser("read", help="Read a workspace file")
    q.add_argument("path")
    q.add_argument("--workspace", default=".")
    q.set_defaults(func=cmd_read)

    q = sub.add_parser("write", help="Atomically write a workspace file")
    q.add_argument("path")
    q.add_argument("--workspace", default=".")
    q.add_argument("--content")
    q.add_argument("--from-file")
    q.add_argument("--expected-sha256")
    q.set_defaults(func=cmd_write)

    for name, help_text, verify in (
        ("run", "Run one allowlisted argv command", False),
        ("verify", "Run a verification command and record the result", True),
    ):
        q = sub.add_parser(name, help=help_text)
        q.add_argument("--workspace", default=".")
        q.add_argument("--timeout", type=int)
        q.add_argument("argv", nargs=argparse.REMAINDER)
        q.set_defaults(func=(lambda a, v=verify: cmd_run(a, verify=v)))

    q = sub.add_parser("connect", help="Print the stdio MCP connection config")
    q.add_argument("--workspace", default=".")
    q.add_argument("--client", choices=["generic", "claude", "codex", "gemini"], default="generic")
    q.set_defaults(func=cmd_connect)

    q = sub.add_parser("mcp", help="Start the MCP stdio server")
    q.add_argument("--workspace", default=".")
    q.set_defaults(func=cmd_mcp)

    q = sub.add_parser("demo", help="Run a zero-config proof of read → write → verify → receipt")
    q.add_argument("--workspace")
    q.set_defaults(func=cmd_demo)

    flow = sub.add_parser("flow", help="Run durable approval/resume/verification workflows")
    flow_sub = flow.add_subparsers(dest="flow_command", required=True)

    q = flow_sub.add_parser("start", help="Start a JSON workflow")
    q.add_argument("spec")
    q.add_argument("--workspace", default=".")
    q.add_argument("--run-id")
    q.add_argument("--inputs", help="JSON object passed as context.inputs")
    q.set_defaults(func=cmd_flow_start)

    q = flow_sub.add_parser("status", help="Read durable workflow state")
    q.add_argument("run_id")
    q.add_argument("--workspace", default=".")
    q.add_argument("--full", action="store_true")
    q.set_defaults(func=cmd_flow_status)

    q = flow_sub.add_parser("decide", help="Resolve a waiting human approval and resume")
    q.add_argument("spec")
    q.add_argument("run_id")
    q.add_argument("decision", choices=["approve", "edit", "reject", "respond"])
    q.add_argument("--workspace", default=".")
    q.add_argument("--value")
    q.add_argument("--actor", default="human")
    q.set_defaults(func=cmd_flow_decide)

    q = flow_sub.add_parser("template", help="Write a starter verified-change workflow")
    q.add_argument("path", nargs="?", default="verified-change.json")
    q.add_argument("--force", action="store_true")
    q.set_defaults(func=cmd_flow_template)

    q = flow_sub.add_parser("demo", help="Prove approval → resume → mutation → verification")
    q.add_argument("--workspace")
    q.set_defaults(func=cmd_flow_demo)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except (PolicyError, FlowError, ValueError, KeyError, OSError, RuntimeError) as exc:
        print(f"NS Workflow: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
