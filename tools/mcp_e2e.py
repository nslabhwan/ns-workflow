from __future__ import annotations

import asyncio
import tempfile
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from nsworkflow.config import init_config


async def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="nsw-mcp-e2e-"))
    init_config(root, overwrite=True)
    (root / "hello.txt").write_text("before\n", encoding="utf-8")
    (root / "expected.txt").write_text("after\n", encoding="utf-8")

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "nsworkflow.cli", "mcp", "--workspace", str(root)],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            expected = ["ns_status", "ns_read", "ns_write", "ns_run", "ns_verify"]
            assert names == expected, names
            print("TOOLS", ",".join(names))

            read_result = await session.call_tool("ns_read", {"path": "hello.txt"})
            assert "before" in str(read_result.content)
            print("READ_OK", True)

            write_result = await session.call_tool(
                "ns_write",
                {"path": "hello.txt", "content": "after\n"},
            )
            assert "after_sha256" in str(write_result.content)
            print("WRITE_OK", True)

            verify_result = await session.call_tool(
                "ns_verify",
                {
                    "argv": [
                        "git",
                        "diff",
                        "--no-index",
                        "--exit-code",
                        "--no-ext-diff",
                        "expected.txt",
                        "hello.txt",
                    ]
                },
            )
            assert '"ok": true' in str(verify_result.content).lower()
            print("VERIFY_OK", True)

    assert (root / "hello.txt").read_text(encoding="utf-8") == "after\n"
    print("FINAL", "after")
    print("MCP_E2E", "PASS")


if __name__ == "__main__":
    asyncio.run(main())
