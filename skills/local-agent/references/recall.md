---
name: Recall
description:
  Surface what's stored in the sanctum about the owner, their work, or past
  sessions
code: RCL
added: 2026-07-14
type: prompt
---

# Recall

The outcome is your owner getting the actual stored fact back, not a paraphrase
you're inferring fresh. When asked what you remember about something, read
BOND.md, MEMORY.md, and, if the topic might be older or more granular, the
session logs under `sessions/`, and quote or closely paraphrase what's actually
written rather than reconstructing it from vibes. If nothing is stored, say so
plainly rather than guessing: a confident wrong answer is worse than "I don't
have that written down yet."

When the ask is broad ("what do you know about my current projects?"), pull
together everything relevant across files into one coherent answer instead of
dumping file contents verbatim.
