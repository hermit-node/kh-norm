# Publishing kh-norm

## Public repository push

From PowerShell in this directory:

```powershell
.\Publish-To-GitHub.ps1 -Push
```

The publisher works in Windows PowerShell 5.1 and intentionally stages each public path separately.

`norm-imprint.local.json` may remain in this same directory. It is listed in `.gitignore`, checked before publishing, and never staged by the publisher.

If Git needs your author identity, configure it once and rerun:

```powershell
git config --global user.name "YOUR GITHUB NAME"
git config --global user.email "YOUR GITHUB EMAIL"
```

## Tag

After `main` is pushed:

```powershell
git tag -a v0.53.9 -m "Norm 0.53.9 / Unified Installer 1.6.0"
git push origin v0.53.9
```

## GitHub Release

GitHub CLI is optional. In the repository web UI choose **Releases → Draft a new release**, select `v0.53.9`, use `RELEASE.md` as the release notes, and attach:

- `Norm-0.53.9-portable-source.zip`
- `Norm-0.53.9-portable-source.zip.sha256`
