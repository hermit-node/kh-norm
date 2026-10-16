# r11r8 installer-ready source candidate — Celery retired

Based on 0.55.10r11r7 Strict Recovery IDs portable ZIP, preserving its recovery changes and bundled launcher.
No source or runtime files in C:\Norm were modified by this build.

- Removed Celery worker launcher, broker executor and package dependency.
- N1-approved tools use Norm's existing synchronous execution and evidence recording.
- Legacy in-flight Celery receipts are converted into N1-observed unresolved results without replay.
- Suppression and resume remain in the original Redis/PostgreSQL task-parking infrastructure.
- One-time process-identity-scoped cleanup of legacy Norm workers occurs at new-service startup.
- The diagnostic label 'Ollama n2-worker' existed in 0.54.0c before Celery and is not taken as evidence of a changed reasoning role.
- No live Windows/Redis/Ollama/PostgreSQL behavioral smoke test has been performed.
- Installer 1.6.12 validates the supplied lightweight launcher and legacy --self-test-celery alias (direct transport; no Celery import).
- Installer preserves existing local config and user plugins; source ZIP retains the original distributable package resources.
