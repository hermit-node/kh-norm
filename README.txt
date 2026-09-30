Norm Installer Kit 1.4.15
=========================

Included Norm source
--------------------
Norm 0.53.7 portable source.

What changed
------------
- Adds /about to both operator prompt surfaces.
- Adds /memory-condense for incremental consolidated-background-memory refresh.
- Adds /memory-condense -full for a full-source consolidated-background-memory rebuild.
- Memory condensation is scheduled directly on the existing worker and runs only when idle; it does not submit /api/chat, create a user task, or create a new task UUID.
- Reuses the existing checkpointed background-memory condensation engine, so interrupted condensation can resume from its checkpoint.
- Updates both visible help menus with /about and the two memory-condense forms.
- Retains Norm 0.53.2's separate Rotor5/StegoSplit plugins and frozen cryptography/cffi support.
- Retains newest-local-source discovery: the installer selects the highest-version valid portable source ZIP beside it; modification time breaks same-version ties.

Typical update
--------------
1. Put the built Norm-Installer-1.4.15.exe beside Norm-0.53.7-portable-source.zip and its .sha256 file, or run Norm-Installer.py directly with Python.
2. Update C:\Norm in place.
3. Leave "compile norm.exe" enabled. The installer reuses/repairs C:\Norm\.venv, installs pinned dependencies, then rebuilds core\norm.exe with cryptography bundled.

Memory command semantics
------------------------
/memory-condense
  Incrementally folds PostgreSQL records newer than the current consolidated snapshot into a new background-memory snapshot.

/memory-condense -full
  Rebuilds the consolidated background-memory snapshot from the complete surviving PostgreSQL source set.

Both commands are maintenance operations handled by the existing worker at an idle boundary. They are intentionally different from the still-unimplemented destructive /condense-memories design, which would reconcile/prune curated memory rows.

Builder
-------
Run Build-Norm-Installer-EXE.bat to compile the generic installer EXE on Windows. The builder continues to support exact locked dependencies or newest eligible stable/RC resolution.

Note
----
The kit itself contains source and the installer builder; a Windows norm.exe is produced on the target Windows machine by the normal installer/build path.

0.53.7 / 1.4.15 tuning:
- pre-task /suppress-task now covers active/queued DB3 ingress before a task UUID exists
- runtime PyInstaller analysis cache is persistent and normal builds are incremental
- cryptography is pinned to 50.0.2
- exact already-satisfied dependency locks skip pip reconciliation

1.4.15 builder UX / dependency persistence:
- After "All newest (resolve)" finishes, choose either "This build only" or "Update requirements + this build".
- The persistent choice rewrites tools\requirements-lock.txt inside the base portable source ZIP, updates matching core\requirements.txt direct pins, validates the new ZIP, and refreshes its .sha256 so later builder runs start from the versions you already approved.
- The choice popup is a child of the Make Norm Installer window and is centered over that window rather than the physical monitor.

0.53.7 recovery/interjection additions:
- /inject-context is restored to the current active task tree; injected text is persisted and delivered at the next model-call boundary without creating a new task.
- Weekly and manual memory-condense maintenance remove verified terminal recovery notes, summarize/replay-validate genuinely dangling nonterminal task trees before pruning, and remove unfinished descendants superseded by an already verified terminal root.
- Existing request_type/prompt_origin metadata is enforced so Norm-generated work is not described as user-authored.
