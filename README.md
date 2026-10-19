# Norm

**Norm is a local-first AI agent runtime for Windows.** It connects locally hosted language models to tools, persistent context, and operator-controlled task execution.

Norm is designed to carry out work—not just answer prompts. It plans multi-step tasks, checks proposed actions, operates through approved tools, and resumes work after interruptions.

## How Norm works

```text
Operator request
       ↓
N2 — plans, reasons, and proposes tool actions
       ↓
N1 — validates each proposal, executes approved actions
     or returns a concrete denial for N2 to revise
       ↓
Results and checkpoints → N2 continues → N1 reviews completion
```

**N2 does not execute tools directly.** N1 controls approval, tool execution, and acceptance of completed steps. Operator slash commands use their own direct-command path; optional plugins preserve the same N1/N2 contract.

## Capabilities

- **Local AI:** Ollama-hosted language models with optional CUDA-capable vision utilities.
- **Durable tasks:** Resumable and suppressible work, Redis-backed live evidence, and PostgreSQL-backed history and checkpoints.
- **Controlled tools:** Native filesystem, database, browser, and system operations with explicit approval and access boundaries.
- **Extensible plugins:** Hot-swappable schema-2 Python plugins, tool discovery, and integrity-checked source identities.
- **Vision and media:** PDF/image analysis, OCR, face observations and embeddings, video scene boundaries, and structured identity-match proposals.
- **Operator interfaces:** Local prompt, reply, and runtime views with structured reporting.

Media analysis produces reusable structured results. External apps such as Media Catalog manage editable names, aliases, database records, and accepted person-profile merges.

## Repository layout

| Location | Purpose |
| --- | --- |
| [Src/core](Src/core) | Runtime, model clients, task persistence, and tool execution |
| [Src/plugins](Src/plugins) | Built-in and optional plugin implementations |
| [Src/tools](Src/tools) | Consoles, utilities, and regression tests |
| [Src/config](Src/config) | Runtime defaults and configuration examples |
| [Src/docs](Src/docs) | Technical and operator documentation |

## Download and setup

Get the [latest published source](https://github.com/hermit-node/kh-norm/releases/latest). The release ZIP contains **the contents of `Src` at the ZIP root**, not an additional enclosing folder.

Norm is intended for Windows with a compatible Python environment, locally configured Ollama, Redis, and PostgreSQL, and appropriate runtime dependencies. Some GPU and plugin capabilities require optional models and libraries. See [runtime requirements](Src/core/requirements.txt) and the [operator documentation](Src/docs/README.md).

**Public-source boundary:** This repository excludes machine-specific secrets and settings, runtime state, and unpublished private modules. The public ZIP is a source snapshot, not a preconfigured executable installer. Never commit credentials, environment files, or user data.

## Documentation

- [Plugin index](Src/plugins/README.md)
- [Vision plugin](Src/plugins/vision_parse/README.md)
- [Operator and technical documentation](Src/docs/README.md)
- [Source revision records](Src/README.md)
