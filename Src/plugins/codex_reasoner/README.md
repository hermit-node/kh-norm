# codex_reasoner

First-party read-only reasoning bridge from Norm to OpenAI Codex CLI.

Codex receives only the question/evidence Norm explicitly sends, runs in an ephemeral empty
working directory with the Codex `read-only` sandbox, and returns structured advice. It is not
an execution worker: Norm/N2 remains responsible for tool calls, mutations, verification, and
user-facing completion claims.

Settings are declared through `plugins.ini` under `[codex_reasoner]`.
