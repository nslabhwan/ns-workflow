from __future__ import annotations

import json
from pathlib import Path

from .core import PolicyError, Workspace


def serve(workspace: str | Path) -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception as exc:  # pragma: no cover - import error is user-facing
        raise RuntimeError("MCP support is unavailable. Reinstall ns-workflow with its dependencies.") from exc

    ws = Workspace(workspace)
    mcp = FastMCP("NS Workflow")

    def dump(payload: object) -> str:
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @mcp.tool()
    def ns_status() -> str:
        """Show the bounded workspace, command allowlist and current Git status."""
        return dump(ws.status())

    @mcp.tool()
    def ns_read(path: str) -> str:
        """Read one UTF-8 text file inside the configured workspace."""
        try:
            return dump(ws.read_text(path))
        except PolicyError as exc:
            return dump({"ok": False, "error": str(exc)})

    @mcp.tool()
    def ns_write(path: str, content: str, expected_sha256: str | None = None) -> str:
        """Atomically write one text file inside the workspace, optionally with CAS protection."""
        try:
            return dump(ws.write_text(path, content, expected_sha256=expected_sha256))
        except PolicyError as exc:
            return dump({"ok": False, "error": str(exc)})

    @mcp.tool()
    def ns_run(argv: list[str], timeout: int | None = None) -> str:
        """Run one allowlisted argv command in the workspace. Shell strings are not accepted."""
        try:
            return dump(ws.run(argv, timeout=timeout))
        except PolicyError as exc:
            return dump({"ok": False, "error": str(exc)})

    @mcp.tool()
    def ns_verify(argv: list[str], timeout: int | None = None) -> str:
        """Run an allowlisted verification command and write an observable receipt."""
        try:
            return dump(ws.verify(argv, timeout=timeout))
        except PolicyError as exc:
            return dump({"ok": False, "error": str(exc)})

    mcp.run(transport="stdio")
