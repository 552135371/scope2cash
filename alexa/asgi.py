"""ASGI entry point: the core Scope2Cash app + the Alexa+ add-on.

    uvicorn alexa.asgi:app --port 8000

Adds, without touching app/:
  POST/GET/DELETE /mcp   MCP Streamable HTTP endpoint (official MCP Python SDK; 2025-11-25 handshake supported)
  GET  /alexa            simulated Alexa+ voice experience (Web Speech API in the browser)
  POST /api/alexa/ask    one conversational turn: utterance -> MCP tool calls -> spoken answer + display cards
  GET  /api/alexa/info   MCP endpoint, tools and protocol versions (for judges and for the simulator page)
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from starlette.routing import Route
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS, KNOWN_PROTOCOL_VERSIONS
from pydantic import BaseModel, Field

from app.main import app, lifespan as core_lifespan
from . import assistant
from .mcp_server import TOOL_NAMES, server

STATIC = Path(__file__).parent / "static"
MIN_PROTOCOL = "2025-11-25"


def transport_security() -> TransportSecuritySettings:
    """DNS-rebinding protection stays on: only our own hostnames may reach /mcp. MCP_ALLOWED_HOSTS='*' turns it off."""
    extra = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    if "*" in extra:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*", "testserver"]
    render = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "")
    if render:
        hosts += [render, f"{render}:*"]
    hosts += extra
    origins = [f"http://{h}" for h in hosts] + [f"https://{h}" for h in hosts]
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins)


class _MCPEndpoint:
    """ASGI endpoint for /mcp. The SDK's session manager can only run once, so each app start-up (a server boot, or
    a test client) builds a fresh Streamable HTTP handler and this endpoint forwards to the current one."""
    handler = None

    async def __call__(self, scope, receive, send):
        if self.handler is None:
            raise RuntimeError("MCP endpoint used before the app started")
        await self.handler(scope, receive, send)


_endpoint = _MCPEndpoint()
app.router.routes.insert(0, Route("/mcp", endpoint=_endpoint, methods=["GET", "POST", "DELETE"]))


def _fresh_handler():
    sub = server.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=True,
                                     transport_security=transport_security(), host="0.0.0.0")
    return next(r.endpoint for r in sub.routes if getattr(r, "path", "") == "/mcp")


@asynccontextmanager
async def lifespan(a):
    async with core_lifespan(a):
        _endpoint.handler = _fresh_handler()
        async with server.session_manager.run():  # the Streamable HTTP session manager needs a running task group
            yield
        _endpoint.handler = None


app.router.lifespan_context = lifespan


class AskRequest(BaseModel):
    text: str = Field(..., max_length=500)
    session_id: str = Field("", max_length=64)


@app.post("/api/alexa/ask")
async def alexa_ask(req: AskRequest):
    return await assistant.ask(req.text, req.session_id)


@app.get("/api/alexa/info")
def alexa_info():
    return {"mcp_endpoint": "/mcp", "transport": "streamable-http", "server": "scope2cash",
            "min_protocol_version": MIN_PROTOCOL,
            "protocol_versions": list(KNOWN_PROTOCOL_VERSIONS[KNOWN_PROTOCOL_VERSIONS.index(MIN_PROTOCOL):]),
            "handshake_versions": list(HANDSHAKE_PROTOCOL_VERSIONS), "tools": list(TOOL_NAMES),
            "sends_without_approval": False}


@app.get("/alexa")
def alexa_page():
    return FileResponse(STATIC / "alexa.html")


@app.get("/alexa/{name}")
def alexa_static(name: str):
    p = (STATIC / name).resolve()
    if p.parent != STATIC.resolve() or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


# Same public-demo rate limit as the core AI endpoints (the core middleware reads AI_PATHS at request time).
from app import main as _core  # noqa: E402

if "/api/alexa/ask" not in _core.AI_PATHS:
    _core.AI_PATHS = _core.AI_PATHS + ("/api/alexa/ask",)
