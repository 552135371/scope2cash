"""Talk to a running Scope2Cash MCP server over Streamable HTTP, the way Alexa+ (or any MCP host) would.

    python -m alexa.mcp_check http://localhost:8000/mcp
    python -m alexa.mcp_check https://<your-service>.onrender.com/mcp

Read-only by default: lists the tools and calls who_owes_me and cashflow_this_week. Add --propose "Harbor Yoga"
to also queue (never send) a reminder that you can then approve or reject in the dashboard.
"""
from __future__ import annotations

import argparse
import asyncio

from mcp import Client


async def main(url: str, propose: str) -> None:
    async with Client(url, mode="legacy") as c:  # initialize handshake (protocol 2025-11-25)
        info = c.session.server_info if hasattr(c, "session") else None
        print("connected:", getattr(info, "name", "?"), "| protocol:", getattr(c.session, "protocol_version", "?"))
        tools = await c.list_tools()
        print("tools:", ", ".join(t.name for t in tools.tools))
        for name, args in [("who_owes_me", {}), ("cashflow_this_week", {})] + ([("propose_reminder", {"client": propose})] if propose else []):
            r = await c.call_tool(name, args)
            print(f"\n> {name} {args}\n  {(r.structured_content or {}).get('speech', r.content)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?", default="http://localhost:8000/mcp")
    ap.add_argument("--propose", default="", help="client name to queue a reminder for (needs your approval to send)")
    a = ap.parse_args()
    asyncio.run(main(a.url, a.propose))
