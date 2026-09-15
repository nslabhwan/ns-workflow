# Contributing

NS Workflow is intentionally small. Contributions should make the five-tool execution surface easier to use, easier to verify, or harder to misuse without turning it into a general remote shell.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
nsw demo
```

## Pull requests

Please keep changes narrow and include tests for behavior changes.

Strong contributions usually improve one of these areas:

- workspace boundary correctness
- atomic write and stale-write protection
- command bounding
- observable verification receipts
- client onboarding
- cross-platform behavior
- documentation that reduces first-run friction

Avoid adding hidden cloud dependencies, unrestricted shell execution, duplicate control layers, or client-specific behavior without a reproducible test path.

## Compatibility claims

Do not claim a client integration is plug-and-play until it has been tested against the current public client version. If a configuration is only an example, label it as an example.
