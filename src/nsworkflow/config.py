from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ALLOWED_COMMANDS = [
    "git", "python", "python3", "pytest", "uv", "ruff",
    "node", "npm", "pnpm", "yarn", "go", "cargo",
]
DEFAULT_DENY_NAMES = [
    ".env", ".env.local", ".env.production", ".ssh", "credentials",
    "credentials.json", "secrets", "secrets.json", "id_rsa", "id_ed25519",
]
DEFAULT_DENY_SUFFIXES = [".pem", ".key", ".p12", ".pfx"]


@dataclass(slots=True)
class WorkflowConfig:
    version: int = 1
    allowed_commands: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_COMMANDS))
    deny_names: list[str] = field(default_factory=lambda: list(DEFAULT_DENY_NAMES))
    deny_suffixes: list[str] = field(default_factory=lambda: list(DEFAULT_DENY_SUFFIXES))
    timeout_seconds: int = 120
    max_output_bytes: int = 65536
    max_read_bytes: int = 262144

    @classmethod
    def from_dict(cls, raw: dict) -> "WorkflowConfig":
        cfg = cls()
        for key in (
            "version", "allowed_commands", "deny_names", "deny_suffixes",
            "timeout_seconds", "max_output_bytes", "max_read_bytes",
        ):
            if key in raw:
                setattr(cfg, key, raw[key])
        cfg.timeout_seconds = max(1, min(int(cfg.timeout_seconds), 300))
        cfg.max_output_bytes = max(1024, min(int(cfg.max_output_bytes), 1048576))
        cfg.max_read_bytes = max(1024, min(int(cfg.max_read_bytes), 1048576))
        cfg.allowed_commands = sorted({str(x) for x in cfg.allowed_commands if str(x).strip()})
        cfg.deny_names = sorted({str(x).lower() for x in cfg.deny_names if str(x).strip()})
        cfg.deny_suffixes = sorted({str(x).lower() for x in cfg.deny_suffixes if str(x).strip()})
        return cfg

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "allowed_commands": self.allowed_commands,
            "deny_names": self.deny_names,
            "deny_suffixes": self.deny_suffixes,
            "timeout_seconds": self.timeout_seconds,
            "max_output_bytes": self.max_output_bytes,
            "max_read_bytes": self.max_read_bytes,
        }


def config_dir(root: Path) -> Path:
    return root / ".nsworkflow"


def config_path(root: Path) -> Path:
    return config_dir(root) / "config.json"


def load_config(root: Path) -> WorkflowConfig:
    path = config_path(root)
    if not path.exists():
        return WorkflowConfig()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(".nsworkflow/config.json must contain a JSON object")
    return WorkflowConfig.from_dict(raw)


def init_config(root: Path, *, overwrite: bool = False) -> Path:
    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    cfg_dir = config_dir(root)
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = config_path(root)
    if path.exists() and not overwrite:
        return path
    path.write_text(json.dumps(WorkflowConfig().to_dict(), indent=2) + "\n", encoding="utf-8")
    (cfg_dir / ".gitignore").write_text("receipts/\n", encoding="utf-8")
    return path
