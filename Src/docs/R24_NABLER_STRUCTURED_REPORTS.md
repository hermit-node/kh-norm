# r24 Nabler structured-output transport

Built directly from SHA-256-verified r23 portable source, with only the intended Nabler changes.

N2 prefers the existing Nabler rendering tool for structured human-readable results at any task phase, including intermediate steps. The existing native call returns JSON-compatible rows, counts, title, mode, output and rendered text. No Arrow, pickle or executable callbacks are serialized.

The Redis compact evidence stream and ledger now include Nabler's rendered content and structured rows (bounded by existing 100-row / 24000-character compact-field limits). PostgreSQL still records complete tool evidence. After successful execution, N2 publishes a nabler_report event; the integrated and independent Runtime consoles render that text and do not dump Nabler tool-call argument JSON.

This is source-only: the active Norm process is not restarted. An ordinary later restart is required to load Python changes. N1 verification, no-Celery, shared browser, tools, and persistence are otherwise unchanged. The r23 settings for context budgets, allowed roots, plugins and workspace geometry are intentionally preserved rather than copied from the live installation. Formatting is not verification evidence.

Run the standalone in-memory regression after installing:
    .venv\Scripts\python.exe -B tools\test_nabler_display_r24.py
