# Norm lightweight launcher (isolated prototype)

A small .NET Framework Windows launcher for Norm, built with the C# compiler
already present on KHzz. Requires Windows .NET Framework 4.x and uses Norm's
existing Python virtual environment.

This replaces heavyweight PyInstaller packaging only in a future, explicitly
approved installer revision. It does not change any existing Norm install.

## Layout expected at installation

    C:\Norm\core\norm.exe
    C:\Norm\core\norm_main.py
    C:\Norm\core\norm_runtime\*.py
    C:\Norm\.venv\Scripts\python.exe
    C:\Norm\tools\, plugins\, config\, ...

The launcher locates its own directory (not the current shell directory),
executes .venv\Scripts\python.exe core\norm_main.py, passes all arguments,
sets NORM_ROOT and CWD, streams inherited stdout/stderr, waits for the Python
process, and forwards its exit code. It uses a Windows Job Object so killing
the launcher also kills the Python child.

## Build and smoke test

    py -3.14 build_launcher.py
    py -3.14 test_launcher.py

Build output: dist\norm.exe

## Scope and constraints

- Existing plugins and custom tools remain Python code in the same .venv.
- The main Python source stays on disk; this launcher is NOT a frozen application.
- Current installer --self-test-celery expects a frozen app. An installer
  adapted to this launcher must validate actual source-based Celery imports
  and worker process startup instead, not require frozen_celery=true.
- No venv recreation, plugin mutation, settings migration, state cleanup or
  source-file deletion is performed by this project.
- A real end-to-end smoke test must precede any replacement of core\norm.exe.
- .NET Framework 4.x runtime is required; this is a small managed Windows
  executable, not an unmanaged C/C++ binary.
