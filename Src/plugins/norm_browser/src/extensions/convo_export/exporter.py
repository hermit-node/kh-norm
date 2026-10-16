"""Local, selective ChatGPT exports for Norm's browser cooperation plugin.

Produces Markdown + assets, JSON, a standalone PDF, or a ZIP. The exporter
never uploads content to a vendor. Unresolved assets are documented rather
than being silently omitted. No web fetch is attempted for unsupported hosts.
"""
from __future__ import annotations
import html
import json
import mimetypes
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

MAX_ASSET = 24 * 1024 * 1024
MAX_TOTAL_ASSETS = 512 * 1024 * 1024


def _slug(s: str) -> str:
    return re.sub(r'[^A-Za-z0-9._-]+', '_', str(s or 'ChatGPT'))[:75].strip('._') or 'ChatGPT'


def _safe_host(url: str) -> bool:
    try:
        u = urlsplit(url)
        if u.scheme != 'https' or u.username or u.password or (u.port not in (None, 443)):
            return False
        host = (u.hostname or '').lower()
        return host in {'chatgpt.com', 'chat.openai.com', 'openai.com', 'oaistatic.com', 'oaiusercontent.com'} or any(
            host.endswith('.' + base) for base in ('openai.com','oaistatic.com','oaiusercontent.com')
        )
    except ValueError:
        return False


class _RestrictedRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _safe_host(newurl):
            raise ValueError('Attachment redirect outside approved content hosts')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(url: str, destination: Path):
    if not _safe_host(url):
        raise ValueError('Download URL is not a recognized ChatGPT asset host')
    opener = build_opener(_RestrictedRedirect())
    with opener.open(Request(url, headers={'User-Agent': 'NormBrowser/0.1'}), timeout=18) as response:
        if int(response.headers.get('Content-Length') or 0) > MAX_ASSET:
            raise ValueError('Asset exceeds 24 MiB limit')
        total=0
        with destination.open('wb') as output:
            while True:
                chunk=response.read(min(65536, MAX_ASSET + 1 - total))
                if not chunk:break
                total+=len(chunk)
                if total>MAX_ASSET:raise ValueError('Asset exceeds 24 MiB limit')
                output.write(chunk)
    if total==0:raise ValueError('Zero-byte download')
    return total


