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


def test_final_claim_strips_markdown_and_punctuation():
    # Leading whitespace, markdown (**, __, #, >, backticks) and punctuation are stripped,
    # and the word is compared case-insensitively (DEVIATIONS.md, 2026-10-03).
    assert final_claim_of("  \n DONE all good") == "done"
    assert final_claim_of("__DONE__ all good") == "done"
    assert final_claim_of("### Done") == "done"
    assert final_claim_of("> **done**: summary") == "done"
    assert final_claim_of("`DONE` summary") == "done"
    assert final_claim_of("- Failed: no access") == "failed"
    assert final_claim_of("...FAILED") == "failed"
    # Only the first word counts.
    assert final_claim_of("Not done yet") == "none"
    assert final_claim_of("**Summary**: DONE") == "none"


def test_final_claim_on_the_pilot_messages():
    # Exact final texts from runs/pilot-dev-20261003-031744 (gpt-oss-120b). The three 20b
    # episodes ended with an empty message or a message without DONE, so they stay none.
    assert final_claim_of("DONE: Identified that only `file_09.txt` contains ...") == "done"
    assert final_claim_of("DONE All five specified text files have been converted") == "done"
    assert final_claim_of("") == "none"
    assert final_claim_of("Check if any other file shares substrings of 30+ length") == "none"


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


def test_empty_reply_detection_and_kind():
    from pruefstand.agent.llm import LLMReply, ToolCall
    from pruefstand.agent.loop import empty_reply_kind, is_empty_reply

    def reply(content="", calls=(), finish="stop", tokens_out=10, reasoning="List the dir."):
        return LLMReply(
            content=content,
            tool_calls=list(calls),
            message={},
            tokens_out=tokens_out,
            finish_reason=finish,
            extra={"reasoning": reasoning},
        )

    assert is_empty_reply(reply())
    assert is_empty_reply(reply(content="  \n"))
    assert not is_empty_reply(reply(content="DONE"))
    assert not is_empty_reply(reply(calls=[ToolCall("c1", "list_directory", "{}")]))
    # Cut off by the token limit is a different problem, not re-sampled.
    assert not is_empty_reply(reply(finish="length"))
    # "List the dir." is 13 characters, about 3 tokens: 10 output tokens leave 7 hidden.
    assert empty_reply_kind(reply(tokens_out=10)) == "stopped_after_reasoning"
    # 40 output tokens leave about 37 hidden: a tool call was generated and lost.
    assert empty_reply_kind(reply(tokens_out=40)) == "dropped_call"
