# Empty final replies from gpt-oss (M1 baseline)

What we found about the replies that ended 21 of the 100 M1 baseline episodes with no text and no tool call. Investigated on 2026-10-03 from the committed traces of `runs/dev-20261003-033437` plus 500 new OpenRouter calls (cost 0.033 USD, about 0.03 EUR). The probe scripts were throwaway and are not in the repo. No harness behaviour was changed.

## What the baseline shows

- 25 episodes ended `final_answer` with `final_claim: none` and failed. 4 of them are gpt-oss-20b replies with reasoning text leaked into `content` ("Now create empty file v10."). The other **21** have `content: ""`, no `tool_calls`, `finish_reason: stop`, provider CoreWeave: 11 on 120b, 10 on 20b, in 9 of the 10 tasks.
- Per model call that is 1.5% (120b, 11 of 725 calls) and 1.9% (20b, 10 of 540). For 120b it hit 3 of the 50 **first** calls, all on two tasks: `pattern_matching` (2 of 5) and `papers_counting` (1 of 5).
- In 16 of the 21 the reply carries reasoning, and the reasoning almost always ends with the next tool call announced: "Let's list directory.", "First list directory to see files.", "Let's list allowed directories." The model meant to call a tool.
- Two kinds, told apart by output tokens that appear nowhere in the reply (`tokens_out` minus visible reasoning at about 4 characters per token; a normal tool-call reply has a median of 33 such tokens):
  - about half have 30 to 50 hidden tokens: a tool call was generated and then lost by the provider's parser;
  - about half have none: generation ended right after the reasoning (analysis channel), with no final message and no call.

## Raw responses

The trace stores the parsed reply, not the raw HTTP body. Original reply of `b3276d6421ad30a7` (120b, `file_splitting`, step 2):

```json
{"message": {"role": "assistant", "content": ""}, "finish_reason": "stop", "provider": "CoreWeave",
 "reasoning": "We have workspace at /tmp/pruefstand/... Need to locate large_file.txt. List directory.",
 "tokens_out": 61}
```

About 20 tokens of visible reasoning out of 61: the rest is a `list_directory` call that never reached us.

Fresh raw reply, CoreWeave, conversation of `58b1a6e1053eef17` (120b, `pattern_matching`, step 1), shortened:

```json
{"provider": "CoreWeave",
 "choices": [{"finish_reason": "stop", "native_finish_reason": "stop",
   "message": {"role": "assistant", "content": null,
     "reasoning": "<457 chars> ... We'll read all files.\n\nFirst list directory to see files."}}],
 "usage": {"completion_tokens": 116, "completion_tokens_details": {"reasoning_tokens": 112}}}
```

Fresh raw reply, DeepInfra, conversation of `b3276d6421ad30a7`. The call's arguments are glued to the end of the reasoning:

```json
{"provider": "DeepInfra",
 "choices": [{"finish_reason": "stop", "native_finish_reason": "stop",
   "message": {"role": "assistant", "content": null,
     "reasoning": "We have allowed directory /tmp/pruefstand/b3276d6421ad30a7/workspace. Need to locate large_file.txt. Let's list workspace.{\n  \"path\": \"/tmp/pruefstand/b3276d6421ad30a7/workspace\"\n}"}}],
 "usage": {"completion_tokens": 75, "completion_tokens_details": {"reasoning_tokens": 46}}}
```

## Replays

Each conversation was rebuilt from the trace up to the empty reply (same system prompt, task, earlier assistant messages and tool results, the 14 tools of `server-filesystem@2025.12.18`, `max_tokens: 32768`) and sent with raw HTTP, pinned, no fallbacks. Counts are empty replies out of samples.

| Conversation (120b) | CoreWeave, default temp | DeepInfra (bf16), default temp | CoreWeave, temp 1.0 | DeepInfra, temp 1.0 |
| --- | --- | --- | --- | --- |
| `b3276d6421ad30a7` file_splitting, step 2 | 0 / 10 | 10 / 10 | | |
| `3a3297165c989049` structure_analysis, step 3 | 0 / 10 | 9 / 10 | | |
| `67841bc5400623dd` papers_counting, step 1 | 0 / 10, then 3 / 100 | 0 / 10 | | |
| `58b1a6e1053eef17` pattern_matching, step 1 | 19 / 100 | | 27 / 100 | 23 / 100 |

- The empty reply reproduces on both providers. At temperature 1.0 the rate on the same prompt is about equal (27% and 23%), so it follows the model's Harmony output, not one provider's stack.
- DeepInfra's default sampling is almost deterministic (repeated samples have identical token counts), so on a given conversation it either always or never fails. CoreWeave's default samples vary. `temperature: null` therefore means different things on different providers; MCPMark sends `temperature: 1.0`.
- On DeepInfra the lost call is visible as JSON at the end of the reasoning. On the step-1 prompts both providers' empty replies are the other kind: the reasoning ends and nothing follows.
- Passing the earlier turns' reasoning back in the assistant messages (OpenAI's advice for gpt-oss) did not help: `b3276` 10 / 10 on DeepInfra and 4 / 10 on CoreWeave (from 0 / 10), `3a32` 4 / 10 on DeepInfra and 0 / 10 on CoreWeave.
- Side observation: one CoreWeave 120b reply had the arguments inside the tool name (`list_directory{"path": ...}`), the 120b form of the 20b tool-name leak.

## What MCPMark's agent does

`src/agents/mcpmark_agent.py`, `_execute_litellm_tool_loop` (the path for gpt-oss): a reply without `tool_calls` is appended, `turn_count` goes up, the loop logs "Task ended with the finish reason from messages being 'stop'" and `break`s with `ended_normally = True` (around line 1037). No second prompt, no resample. The Claude-native loop does the same (`if not tool_uses: break`). Only the non-default `ReActAgent` re-prompts, and only when its JSON format is invalid. So MCPMark scores these episodes the way Prüfstand does now: the task ends and `verify.py` decides.

MCPMark also sends `temperature: 1.0` on every call and appends the full `message.model_dump()` to the history.
