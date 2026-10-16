"""Direct slash shortcuts for Norm's existing shared-browser PDF plugin.

Dispatch is local to Norm Prompt, not a queued model job, with the same
capability-scoped filesystem access as the callable PDF plugin functions.
"""
from __future__ import annotations

import json
import shlex
import sys
import threading
from pathlib import Path
from typing import Callable

PDF_COMMANDS = frozenset({
    '/pdf', '/pdf-chat', '/pdf-dialogue', '/pdf-name', '/pdf-verify',
    '/pdf-text', '/pdf-visual', '/pdf-status', '/pdf-cleanup',
})
_PDF_LOCK = threading.Lock()


def _parts(command_line: str) -> list[str]:
    # Unlike POSIX shlex, retain Windows drive/backslash syntax verbatim.
    values=shlex.split(command_line, posix=False)
    return [s[1:-1] if len(s)>=2 and s[0]==s[-1] and s[0] in ('"', "'") else s
            for s in values]


def _plugin(root: Path):
    import importlib.util
    root=Path(root).resolve()
    plugin=root/'plugins/shared_browser'
    sys.path.insert(0,str(root/'core')) if str(root/'core') not in sys.path else None
    from norm_runtime.plugin_identity import verify_identity
    verify_identity(plugin)
    module_path=plugin/'src/main.py'
    spec=importlib.util.spec_from_file_location('_norm_pdf_slash_plugin',module_path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse(args: list[str], *, allowed: set[str], boolean: set[str] = frozenset()):
    values=[];flags={};i=0
    while i<len(args):
        entry=args[i]
        if entry.startswith('--'):
            if entry not in allowed:
                raise ValueError(f'Unsupported flag: {entry}')
            if entry in flags:
                raise ValueError(f'Duplicate flag: {entry}')
            if entry in boolean:
                flags[entry]=True
            else:
                i+=1
                if i==len(args) or args[i].startswith('--'):
                    raise ValueError(f'{entry} requires an argument')
                flags[entry]=args[i]
        else:
            values.append(entry)
        i+=1
    return values, flags


def _emit(obj: dict, out: Callable[[str],None]):
    out(json.dumps(obj,indent=2,ensure_ascii=False,default=str))


def _action(command_line: str, root: Path, out: Callable[[str],None]):
    parts=_parts(command_line)
    if not parts:raise ValueError('Empty PDF command')
    cmd=parts[0].lower()
    args=parts[1:]
    if cmd=='/pdf':
        if args:raise ValueError('Usage: /pdf (help)')
        out('PDF formats: /pdf-chat "SOURCE.pdf" [--name TITLE] [--output PATH] [--include-original], '
            '/pdf-text ARCHIVE, /pdf-visual [NAME]. See /help shared-browser for /pdf-verify, /pdf-name, /pdf-cleanup.')
        return
    shared=_plugin(root)
    if cmd in ('/pdf-chat','/pdf-dialogue'):
        items,opts=_parse(args,allowed={'--name','--output','--include-original'},boolean={'--include-original'})
        if len(items)!=1:
            raise ValueError(f'Usage: {cmd} "SOURCE.pdf" [--name "TITLE"] [--output "PATH.pdf"] [--include-original]')
        out('Creating chat-style vector-preserving dialogue with native PDF text/images; existing files protected...')
        _emit(shared.browser_shared_pdf_export(format='dialogue',source=items[0],
              name=opts.get('--name',''),output=opts.get('--output',''),
              include_original=opts.get('--include-original',False)),out)
    elif cmd=='/pdf-name':
        if len(args)!=1:raise ValueError('Usage: /pdf-name "SOURCE.pdf"')
        _emit(shared.browser_shared_pdf_title_context(args[0]),out)
    elif cmd=='/pdf-verify':
        if len(args)!=2:raise ValueError('Usage: /pdf-verify "SOURCE.pdf" "DIALOGUE.pdf"')
        _emit(shared.browser_shared_pdf_verify(args[0],args[1]),out)
    elif cmd=='/pdf-text':
        if len(args)!=1:raise ValueError('Usage: /pdf-text "ARCHIVE NAME"')
        _emit(shared.browser_shared_pdf_export(format='text',name=args[0]),out)
    elif cmd=='/pdf-visual':
        if len(args)>1:raise ValueError('Usage: /pdf-visual ["TITLE"]')
        _emit(shared.browser_shared_pdf_export(format='visual',name=args[0] if args else ''),out)
    elif cmd=='/pdf-status':
        if args:raise ValueError('Usage: /pdf-status')
        _emit(shared.browser_shared_visual_status(),out)
    elif cmd=='/pdf-cleanup':
        items,opts=_parse(args,allowed={'--execute','--older-hours'},boolean={'--execute'})
        if len(items)>1:raise ValueError('Usage: /pdf-cleanup ["FOLDER"] [--older-hours 24] [--execute]')
        try:hours=int(opts.get('--older-hours',24))
        except ValueError as exc:raise ValueError('--older-hours must be an integer') from exc
        _emit(shared.browser_shared_pdf_cleanup(directory=items[0] if items else '',
              dry_run=not opts.get('--execute',False),older_than_hours=hours),out)
    else:
        raise ValueError('Unsupported PDF shortcut')


def run_pdf_command(line: str, root: Path, out: Callable[[str],None] = print) -> bool:
    command=line.strip().split(maxsplit=1)[0].lower() if line.strip() else ''
    if command not in PDF_COMMANDS:return False
    if command=='/pdf':
        try:_action(line,root,out)
        except Exception as exc:out(f'PDF command failed: {type(exc).__name__}: {exc}')
        return True
    if not _PDF_LOCK.acquire(blocking=False):
        out('A PDF command is already running; nothing queued.')
        return True
    def work():
        try:_action(line,root,out)
        except Exception as exc:out(f'PDF command failed: {type(exc).__name__}: {exc}')
        finally:_PDF_LOCK.release()
    try:threading.Thread(target=work,name='norm-pdf-shortcut',daemon=True).start()
    except Exception:
        _PDF_LOCK.release();raise
    out('Direct PDF command started (not queued). Result will appear in Norm Prompt.')
    return True
