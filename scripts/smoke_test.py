"""Phase 1 smoke test — verify connectivity to Slack MCP at https://mcp.slack.com/mcp."""

import asyncio
import os
import sys

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

load_dotenv()

MCP_URL = "https://mcp.slack.com/mcp"


async def smoke_test() -> None:
    token = os.environ.get("SLACK_MCP_TOKEN")
    if not token:
        print("ERROR: SLACK_MCP_TOKEN is not set in .env", file=sys.stderr)
        sys.exit(1)

    headers = {"Authorization": f"Bearer {token}"}

    print(f"Connecting to {MCP_URL} ...")
    async with streamablehttp_client(MCP_URL, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as mcp:
            await mcp.initialize()
            print("MCP session initialized.")

            tools_result = await mcp.list_tools()
            tool_names = [t.name for t in tools_result.tools]
            print(f"Tools ({len(tool_names)}): {tool_names}")

            if not tool_names:
                print("WARNING: No tools returned — check token scopes.", file=sys.stderr)
                sys.exit(1)

    print("\nSmoke test passed — Phase 1 complete.")


if __name__ == "__main__":
    asyncio.run(smoke_test())
