# Norm

**Norm is a self-hosted AI agent for Windows.** It uses local Ollama models to plan work, call tools, inspect results, and keep tasks moving without requiring you to restart from scratch after every interruption.

Give Norm an ordinary-language request—such as reviewing a directory, analyzing a document, working on a code project, or processing a video. It breaks the work into steps, runs approved operations, and reports what happened.

## What Norm can do

- **Files and code:** inspect folders, analyze files, propose and carry out changes, and run verification steps.
- **Documents and media:** OCR and analyze PDFs or images; detect video scene boundaries and faces and return structured metadata.
- **Databases and reports:** work with configured PostgreSQL data, use Redis for reusable evidence and task coordination, and produce structured reports.
- **Long-running tasks:** retain checkpoints, show progress, suppress work, and resume eligible tasks later.
- **Plugins:** add hot-swappable tools without modifying the N1/N2 core.

Norm is not a hosted chatbot. It runs in your own Windows environment with access limited by the tools, services, and file permissions you configure.

## How it works

Norm separates reasoning from execution:

- **N2 (worker)** plans the task, interprets results, and proposes tool actions.
- **N1 (gatekeeper)** checks each proposal against the task and available evidence. It approves and executes authorized actions or returns a reason for N2 to revise them.
- **You (operator)** submit requests, inspect replies and runtime activity, and control tasks through the Norm interface.

N2 does not execute tools directly. Ordinary-language tasks pass through that workflow; supported slash commands are direct operator controls.

## Install Norm

Norm's **source ZIP is not the installer**. You need the separately distributed Norm installer, plus the source package you want to install.

1. Download the [latest Norm source release](https://github.com/hermit-node/kh-norm/releases/latest).
2. Run your Norm installer and select the downloaded source ZIP as its source package. The ZIP opens directly to folders such as `core/`, `plugins/`, `tools/`, and `config/`—there is no extra `Src/` wrapper.
3. Configure the installation's Ollama, Redis, PostgreSQL, and Tailscale connectivity as required by your environment. The installer manages the Python environment and builds the Norm executable.
4. Confirm that the installed folder contains `core/norm.exe`, `.venv/`, and `config/settings.ini` before launching.

The default installation root in the included configuration example is `C:\Norm`, but your installer may use a different directory. Settings live in `config/settings.ini`; credentials should be kept in the configured secrets file, not in the GitHub repository.

## Launch Norm

From your installed Norm folder:

| Launcher | What it opens |
| --- | --- |
| `Run-Norm.bat` | Starts or reuses the backend and opens the Qt workspace |
| `Run-Norm-Terminals.bat` | Starts or reuses the backend and opens separate **Norm Prompt**, **Norm Replies**, and **Norm Runtime** windows |

You can also open the installer-created shortcuts if they are available.

**Norm Prompt** is where you type tasks. **Norm Replies** displays results; **Norm Runtime** shows activity. The launcher expects the installed Python environment, compiled executable, settings, and configured Tailscale connectivity to be available.

## Give Norm its first task

Type a normal request into **Norm Prompt**, for example:

> Inspect my Downloads folder, group the files by type and project, and show me a proposed organization before moving anything.

Or:

> Analyze this video for scene changes and face appearances. Return the scene timestamps and face-observation IDs as structured results. Do not rename people or merge conflicting manual identities.

Norm handles the request through N2 planning and N1-approved tool actions. You can review results in Replies and track the work in Runtime. Be explicit about constraints such as **preview only**, **do not overwrite**, or **ask before deleting**.

## Useful commands

Type these in **Norm Prompt**:

| Command | Purpose |
| --- | --- |
| `/help` | Show the available operator commands |
| `/help plugins` | List plugin-specific help manuals |
| `/status` | Show Norm's runtime and queue status |
| `/queue` | See pending tasks |
| `/queue-full` | Include parked tasks and fuller details |
| `/about` | Show version, runtime and plugin information |
| `/switch-model` | List available model choices |
| `/switch-model NAME` | Switch to a named model |
| `/suppress-task` | Suppress the current/next active task |
| `/resume-task` | See or resume suppressed tasks |
| `/failed` | Show recent failed tasks |
| `/new` | Start a new conversation |
| `/stop-all` | Finish the current step and shut down Norm/Ollama |
| `/exit` | Close the prompt window without stopping Norm |

Use `/help` inside your installed version as the authoritative command list; plugin availability and installed versions can differ.

## Plugins

Norm's built-in and added tools live under the `plugins/` directory. Ask `/help plugins` in Norm Prompt to see the installed plugin manuals and available operations.

Examples include [Vision](Src/plugins/vision_parse/README.md) for documents and media, [Nabler](Src/plugins/nabler/README.md) for structured output, and tools for filesystem, database, browser, voice-profile, and task workflows. Plugins expose tools to the existing N1 approval/execution path rather than bypassing it.

## When something will not start

Check that Ollama, Redis, PostgreSQL, and the configured Tailscale connection are available, and verify that `config/settings.ini` points to the right hosts and ports. The startup script reports missing files and unhealthy services, and can show recent logs from `logs/`.

For the source tree, runtime details, and plugin manuals, see the [operator documentation](Src/docs/README.md) and [plugin index](Src/plugins/README.md).
