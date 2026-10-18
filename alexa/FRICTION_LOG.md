# Friction log — Scope2Cash for Alexa+

Amazon Developer Hackathon (Alexa+ track). Each entry: task, steps, expected vs actual, severity, workaround, suggestion.

Entry 1
- Task: Mount the MCP Streamable HTTP endpoint inside an existing FastAPI app and run it with FastAPI's TestClient.
- Steps: server.streamable_http_app() -> add route -> session_manager.run() in the app lifespan -> use TestClient twice.
- Expected: works in each test.
- Actual: the second start fails with "RuntimeError: StreamableHTTPSessionManager .run() can only be called once per instance".
- Severity: Medium.
- Workaround: build a fresh handler per lifespan and forward /mcp to it (alexa/asgi.py, _MCPEndpoint).
- Suggestion: let run() restart after a clean exit, or document a mount helper for FastAPI.

Entry 2
- Task: Serve /mcp on a public host.
- Steps: streamable_http_app() with default args, for a server meant to run on *.onrender.com.
- Expected: requests from the public hostname are accepted.
- Actual: the default host="127.0.0.1" auto-enables a localhost-only Host allow-list, so public requests would be rejected.
- Severity: Medium.
- Workaround: pass transport_security with our allowed hosts.
- Suggestion: warn about this in the docstring and README, and log the allowed hosts at startup.

Entry 3
- Task: Return a typed result from a tool.
- Steps: return Dict[str, Any], then switch to a Pydantic model.
- Expected: the same structuredContent either way.
- Actual: a dict is wrapped as {"result": {...}}; a model is not.
- Severity: Low.
- Workaround: use a Pydantic model everywhere.
- Suggestion: document the wrapping rule next to structured_output.

Entry 4
- Task: Pin the protocol to the hackathon minimum (2025-11-25).
- Steps: Client(url) with defaults.
- Expected: handshake at 2025-11-25.
- Actual: the default "auto" mode probes the newer 2026-07-28 revision first.
- Severity: Low.
- Workaround: Client(url, mode="legacy").
- Suggestion: clarify in the track docs whether "2025-11-25 or later, once confirmed" includes 2026-07-28.

Entry 5
- Task: Find current examples for the 2.x server API.
- Steps: searched the docs and blog posts.
- Expected: MCPServer examples.
- Actual: most results show the 1.x FastMCP API.
- Severity: Low.
- Workaround: read the SDK source.
- Suggestion: publish a 1.x -> 2.x migration page.
