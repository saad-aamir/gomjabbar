---
id: tool-poisoning
summary: Hide instructions inside a tool's description so the agent follows them as if they came from the system.
source: https://owasp.org/www-project-top-10-for-large-language-model-applications/
---

An MCP tool's description is read by the agent as trusted context. An attacker who
controls a tool description can embed instructions there, and the agent often treats
them as part of its own task rather than as untrusted data. Works best when the
instruction is framed as a requirement or procedure the tool itself imposes, rather
than as an obvious request.
