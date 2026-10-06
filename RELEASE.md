# Norm 0.53.18 / Installer 1.6.8-unified

Key current-release changes:

- weekly regular memory is aggressively synthesized into a small working-memory snapshot rather than source-by-source restatement;
- active/parked maintenance appears in queue/busy status and can be suppressed/resumed by the operator;
- deterministic output truncation parks scheduled maintenance instead of hot-looping;
- /switch-model remains fail-closed and session-only; boot always uses norm;
- operator help is sourced from src/Norm/docs/help_menu.txt;
- public delivery no longer vendors the frozen WeasyPrint/Pango runtime;
- Installer 1.6.8 and runtime repair fetch the exact official WeasyPrint 70.0 Windows archive using the package-pinned URL/SHA and require a real PDF render.
