"""Agentic loop: per-request Slack MCP session → Claude → SSE stream."""

import json
import os
from collections.abc import AsyncIterator
from typing import Any

import anthropic
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

MCP_URL = "https://mcp.slack.com/mcp"
MODEL = "claude-opus-4-7"
MAX_ITERATIONS = 10


def _sse(event: str, data: Any) -> str:
    payload = json.dumps(data) if not isinstance(data, str) else data
    return f"event: {event}\ndata: {payload}\n\n"


def _leaf_exceptions(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        leaves = []
        for e in exc.exceptions:
            leaves.extend(_leaf_exceptions(e))
        return leaves
    return [exc]


def _format_error(exc: BaseException) -> str:
    leaves = _leaf_exceptions(exc)
    parts = []
    for e in leaves:
        if isinstance(e, anthropic.APIStatusError):
            parts.append(f"Claude API error: {e.message}")
        else:
            parts.append(f"{type(e).__name__}: {e}")
    return "; ".join(parts)


async def run_agent(
    user_message: str,
    slack_token: str | None = None,
) -> AsyncIterator[str]:
    try:
        token = slack_token or os.environ["SLACK_MCP_TOKEN"]
        mcp_headers = {"Authorization": f"Bearer {token}"}

        yield _sse("status", {"text": "Connecting to Slack MCP..."})

        async with streamablehttp_client(MCP_URL, headers=mcp_headers) as (read, write, _):
            async with ClientSession(read, write) as mcp:
                await mcp.initialize()

                tools_result = await mcp.list_tools()
                claude_tools = [
                    {
                        "name": t.name,
                        "description": t.description or "",
                        "input_schema": t.inputSchema,
                    }
                    for t in tools_result.tools
                ]

                print(f"[agent] {len(claude_tools)} tools: {[t['name'] for t in claude_tools]}", flush=True)
                yield _sse("status", {"text": f"Ready ({len(claude_tools)} tools available)"})

                system_prompt = (
                    "You are a Slack assistant. Help users explore their workspace, "
                    "read messages, find channels, and understand conversations.\n"
                    "Rules:\n"
                    "- Be concise and helpful.\n"
                    "- Never ask the user for channel IDs or workspace details you can discover with tools.\n"
                    "- If a tool call returns an error, report it fully — do not ask for more information to retry.\n"
                    "- Use tools to discover workspace structure before answering questions about it."
                )

                client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
                messages: list[dict] = [{"role": "user", "content": user_message}]

                for _ in range(MAX_ITERATIONS):
                    async with client.messages.stream(
                        model=MODEL,
                        max_tokens=4096,
                        system=system_prompt,
                        tools=claude_tools,
                        messages=messages,
                    ) as stream:
                        async for text in stream.text_stream:
                            yield _sse("text", {"text": text})

                        final = await stream.get_final_message()

                    messages.append({"role": "assistant", "content": final.content})

                    print(f"[agent] stop_reason={final.stop_reason}", flush=True)
                    if final.stop_reason != "tool_use":
                        break

                    tool_results = []
                    for block in final.content:
                        if block.type != "tool_use":
                            continue
                        print(f"\n[TOOL CALL] {block.name}")
                        print(f"  input: {json.dumps(block.input, indent=2)}")
                        yield _sse("tool", {"name": block.name, "input": block.input})
                        try:
                            result = await mcp.call_tool(block.name, block.input)
                            result_text = "\n".join(
                                getattr(c, "text", str(c)) for c in result.content
                            )
                            print(f"  result: {result_text[:500]}")
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result_text,
                            })
                        except Exception as exc:
                            print(f"  error: {exc}")
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "is_error": True,
                                "content": str(exc),
                            })

                    messages.append({"role": "user", "content": tool_results})

        yield _sse("done", {})

    except BaseException as exc:
        import traceback
        traceback.print_exc()
        yield _sse("error", {"text": _format_error(exc)})
