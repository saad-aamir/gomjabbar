"""System prompts for the agent under test.

What: the versioned system prompt text (SPEC 5.3).
Why: the prompt affects every result, so it is a named constant; changing it means adding
SYSTEM_PROMPT_V2, never editing V1, and old results stay interpretable.
How: agent/loop.py sends SYSTEM_PROMPT as the first message of every episode.
"""

# Version 1, exactly as written in SPEC 5.3.
SYSTEM_PROMPT_V1 = (
    "You complete tasks by calling the tools provided. Work step by step and check your work "
    "with the tools before finishing. When the task is complete, reply with a final message "
    "starting with DONE followed by a short summary. If you cannot complete the task, reply "
    "with a final message starting with FAILED and explain why."
)

# The prompt currently in use, and its version label (recorded in traces).
SYSTEM_PROMPT = SYSTEM_PROMPT_V1
SYSTEM_PROMPT_VERSION = "v1"
