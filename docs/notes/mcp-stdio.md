# MCP stdio framing and SDK behaviour

What the chaos proxy (SPEC 5.2) relies on about the MCP stdio transport and the MCP Python SDK. Checked by reading the SDK source and by running small experiments in the cloud session (October 2026).

## SDK version

- PyPI has `mcp` 2.2.0 (latest) and 1.30.0 (latest 1.x).
- 2.x is a breaking release: FastMCP was renamed (`mcp.server.mcpserver`), protocol types moved to a separate `mcp_types` package, and the version handshake changed. SPEC 13 asks for a FastMCP fake server, and SPEC 5.3 for `stdio_client` + `ClientSession`, which is the 1.x API.
- **Decision:** pin `mcp>=1.30,<2`. Everything below was tested with 1.30.0.
- Protocol version negotiated by 1.30.0: `2025-11-25` (`mcp.types.LATEST_PROTOCOL_VERSION`). The SDK also accepts `2024-11-05`, `2025-03-26` and `2025-06-18`. The filesystem server `@modelcontextprotocol/server-filesystem@2025.12.18` negotiated `2025-11-25`.

## Framing

From the MCP specification (Transports, stdio), version 2025-11-25:

- The client starts the server as a subprocess. The server reads JSON-RPC messages from stdin and writes them to stdout.
- Messages are individual JSON-RPC requests, notifications or responses, UTF-8 encoded, **delimited by newlines**, and **must not contain embedded newlines**.
- The server may write UTF-8 log text to stderr. The client may capture, forward or ignore it.
- The server must not write anything to stdout that is not a valid MCP message, and the client must not write anything to the server's stdin that is not one.

What the SDK actually does (`mcp/client/stdio/__init__.py`, 1.30.0):

- Writing: `message.model_dump_json(by_alias=True, exclude_none=True) + "\n"`, encoded as UTF-8. One message per line.
- Reading: decodes stdout as UTF-8 text, splits on `"\n"`, keeps the incomplete tail in a buffer, and parses each complete line with `JSONRPCMessage.model_validate_json`. No length prefix, no `Content-Length` headers.

**Consequence for the proxy:** read each direction line by line (`asyncio.StreamReader.readline()`), keep the exact bytes, and only parse a copy to look at `method` and `id`. Unmutated lines are written back byte for byte, including the trailing `\n`, which gives the byte equality guarantee in SPEC 5.2. A line that is not valid JSON is forwarded unchanged and logged.

## Environment of the server subprocess

`stdio_client` does **not** pass the parent's full environment. If `StdioServerParameters.env` is `None`, the child gets only `get_default_environment()`: `HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, `USER`. In the cloud session that strips `NODE_EXTRA_CA_CERTS` and the proxy variables, and `npx` then fails with `SELF_SIGNED_CERT_IN_CHAIN` (observed). The agent loop therefore passes the full parent environment, **minus every variable whose name starts with `PFS_`** (so the server under test never sees our API keys; decided 2026-10-02), plus any per server variables, when it starts the proxy. The proxy passes its environment on to the real server.

## What the client does on a malformed line

Experiment: a FastMCP server behind a relay that replaced the first `tools/call` response with the first half of the JSON line (invalid JSON), client using `ClientSession(read_timeout_seconds=3s)`.

Observed with 1.30.0:

1. The reader logs `Failed to parse JSONRPC message from server` with the validation error, and pushes the exception into the session's read stream.
2. `ClientSession` hands the exception to its incoming message handler (by default it is ignored). **The session stays alive.**
3. The `call_tool` that was waiting for that response never gets one and raises `McpError: Timed out while waiting for response to ClientRequest. Waited 3.0 seconds.`
4. The next `call_tool` on the same session succeeds.

So in 1.30.0 the `malformed` profile behaves like `timeout` from the agent's point of view: the loop's per call timeout (`tool_timeout_s`) fires and the model sees a tool error. It does **not** kill the transport, so `stop_reason = "transport_failure"` is not expected for `malformed`. **Decision (2026-10-02):** `malformed` is an ordinary fault profile; `transport_failure` is only for genuinely dead sessions (SPEC 5.2). Without a read timeout the call would hang forever, so the loop must always pass `read_timeout_seconds`.

**Re-checked in M2 (2026-10-03) with the real proxy:** `tests/integration/test_agent_loop.py::test_server_executes_the_call_in_every_fault_profile` runs every profile through `python -m pruefstand.proxy` against the fake server with the real agent loop. For `malformed` the model sees `Tool error: Timed out while waiting for response ...` after `tool_timeout_s`, the server has executed the call, and the next call on the same session works. The parse error is client-side, so the server behind the proxy does not matter. The same holds for `timeout`. No profile killed the transport.

## When the transport really dies

If the server process exits, the SDK closes the read stream and every pending and later request raises `McpError: Connection closed`. The loop treats that as `transport_failure` (SPEC 5.3: a dead transport ends the episode).

## Extra `tools/list` requests sent by the SDK

`ClientSession.call_tool` validates structured output. If the tool's output schema is not cached yet, it first calls `list_tools()` itself (`_validate_tool_result`, `mcp/client/session.py:417`). Observed in the experiment: a `call_tool` without a prior `list_tools` produced an extra `tools/list` request. The loop always calls `list_tools()` at start, which fills the cache, so normally no hidden `tools/list` appears. The proxy must still cope with `tools/list` arriving at any time (poison mutations apply to every `tools/list` response).

## Request id tracking

JSON-RPC ids from the SDK client are integers counting up from 0 per session. Responses carry the same `id`. Server to client requests (for example `roots/list`, sampling) use the server's own id space, so the proxy keys its `request_id -> method` map on client requests only and looks up only responses travelling server to client.
