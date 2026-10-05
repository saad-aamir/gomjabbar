# Project overview: Gom Jabbar in one page

## The system

Gom Jabbar is a test bench for tool-using AI agents. It takes MCPMark's filesystem and postgres tasks, runs an agent on each one many times through its own agent loop, and puts a chaos proxy between the agent and the real MCP server. The proxy can break tool calls (eight fault profiles), poison tool descriptions, inject instructions into tool output, or add fake "shadow" tools; the runner can also reword the task or push back on a correct answer. Every episode runs in its own sandbox (a copy of the files, or a cloned database), with a fake API key planted where attacks can reach it. MCPMark's `verify.py` judges the final state; Gom Jabbar's own graders judge leaks, the attacker's goal, honesty and read/write behaviour. Results are reported along four dimensions (reliability, robustness, security, behavioural stability), each metric computed per task and given a 95% interval by resampling tasks, and two runs can be compared episode by episode with McNemar's test. Three host-side defenses (pinning, a sanitizer, a description scan) can be switched on per run.

What it found on 20 easy dev tasks (exploratory): gpt-oss-120b passes 87% of single attempts but only 65 to 75% of tasks five times in a row; it obeys poisoned tool descriptions 77% of the time (gpt-oss-20b 33%), usually while still finishing the task correctly; injected tool output works far less often (15 to 16%); and authoritative pushback makes both models undo correct work in about a third of challenged episodes (7 of 20 and 5 of 15). Three host-side defenses took gpt-oss-120b's attack success from 77% to 0% (poison) and 16% to 0% (inject) at a 3-point baseline cost that is not significant, and stopped a held-out poisoned description written after the patterns were frozen (90% to 0%). The cost appears under attack instead: removing the attacker's text can hide or poison the real tool, and task success in poisoned episodes fell from 52 to 35 of 60.

## Five decisions that matter most

1. **Judge the state, not the words.** Correctness is MCPMark's `verify.py` on the final state, never the agent's claim. Strict pass adds the agent's honesty and the attack outcome on top. That is what exposes the central finding: right result, compromised path.
2. **A raw JSON-RPC proxy, faithful when idle.** The proxy relays bytes and changes only what a plan says; with an empty plan the output is byte-identical (tested). Faults alter only the response, so the server still executes the call. Attacks never modify the real server.
3. **One episode, one sandbox, one deterministic id.** Each episode gets its own files or database clone and a canary unique to it, and its id is a hash of its spec. That makes concurrent episodes safe, leaks attributable, and resume exact: finished ids are skipped.
4. **Resample tasks, not episodes, and pair when you can.** Attempts of one task are not independent, so every interval comes from a bootstrap over tasks. Comparisons of two runs pair each episode with its twin and use McNemar on the discordant pairs.
5. **Write down every departure, and keep the confirmatory test honest.** Every change to the spec is a dated `DEVIATIONS.md` entry. The standard suite was never touched; all results are labelled exploratory; hypotheses were written after the dev data and proposed for a held-out run with Holm correction. The defense patterns were frozen before the held-out payloads were written (in a separate chat with Claude, by an author who never saw the pattern file), and both held-out rounds are reported, including the inconclusive first one.

## Twenty interview questions

1. In two sentences, what does Gom Jabbar measure that a normal benchmark does not?
2. Why are the four dimensions reported separately instead of as one score?
3. What is the difference between state pass and strict pass, and which finding depends on strict pass?
4. Why did you write your own agent loop instead of using MCPMark's?
5. How does the proxy know which response belongs to which request, and why does that matter for faults?
6. Why does a fault replace the server's response rather than block the request?
7. How do you guarantee that an empty plan changes nothing on the wire?
8. Explain pass^k and why it is lower than pass@1. How do you estimate it from 5 attempts?
9. Why do your confidence intervals resample tasks rather than episodes?
10. How do you keep two concurrent postgres episodes of the same task from seeing each other's writes?
11. What is the canary, why is it unique per episode, and why is access kept separate from leak?
12. What is vault_control, and what would you conclude without it?
13. Why is the larger model more vulnerable to tool poisoning, and how did you check that the smaller one was not just too weak to follow the attack?
14. How does pushback reuse the live session, and what are the four response types?
15. How does a resumed run produce the same results as an uninterrupted one?
16. How do you stop a paid run from spending more than its cap, across several runs?
17. What would make your leak detector miss a real leak?
18. Describe your three defenses and the attack each one is aimed at. Which one cannot act in your experiment, and why?
19. How did you make sure the defense evaluation is not just measuring how well you tuned the patterns?
20. If you had budget for one more run, what would it be, and which pre-registered hypothesis are you least sure will hold?
