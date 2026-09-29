Norm Installer Kit 1.4.9
========================

Included Norm source
--------------------
Norm 0.53.2 portable source.

What changed
------------
- Adds cryptography==46.0.4, cffi==2.0.0, and pycparser==3.0 to the reproducible Norm environment lock.
- The Norm PyInstaller build explicitly collects cryptography, cffi, and _cffi_backend so dynamically loaded plugins can use cryptography from the frozen norm.exe process.
- Splits Rotor5 and StegoSplit into independent first-party plugins:
  * plugins\rotor5_cipher — R5E2 five-machine rotor encoder/decoder.
  * plugins\stegosplit_message — StegoSplit V2 two-image authenticated carrier only.
- Adds NORM_ROTOR5_SECRET and NORM_ROTOR5_PREVIOUS_SECRETS support from Norm's configured .env.
- Retains the 1.4.8 newest-local-source discovery behavior: the installer selects the highest-version valid portable source ZIP beside it; modification time breaks same-version ties.

Typical update
--------------
1. Put the built Norm-Installer-1.4.9.exe beside Norm-0.53.2-portable-source.zip and its .sha256 file, or run Norm-Installer.py directly with Python.
2. Update C:\Norm in place.
3. Leave "compile norm.exe" enabled. The installer reuses/repairs C:\Norm\.venv, installs the pinned dependencies, then rebuilds core\norm.exe with cryptography bundled.

Builder
-------
Run Build-Norm-Installer-EXE.bat to compile the generic installer EXE on Windows. The builder continues to support exact locked dependencies or newest eligible stable/RC resolution.

Note
----
The kit itself contains source and the installer builder; a Windows norm.exe is produced on the target Windows machine by the normal installer/build path.
