"""Scope2Cash for Alexa+: a voice add-on (self-hosted MCP server + simulated Alexa+ web experience).

Kept in its own package so the core Scope2Cash app (app/) is untouched: run `uvicorn alexa.asgi:app` to serve the
core app plus /mcp (MCP Streamable HTTP) and /alexa (voice simulator)."""
