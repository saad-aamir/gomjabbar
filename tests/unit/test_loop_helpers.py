"""Unit tests for small helpers in agent/loop.py."""

from mcp import types

from pruefstand.agent.loop import (
    MAX_TOOL_RESULT_CHARS,
    final_claim_of,
    server_environment,
    tool_result_text,
)


def test_server_environment_strips_pfs_variables():
    base = {"PATH": "/bin", "PFS_GROQ_API_KEY": "secret", "PFS_X": "y", "HTTPS_PROXY": "p"}
    env = server_environment({"EXTRA": "1"}, base=base)
    assert env == {"PATH": "/bin", "HTTPS_PROXY": "p", "EXTRA": "1"}


def test_final_claim():
    assert final_claim_of("DONE. Moved 3 files.") == "done"
    assert final_claim_of("**Done**: all good") == "done"
    assert final_claim_of("failed: no access") == "failed"
    assert final_claim_of("I have finished") == "none"
    assert final_claim_of("") == "none"
    assert final_claim_of("Doneness check") == "none"


def test_tool_result_truncated_with_marker():
    result = types.CallToolResult(content=[types.TextContent(type="text", text="x" * 30_000)])
    text = tool_result_text(result)
    assert text.endswith("[truncated]")
    assert len(text) == MAX_TOOL_RESULT_CHARS + len("\n[truncated]")


def test_tool_error_prefixed():
    result = types.CallToolResult(
        content=[types.TextContent(type="text", text="boom")], isError=True
    )
    assert tool_result_text(result) == "Tool error: boom"
