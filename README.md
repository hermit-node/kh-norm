# Norm - source-only public release history

The earlier canonical Git history and tags through v0.53.20 are preserved. Each subsequent version tag points to a commit whose Src/ contains extracted Norm portable source package files. No installer sources, compiled installer executables, or portable-source ZIPs are included in the new commits.

Private machine settings, private network addresses, local user paths, credential material and runtime state are excluded or neutralized in these public copies. Consequently these are historical source snapshots, not byte-identical installable packages. The private verified ZIP originals remain in Norm_History_toGit outside the public repository.

CURRENT_RELEASE.md and PUBLIC_SOURCE_RECORD.json show the original archive hash and exactly which files were omitted or sanitized in each release. Old versions include broken, superseded, or experimental builds, with their historical quality annotations preserved. Tags record archival sequence rather than a claim of perfect development ancestry.

Stego implementation is frozen to the already published v0.53.20 through intermediate releases. The latest r23 carries its compiled Stego v3 plugin and public wrappers without raw algorithm sources. The r12a alternate browser-assets build is independently normalized and was catalogued after r20. The r21 standalone source ZIP could not be found; the lineage does not fabricate a version tag for it.
