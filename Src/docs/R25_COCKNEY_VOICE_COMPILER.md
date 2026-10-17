# r25: Cockney voice compiler corrections
Baseline: verified Norm 0.55.10r24 portable source. 2026-10-16.

Changes from r24:
* Voice-profile plugin 0.4.3 removes PDF cover/edition pages and strips editorial headings, footers and page numbers before selecting Cockney dialogue anchors. Ingested text and original PDFs are never changed.
* Dialect-aware anchor scoring rewards observed slang and colloquial rhythm instead of favoring generic editorial variety. Six anchors can cover all five volumes. Other voice profiles retain r24 selection behavior.
* Cockney-specific model synthesis prevents polite corporate flattening and retains cheeky East London idiom, sarcasm, contractions, occasional swearing and accurate technical explanation.
* All generated voice prompts forbid copying a source passage's memorable wording, images or scenarios. Cockney casual replies favor one or two natural sentences without restricting detailed technical answers.
* Portable offline regression: tools/test_cockney_compiler_r25.py, using synthetic records and a stubbed model.

PDF access policy:
A portable ZIP cannot silently widen trusted filesystem read roots. For PDFs stored under the local Norm state folder, the installation operator may add this narrowly scoped read-only entry in config/settings.ini, within the existing [file_access_overrides] section:
    voice_profile.read_add = C:\Norm\state\voice_profiles\Cockney
Use the actual installed path. The release itself preserves r24 configuration unchanged.

Installation/activation:
This is source only. Existing state/voice_profiles/Cockney corpus, compiled voice_profile.json, voice_prompt.txt, style_override.txt and source PDFs stay on the installed machine, not inside the ZIP. Source installation does not rebuild, restart or activate Norm.
After installation run:
    .venv\Scripts\python.exe -B tools\test_cockney_compiler_r25.py
For a new synthesis from an already-ingested corpus use the Norm operator command /voice-rebuild Cockney. Use /voice-activate Cockney only when activation is intended. No live inference was performed as part of building this archive.

Unchanged: all r24 Nabler fixes, N1/N2 architecture, no-Celery transport, browser, security rules and user configuration.
