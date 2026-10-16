# r11r7 source candidate — Celery retired

Based on the installed 0.55.10r11r6 workspace-stability source (not the inaccessible Library archive).
No source or runtime files in C:\Norm were modified by this build.

- Removed Celery worker launcher, broker executor and package dependency.
- N1-approved tools use Norm's existing synchronous execution and evidence recording.
- Legacy in-flight Celery receipts are converted into N1-observed unresolved results without replay.
- Suppression and resume remain in the original Redis/PostgreSQL task-parking infrastructure.
- One-time process-identity-scoped cleanup of legacy Norm workers occurs at new-service startup.
- The diagnostic label 'Ollama n2-worker' existed in 0.54.0c before Celery and is not taken as evidence of a changed reasoning role.
- No live Windows/Redis/Ollama/PostgreSQL behavioral smoke test has been performed.
- Source-only candidate: compile a fresh norm.exe; do not reuse the Celery-enabled executable.
- Existing local config and user plugins must be preserved during installation; live credentials are excluded from this source candidate.