def _date(value) -> str:
    if value is None or value=='':return ''
    try:return datetime.fromtimestamp(float(value), tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    except (ValueError,TypeError,OverflowError):return str(value)[:64]


def _markdown(messages, title, options, manifest):
    lines=['# '+title, '', 'Source: '+str(manifest.get('source_url') or ''), '',
           'Coverage: '+('API-selected conversation branch' if manifest.get('complete') else
                         'Partial DOM snapshot — completeness NOT verified'), '']
    for index,m in enumerate(messages,1):
        role=str(m.get('role') or 'unknown').capitalize()
        line=f'## {index}. {role}'
        if options.get('timestamps') and m.get('timestamp'):
            line+=' · '+_date(m['timestamp'])
        if options.get('models') and m.get('model'):
            line+=' · '+str(m['model'])[:80]
        lines.extend([line,'',str(m.get('text') or ''),''])
        if options.get('attachments',True):
            for attachment in m.get('attachments') or []:
                label=str(attachment.get('name') or 'Attachment').replace(']','\\]')[:100]
                local=attachment.get('local_path')
                if local:
                    if attachment.get('kind')=='image':lines.append('!['+label+']('+local+')')
                    else:lines.append('['+label+']('+local+')')
                else:lines.append('*[Attachment unavailable: '+label+']*')
                lines.append('')
    return '\n'.join(lines).rstrip()+'\n'


def _pdf(messages, title: str, destination: Path, options: dict, asset_dir: Path):
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, letter
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_LEFT
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image
        from reportlab.lib.units import inch
    except ImportError as exc:
        raise RuntimeError('ReportLab is required for local PDF output') from exc
    compact=options.get('density','comfortable')=='compact'
    style=getSampleStyleSheet()
    body=ParagraphStyle('NormBody',parent=style['BodyText'],fontName='Helvetica',fontSize=9 if compact else 10,
                        leading=12 if compact else 15,spaceAfter=6,splitLongWords=True)
    title_style=ParagraphStyle('NormTitle',parent=style['Heading1'],fontSize=17,leading=21)
    heading=ParagraphStyle('NormHeading',parent=style['Heading2'],fontSize=11,leading=15,
                           textColor=colors.HexColor('#20365a'),spaceBefore=10)
    doc=SimpleDocTemplate(str(destination),pagesize=A4 if options.get('page_size')=='A4' else letter,
                          leftMargin=0.65*inch,rightMargin=0.65*inch,
                          topMargin=0.6*inch,bottomMargin=0.6*inch)
    story=[Paragraph(html.escape(title),title_style),Spacer(1,8)]
    max_width=doc.width
    for idx,m in enumerate(messages,1):
        head=f'{idx}. '+str(m.get('role') or 'Unknown').capitalize()
        if options.get('timestamps') and m.get('timestamp'):head+=' · '+_date(m['timestamp'])
        if options.get('models') and m.get('model'):head+=' · '+str(m['model'])[:50]
        story.append(Paragraph(html.escape(head),heading))
        for para in (str(m.get('text') or '').split('\n\n') or ['']):
            clean=html.escape(para).replace('\n','<br/>')
            if clean:story.append(Paragraph(clean,body))
        if options.get('attachments',True):
            for asset in m.get('attachments') or []:
                local=asset.get('local_path')
                if local and asset.get('kind')=='image':
                    try:
                        from PIL import Image as PILImage
                        file=asset_dir.parent / local
                        with PILImage.open(file) as img:
                            w,h=img.size
                        if w and h:
                            scale=min(1.,max_width/w,470./h)
                            story.append(Image(str(file),width=w*scale,height=h*scale))
                    except Exception:
                        story.append(Paragraph('[Image saved in assets: '+html.escape(str(local))+']',body))
                elif local:
                    story.append(Paragraph('[Attachment: '+html.escape(str(local))+']',body))
                else:
                    story.append(Paragraph('[Attachment unavailable: '+html.escape(str(asset.get('name') or '?'))+']',body))
    doc.build(story)


def export_conversation(messages: list[dict], *, root: Path, title: str,
                        fmt: str='zip', options: dict | None=None,
                        source_url: str='',complete: bool=False,source: str='dom') -> dict:
    """Create one export atomically. Caller must supply explicitly selected messages.

    No source credentials or tokens are included. Never claims DOM-only data is complete.
    """
    if fmt not in {'zip','pdf','md','json'}:raise ValueError('Choose ZIP, PDF, Markdown or JSON')
    if not messages:raise ValueError('Select at least one conversation message')
    if len(messages)>30000:raise ValueError('Too many messages')
    options=dict(options or {})
    out=Path(root);out.mkdir(parents=True,exist_ok=True)
    name=_slug(title)
    base=name+'_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    dest=out/base
    temp=Path(tempfile.mkdtemp(prefix='.norm-export-',dir=str(out)))
    asset_dir=temp/'assets';asset_dir.mkdir()
    items=json.loads(json.dumps(messages,ensure_ascii=False))
    manifest={'title':title,'source_url':source_url,'source':source,'complete':bool(complete),
              'selected_count':len(items),'created_at':datetime.now(timezone.utc).isoformat(),
              'format':fmt,'options':options,'missing_assets':[],'saved_assets':[]}
    try:
        seen={};count_bytes=0
        if options.get('attachments',True):
            for i,message in enumerate(items):
                for j,a in enumerate(message.get('attachments') or []):
                    url=a.get('url') or ''
                    if not url:
                        manifest['missing_assets'].append({'message':i,'asset':j,'name':a.get('name'),'reason':'No resolved URL'})
                        continue
                    if url in seen:
                        a['local_path']=seen[url];continue
                    suffix=Path(urlsplit(url).path).suffix.lower()
                    if suffix not in {'.png','.jpg','.jpeg','.gif','.webp','.svg','.pdf','.txt','.md','.zip','.docx','.xlsx'}:
                        suffix=mimetypes.guess_extension(a.get('mime') or '') or '.bin'
                    local=f'assets/{i+1:05d}_{j+1:02d}{suffix}'
                    file=temp/local
                    try:
                        if count_bytes>=MAX_TOTAL_ASSETS:raise ValueError('Export asset budget exhausted')
                        size=_download(url,file)
                        count_bytes+=size
                        if count_bytes>MAX_TOTAL_ASSETS:raise ValueError('Export asset budget exceeded')
                        a['local_path']=local;seen[url]=local
                        manifest['saved_assets'].append({'path':local,'bytes':size})
                    except Exception as exc:
                        file.unlink(missing_ok=True)
                        manifest['missing_assets'].append({'message':i,'asset':j,
                            'name':a.get('name'),'reason':str(exc)[:180]})
        # Do not publish temporary signed asset URLs in durable JSON exports.
        for message in items:
            for a in message.get('attachments') or []:
                a.pop('url',None)
        (temp/'conversation.json').write_text(json.dumps({'manifest':manifest,'messages':items},
                                       ensure_ascii=False,indent=2),encoding='utf8')
        (temp/'conversation.md').write_text(_markdown(items,title,options,manifest),encoding='utf8')
        (temp/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
        if fmt=='pdf':
            path=dest.with_suffix('.pdf')
            _pdf(items,title,temp/'conversation.pdf',options,asset_dir)
            shutil.move(str(temp/'conversation.pdf'),path)
        elif fmt=='zip':
            path=dest.with_suffix('.zip')
            with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
                for file in sorted(temp.rglob('*')):
                    if file.is_file():z.write(file,file.relative_to(temp))
        elif fmt=='md':
            path=dest.with_suffix('.md')
            shutil.move(str(temp/'conversation.md'),path)
            if manifest['saved_assets']:
                # Distinct per-export asset folder; rewrite references accordingly.
                assets_name=base+'_assets'
                (out/assets_name).mkdir(exist_ok=False)
                shutil.copytree(asset_dir,out/assets_name,dirs_exist_ok=True)
                md=path.read_text(encoding='utf8').replace('](assets/',']('+assets_name+'/')
                path.write_text(md,encoding='utf8')
        else:
            path=dest.with_suffix('.json')
            shutil.move(str(temp/'conversation.json'),path)
            if manifest['saved_assets']:
                assets_name=base+'_assets'
                shutil.copytree(asset_dir,out/assets_name)
                record=json.loads(path.read_text(encoding='utf8'))
                for m in record['messages']:
                    for a in m.get('attachments') or []:
                        if a.get('local_path'):
                            a['local_path']=a['local_path'].replace('assets/',assets_name+'/',1)
                path.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf8')
        return {'path':str(path),'format':fmt,'message_count':len(items),
                'saved_assets':len(manifest['saved_assets']),
                'missing_assets':len(manifest['missing_assets']),
                'coverage':'api-selected-branch' if complete else 'partial-unverified'}
    finally:
        shutil.rmtree(temp,ignore_errors=True)
