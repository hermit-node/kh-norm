# Operator console voice commands / plugin help patch (0.54.3 source)

This patch changes the Python operator-console tools and documentation; it does
not change `core/norm.exe`, the plugin schema-2 `src/` code, or model weights.
It can be applied to the installation by copying `tools/norm_gui_prompt.py`,
`tools/norm_voice_commands.py`, `tools/norm_plugin_help.py`,
`docs/help_menu.txt`, `docs/help_full.txt`, and `plugins/*/help.txt` from
this archive to their matching paths. Close/reopen **Norm Prompt** after
copying; stopping the Norm core service is not necessary for these files.

The default `/help` is now a short index; `/help plugins` enumerates actual
`help.txt` manuals under installed plugins; `/help voice-profiler` shows the
full list of direct voice operations. Unlike a queued prompt, these commands
do not rely on N2 to decide if the profile is active. `/voice-activate NAME`
compares the newly rendered prompt and current active prompt SHA, and refreshes
it when profile JSON changes. The default build uses text extraction only
(`vision_parse` in text-layer mode; no image model processing).

Limitations: the console and Norm must use the same local installation and
`state_root`; long local build operations run in a daemon thread while the
console is open. The active voice is already consumed by the conversation
service in source 0.54.3. Existing queued N2 work may not use the active
voice instructions: this patch changes operator access, not N2 prompt wiring.
The separate context-injection lifecycle/retirement checkbox is not part of
this focused patch. The console still handles `/inject-context` as before.

Test: from the source root on a Python installation with the dependencies:
`PYTHONPATH=core:tools:. python -m unittest tools.test_voice_slash_commands`.
