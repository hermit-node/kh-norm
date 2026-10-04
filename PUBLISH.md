# Publishing kh-norm 0.53.14

The supplied public ZIP is an update bundle for the existing public repository. Extract it over a clean clone, replacing `src/Norm` with the bundled version, review `git status`, then commit and push `main`.

Tag after the push:

```powershell
git tag -a v0.53.14 -m "Norm 0.53.14 / Installer 1.6.6"
git push origin v0.53.14
```
