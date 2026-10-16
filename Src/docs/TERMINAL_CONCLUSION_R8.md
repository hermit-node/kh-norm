# Norm 0.55.8r8: Celery-based terminal conclusion

Only intercept Norm's LAST planned execution/conclusion step, not the N1/N2 core execution pipeline. N1 assigns atomic verification checks to five real Celery workers. Redis DB5 is the Celery broker, result backend, and report card; DB0-DB3 remain used by the existing application. N1 polls Redis every three seconds.

Atomic read-only worker methods:
- file_exists(path), directory_exists(path): Python path predicates
- sha256_matches(path, sha256): streamed SHA-256 comparison, bounded at 512MB
- text_contains(path, text): bounded UTF-8 literal membership
- model_review: small Ollama N2 model prompt, only for semantic validity

Deterministic checks require no Ollama calls, and return TRUE/FALSE/UNKNOWN with evidence to Redis. Compare-and-set transactions prevent stale verdict overwrites. Workers have NO project mutation tools.

If all work passes, N1 outlines user-facing paragraphs. Each Celery paragraph task drafts and evidence-checks one paragraph. Accepted paragraphs persist on DB5 before final Python assembly; original task ID remains the same.

If there is proven missing work, N1 creates one successor task only for incomplete steps; original task ends PASSED_TO:new_id with pointers to completed work and report cards. UNKNOWN never implies success or a need to edit files.

## Windows worker startup
The external scripts tools/start_terminal_workers.py and tools/terminal_worker_entry.py launch five separately running Python worker processes using Celery --pool=solo, --concurrency=1. Norm automatically starts missing workers when entering the terminal step. Celery is NOT a ThreadPoolExecutor shim, and inference workers are independent processes.

Core files norm_runtime/terminal_conclusion.py and norm_runtime/terminal_celery.py are bundled into norm.exe by the installer. Standalone tools scripts are external. Installer 1.6.12 owns the executable build and this package does not include norm.exe. The installer must install package dependencies (Celery 5.6.3, Kombu 5.6.2, redis 6.4.0) and rebuild. Celery prefork is not used on Windows.

## Configuration and installation
Optional terminal_conclusion runtime.json options: celery_db=5 (only 4 or 5), workers=5 (range 1..5), autostart_workers=true, deadline_seconds=3600, max_checks=24. No need to overwrite local config/settings.ini, .env, secrets, state or plugins.

Select this r8 ZIP in Installer 1.6.12, with the adjacent matching .sha256 file. Use source/package dependency mode, and allow rebuilding norm.exe. Stop the staged development workers before installing so the new processes use installed code and not a stale source tree.

## Testing / limitations
Live DB5 round-trip test: three Celery jobs returned TRUE, FALSE, TRUE for correct existence checks. Offline validation and installer-manifest verification follow. End-to-end validation of a rebuilt norm.exe, Ollama semantic checks, restart interleavings and a full real user task remains required.
