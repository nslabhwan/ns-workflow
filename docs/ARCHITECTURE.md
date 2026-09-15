# Architecture

NS Workflow is a small local execution bridge. The public design deliberately exposes only five stable capabilities.

```text
AI client
  │
  │ MCP stdio
  ▼
NS Workflow
  ├─ ns_status
  ├─ ns_read
  ├─ ns_write
  ├─ ns_run
  └─ ns_verify
       │
       ▼
configured workspace
```

## Authority classes

`ns_status` and `ns_read` observe state.

`ns_write` mutates one workspace file through an atomic replace. An optional expected SHA-256 value provides stale-write protection.

`ns_run` and `ns_verify` launch one explicit argv vector. They never accept a shell command string. The executable must be present in the project allowlist.

## Receipts

Every file read, file write, command execution and verification writes a JSON receipt under `.nsworkflow/receipts/`.

A receipt records the observable execution boundary. It is not a cryptographic attestation and it does not prove that an AI reasoned correctly.

## Environment boundary

Child commands receive a reduced environment. Common credentials and arbitrary parent environment values are not forwarded by default.

This reduces accidental secret exposure. It is not process isolation.

## Project independence

The product was inspired by patterns from a private multi-agent system, but the public package has no import, service, network, filesystem, database, queue, control-plane, or runtime dependency on that private system.

The portable invariants are reimplemented locally:

- small capability surface
- explicit read versus mutation authority
- bounded execution
- atomic file changes
- stale-write protection
- result verification
- evidence receipts

## Why not expose a universal shell

A universal shell is convenient but destroys the product boundary. NS Workflow keeps a command allowlist so a repository can state which toolchains an AI is allowed to invoke. Users can opt into additional commands in project-local configuration.

For commands that need full shell semantics, use an external sandbox and explicitly allow the relevant wrapper. The default product does not silently widen authority.
