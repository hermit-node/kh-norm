# Norm

A persistent, self-hosted AI agent that can remember context, use tools, carry work across sessions, and recover interrupted tasks.

Norm is built to do more than answer a prompt. You give it work in ordinary language; it can plan that work, use local tools and services, inspect and modify files, verify results, and preserve enough task state to continue later.

Its memory and task state live outside the model, so changing models or restarting the runtime does not mean throwing away everything the agent has learned or everything it was doing.

Norm is intended to become more useful as it works with you: retaining relevant project facts and decisions, learning which context matters, and gaining new capabilities through hot-loadable Python plugins.

## What Norm does

* **Persistent memory** — useful facts, preferences, decisions, task history, and working context survive individual chats and restarts.
* **Long-running tasks** — work is planned, bounded, checkpointed, verified, and recoverable rather than assumed to finish in a single model turn.
* **Local models** — designed to work with Ollama and self-hosted model infrastructure.
* **Hot-loaded tools** — add Python plugins without rebuilding the Norm executable.
* **Durable task state** — PostgreSQL stores task history and recovery state; Redis handles live queues and short-lived coordination.
* **Recoverable execution** — interrupted or deliberately suppressed work can be resumed instead of recreated from scratch.
* **Large-file workflows** — large sources are streamed and processed incrementally rather than loaded wholesale into model context.
* **Operator controls** — queue inspection, task suppression/resume, backup, memory maintenance, thread management, status, and recovery commands are built in.
* **Local infrastructure awareness** — optional Tailscale inventory and explicitly allowlisted service checks without network-wide scanning.

Norm is intended to be an agent you can keep running and gradually extend, rather than a thin chat wrapper around an LLM.

## Quick start

Norm currently targets **Windows** and uses a reusable installer.

Download or clone the repository and keep the installer and portable source package together:

```text
Norm-Installer.py
Run-Norm-Installer.bat
Norm-0.53.9-portable-source.zip
Norm-0.53.9-portable-source.zip.sha256
```

Then run:

```bat
Run-Norm-Installer.bat
```

The installer configures the environment, installs the required Python packages, synchronizes the Norm runtime, and can build `norm.exe`.

Existing installations are updated in place. Persistent state such as plugins, logs, SSH configuration, runtime state, and compatible Python environments is preserved.

## Architecture

```text
                 ┌──────────────┐
                 │     Norm     │
                 │ Agent Runtime│
                 └──────┬───────┘
                        │
        ┌───────────────┼────────────────┐
        │               │                │
   PostgreSQL         Redis            Ollama
  durable state    live queues       local models
  memory/history   coordination
        │               │
        └───────────┬───┘
                    │
               Python tools
               and plugins
```

Norm separates durable state from live runtime state.

**PostgreSQL** is the long-term source of truth for task history, memories, recovery information, conversation state, and other durable records.

**Redis** handles live ingress, queues, temporary coordination, and runtime buffers.

**Ollama** provides local model inference.

The runtime coordinates these pieces and exposes tools to the model through a controlled execution layer.

## Plugins

Norm's plugin system is deliberately simple: Python files placed in the configured `plugins` directory are discovered and exposed as native tools.

```text
C:\Norm\plugins\
```

Public functions in plugin modules become callable tools. Plugins are rescanned automatically and can be updated without rebuilding `norm.exe`.

If a plugin update fails to load, Norm keeps the last known-good version active.

The public distribution currently includes plugins for:

* backup and recovery
* exact/verbatim file editing
* PDF and document vision parsing
* paired-image steganography
* key-based steganography
* rotor-based text encoding

The plugin directory is also intended for user-created capabilities.

## Memory

Norm does not treat conversation history as its only memory system.

It can retain structured information such as:

* facts
* preferences
* decisions
* constraints
* assumptions
* future tasks
* task history

Mixed user messages can also be separated into the immediate task and reusable **Ingrained Details**, allowing useful side information to survive without turning every sentence into a new task.

Memory is periodically condensed so the model can work from a smaller background context while the underlying PostgreSQL records remain available.

## Task execution

Norm treats substantial work as a task with explicit state rather than a single prompt/response.

A task can contain multiple steps, tool calls, verification, recovery information, and child work.

Large tasks can be checkpointed and resumed:

```text
/suppress-task
/resume-task
/queue-full
/status
/status/busy
```

Interrupted work therefore does not necessarily need to start over.

Norm also tracks the distinction between the original user request and instructions generated internally by the planner, verifier, recovery system, or child tasks.

## Files and large sources

Norm uses bounded reads rather than assuming source files fit into model context.

Large files can be streamed in chunks with continuation cursors, while task processing and temporary storage have explicit limits.

Generated artifacts intended to survive are stored separately from disposable task scratch data.

```text
Documents\Norm\
├─ workspace\    durable generated work
└─ temp\         temporary task/recovery files
```

## PDF reading

The bundled `vision_parse` plugin combines the PDF text layer with rendered-page vision.

This is useful for PDFs where extracted text has broken columns, bad encoding, missing characters, or other layout problems. Visible page content can be used to correct unreliable raw extraction.

## Backups

Norm supports both portable and full-state backups.

```text
/backup
```

Creates portable source/install media.

```text
/backup full
```

Creates a private recovery package containing the state needed to reconstruct a configured Norm installation.

Full backups can contain sensitive information and should not be published.

## Configuration and privacy

The public repository contains generic configuration only.

Machine-specific settings can be supplied through a local:

```text
norm-imprint.local.json
```

The imprint may contain non-secret values such as hosts, ports, paths, usernames, and service locations.

Passwords, API tokens, SSH private keys, OAuth credentials, and similar secrets are kept separately and are excluded from the public repository.

See [SECURITY.md](SECURITY.md) for the publication and secret-handling boundary.

## Network discovery

Norm includes an optional `/network-map` operator command for environments using Tailscale.

Discovery is passive by default.

Active checks are performed only against explicitly configured targets. Discovered peers do not automatically become probe targets, and configured never-probe hosts or networks are excluded before network I/O.

There is no automatic subnet or port sweep.

## Repository layout

```text
src/Norm/       Norm source
tests/          regression tests
tools/          build and maintenance tools

Norm-Installer.py
Run-Norm-Installer.bat
```

The portable source package is generated from `src/Norm`.

## Building from source

Rebuild the portable source package:

```powershell
python .\tools\build_source_package.py
```

Build the Windows executable installer:

```bat
Build-Norm-Installer-EXE.bat
```

Norm itself is packaged into a Windows executable with PyInstaller.

## Documentation

More detailed implementation and operator documentation lives inside the source tree:

* [`src/Norm/docs/README.md`](src/Norm/docs/README.md) — operator/runtime overview
* [`src/Norm/docs/RELEASE_NOTES.md`](src/Norm/docs/RELEASE_NOTES.md) — release history
* [`src/Norm/SOURCE_PACKAGE.md`](src/Norm/SOURCE_PACKAGE.md) — portable package details
* [`AUDIT.md`](AUDIT.md) — public release audit
* [`SECURITY.md`](SECURITY.md) — security and publication boundary
* [`RELEASE.md`](RELEASE.md) — release information
* [`PUBLISH.md`](PUBLISH.md) — maintainer publishing workflow

## Status

Norm is under active development.

The current public source is primarily designed for a self-hosted Windows environment and assumes supporting services such as PostgreSQL, Redis, and an LLM provider such as Ollama.

The architecture is intentionally modular: models, plugins, storage, and external services are separate pieces rather than being hard-wired into one monolithic assistant.
