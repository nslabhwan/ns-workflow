# Security Policy

## Reporting

Do not post credentials, private keys, access tokens, private infrastructure identifiers, or unredacted logs in public issues.

Until the public repository security form is enabled, open a minimal public issue that contains no sensitive data and asks for a private contact path. After GitHub Private Vulnerability Reporting is enabled, use the repository Security tab.

## Boundary

NS Workflow is a guardrail layer for trusted local development workflows. It is not an OS sandbox and does not claim to contain hostile code.

File capabilities are restricted to the configured workspace and deny common secret-like paths. Command execution is argv-only, allowlisted, timed and output-bounded. Child processes receive a reduced environment.

For untrusted repositories, untrusted generated code, or high-risk commands, run NS Workflow inside a disposable VM, container, cloud lab, or another OS-level containment boundary.

## Never include in reports

- real API keys or OAuth tokens
- private keys or certificates
- cookies or session files
- production `.env` files
- customer data
- raw private chat exports
- secrets copied from tool output
