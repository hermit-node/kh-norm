# Norm

A persistent, self-hosted AI agent runtime that can remember context, use controlled tools, carry work across sessions, and recover interrupted tasks.

Norm is built to do more than answer a prompt. A user request can become bounded work backed by PostgreSQL durable state, Redis live coordination, local Ollama models, and hot-loaded Python tools.

## 0.53.14: N1/N2 checkpoint 1

Norm now starts two logical agent roles while keeping ordinary conversation transparent:

```text
USER -> N1 -> N2
             |
             | proposes tool + needed fact/target
             v
            N1 gate
          /    |     \
 existing   execute   stop/reset loop
 answer      tool
   |           |
   +------> raw result -> N2

N2 response -> N1 -> USER
```

At this checkpoint, N2 remains the working/reasoning path. N1 does not rewrite normal user input or N2's user-facing answer. N1 intervenes at model-requested tool boundaries and when repeated reasoning/tool turns indicate a rabbit hole.

For information tools, N2 states what information it needs and proposes the real tool/arguments. N1 checks the live validation pool, reuses an existing sufficiently current observation when appropriate, or invokes the tool and forwards the raw tool result to N2 unchanged. Fresh observations are recorded by N1 rather than by N2.

## What Norm does

- **Persistent memory and task state** in PostgreSQL.
- **Live queues and validation evidence** in Redis.
- **Local inference** through Ollama.
- **N1/N2 separation of authority**: N2 reasons and works; N1 controls model-requested tool execution and loop intervention.
- **Recoverable long-running tasks** with bounded steps and checkpoints.
- **Hot-loaded Python plugins** with source identity verification and last-known-good fallback.
- **Bounded file/PDF workflows** for large sources.
- **Operator controls** for queueing, suppression/resume, backup, status, shutdown, and maintenance.

## Quick start

Norm currently targets Windows. Keep the installer and portable source package together:

```text
Norm-Installer.py
installer_environment.py
Norm-0.53.14-portable-source.zip
Norm-0.53.14-portable-source.zip.sha256
norm-imprint.example.json
```

Run the installer script with Python, or build/use the Windows installer executable. Existing installations are updated in place; persistent runtime state is not part of the public source archive.

## Architecture

```text
                         USER
                          |
                          v
                         N1
                ingress / tool gate / loop guard
                          |
                          v
                         N2
                   reasoning / worker
                          |
                    tool proposal
                          |
                          v
                         N1
                reuse evidence or execute
                          |
             +------------+------------+
             |            |            |
         PostgreSQL      Redis      native/plugins
       durable state   live state       tools
                          |
                         N2
                          |
                         N1
                          |
                         USER
```

## Configuration and privacy

The public repository contains generic configuration only. Machine-specific deployment topology should be kept in ignored local configuration/imprint files. Passwords, tokens, SSH private keys, OAuth material, runtime databases, logs, and full-state backups must not be committed.

The portable source package deliberately excludes `.venv`, generated executables, runtime state, secrets, SSH material, logs, and user workspace content.

## Repository layout

```text
src/Norm/       Norm runtime source and bundled plugins
Norm-Installer.py
installer_environment.py
Norm-0.53.14-portable-source.zip
Norm-0.53.14-portable-source.zip.sha256
```

More detailed runtime documentation lives under `src/Norm/docs/`.

## Status

Norm is under active development. Version 0.53.14 is the first N1/N2 tool-gating checkpoint; both logical roles may still target the same configured model/endpoint until separate hosts/models are assigned.
