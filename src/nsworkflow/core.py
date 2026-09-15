from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable

from .config import WorkflowConfig, load_config
from .receipts import write_receipt


class PolicyError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= limit:
        return text, False
    clipped = raw[:limit].decode("utf-8", errors="replace")
    return clipped + "\n...[output truncated by NS Workflow]", True


def _minimal_env() -> dict[str, str]:
    allowed_exact = {
        "PATH", "HOME", "USER", "LOGNAME", "LANG", "TERM", "TMPDIR", "TMP", "TEMP",
        "SYSTEMROOT", "COMSPEC", "PATHEXT", "WINDIR",
    }
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper in allowed_exact or upper.startswith("LC_"):
            env[key] = value
    return env


class Workspace:
    """Project-local capability surface.

    This is a guardrail layer, not an OS sandbox. File tools are root-bounded and
    secret-denying. Command execution is argv-only, allowlisted, timed and output-bounded.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        if not self.root.exists() or not self.root.is_dir():
            raise PolicyError(f"workspace does not exist: {self.root}")
        self.config: WorkflowConfig = load_config(self.root)

    def _denied_name(self, path: Path) -> bool:
        deny_names = set(self.config.deny_names)
        for part in path.parts:
            low = part.lower()
            if low in deny_names:
                return True
            if low == ".env" or low.startswith(".env."):
                return True
        low_name = path.name.lower()
        return any(low_name.endswith(suffix) for suffix in self.config.deny_suffixes)

    def resolve(self, relative: str | Path, *, for_write: bool = False) -> Path:
        rel = Path(relative)
        if rel.is_absolute():
            raise PolicyError("absolute paths are not allowed")
        candidate = self.root / rel
        parent = candidate.parent.resolve()
        try:
            parent.relative_to(self.root)
        except ValueError as exc:
            raise PolicyError("path escapes workspace") from exc
        if candidate.exists() or candidate.is_symlink():
            resolved = candidate.resolve()
            try:
                resolved.relative_to(self.root)
            except ValueError as exc:
                raise PolicyError("resolved path escapes workspace") from exc
            candidate = resolved
        elif for_write:
            candidate = parent / candidate.name
        if self._denied_name(candidate.relative_to(self.root)):
            raise PolicyError("secret-like path is denied")
        return candidate

    def status(self) -> dict[str, Any]:
        git = shutil.which("git")
        git_status = None
        if git and (self.root / ".git").exists():
            cp = subprocess.run(
                [git, "status", "--short"], cwd=self.root, text=True,
                capture_output=True, timeout=10, env=_minimal_env(), check=False,
            )
            git_status = cp.stdout.strip()
        return {
            "workspace": str(self.root),
            "config_present": (self.root / ".nsworkflow" / "config.json").exists(),
            "allowed_commands": self.config.allowed_commands,
            "timeout_seconds": self.config.timeout_seconds,
            "git_status": git_status,
            "note": "guardrails are not an OS sandbox",
        }

    def read_text(self, relative: str | Path) -> dict[str, Any]:
        started = time.time()
        path = self.resolve(relative)
        if not path.is_file():
            raise PolicyError("path is not a file")
        size = path.stat().st_size
        if size > self.config.max_read_bytes:
            raise PolicyError(f"file exceeds read limit: {size} bytes")
        data = path.read_bytes()
        text = data.decode("utf-8", errors="replace")
        payload = {
            "action": "read",
            "path": str(path.relative_to(self.root)),
            "bytes": len(data),
            "sha256": sha256_bytes(data),
            "duration_ms": round((time.time() - started) * 1000, 2),
            "ok": True,
        }
        receipt = write_receipt(self.root, payload)
        return {**payload, "content": text, "receipt": str(receipt.relative_to(self.root))}

    def write_text(
        self,
        relative: str | Path,
        content: str,
        *,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        started = time.time()
        path = self.resolve(relative, for_write=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        before = sha256_file(path) if path.exists() and path.is_file() else None
        if expected_sha256 is not None and before != expected_sha256:
            raise PolicyError(f"compare-and-swap mismatch: expected {expected_sha256}, observed {before}")
        data = content.encode("utf-8")
        if len(data) > self.config.max_read_bytes:
            raise PolicyError(f"write exceeds limit: {len(data)} bytes")
        tmp = path.with_name(f".{path.name}.nsw-{os.getpid()}-{time.time_ns()}.tmp")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
        after = sha256_bytes(data)
        payload = {
            "action": "write",
            "path": str(path.relative_to(self.root)),
            "before_sha256": before,
            "after_sha256": after,
            "bytes": len(data),
            "duration_ms": round((time.time() - started) * 1000, 2),
            "ok": True,
        }
        receipt = write_receipt(self.root, payload)
        return {**payload, "receipt": str(receipt.relative_to(self.root))}

    def _validate_argv(self, argv: Iterable[str]) -> list[str]:
        out = [str(x) for x in argv]
        if not out:
            raise PolicyError("argv must not be empty")
        if any("\x00" in x for x in out):
            raise PolicyError("NUL byte in argv")
        command = Path(out[0]).name
        if command not in self.config.allowed_commands:
            raise PolicyError(
                f"command '{command}' is not allowed; edit .nsworkflow/config.json to opt in"
            )
        resolved = shutil.which(out[0])
        if not resolved:
            raise PolicyError(f"command not found: {out[0]}")
        out[0] = resolved
        return out

    def run(self, argv: Iterable[str], *, timeout: int | None = None, verification: bool = False) -> dict[str, Any]:
        started = time.time()
        final_argv = self._validate_argv(argv)
        limit = self.config.max_output_bytes
        requested_timeout = self.config.timeout_seconds if timeout is None else int(timeout)
        bounded_timeout = max(1, min(requested_timeout, self.config.timeout_seconds, 300))
        timed_out = False
        try:
            cp = subprocess.run(
                final_argv,
                cwd=self.root,
                text=True,
                capture_output=True,
                timeout=bounded_timeout,
                env=_minimal_env(),
                check=False,
            )
            code = cp.returncode
            stdout, out_truncated = _truncate(cp.stdout or "", limit)
            stderr, err_truncated = _truncate(cp.stderr or "", limit)
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            code = 124
            raw_out = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            raw_err = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            stdout, out_truncated = _truncate(raw_out, limit)
            stderr, err_truncated = _truncate(raw_err, limit)
        payload = {
            "action": "verify" if verification else "run",
            "argv": [Path(final_argv[0]).name, *final_argv[1:]],
            "exit_code": code,
            "timed_out": timed_out,
            "stdout": stdout,
            "stderr": stderr,
            "output_truncated": out_truncated or err_truncated,
            "duration_ms": round((time.time() - started) * 1000, 2),
            "ok": code == 0,
        }
        receipt = write_receipt(self.root, payload)
        return {**payload, "receipt": str(receipt.relative_to(self.root))}

    def verify(self, argv: Iterable[str], *, timeout: int | None = None) -> dict[str, Any]:
        return self.run(argv, timeout=timeout, verification=True)
