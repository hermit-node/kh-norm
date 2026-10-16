"""Native-vector ChatGPT Exporter dialogue reflow, with source fidelity audit.

All content comes from native source-page drawing operators, not reconstructed
text/HTML. No rasterization, no OCR. Intentionally excludes exporter's overview
and page footers; retains chat bubbles, inline fonts, bullets, code, images,
and citation links.

The reading layout is chat-oriented rather than article-oriented: user turns stay
visibly separate from assistant turns, page breaks carry continuation labels, and
assistant content is easier to scan as a back-and-forth transcript.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import os
import re
import unicodedata

import pymupdf as fitz

DATE = re.compile(r"(\d{1,2}/\d{1,2}/\d{4},\s*\d{1,2}:\d\d:\d\d\s*[AP]M)")
CONTENT_LEFT, CONTENT_RIGHT = 31.0, 567.0
PAGE_WIDTH, PAGE_HEIGHT = 595.28, 841.89
READ_LEFT, READ_RIGHT = 35.5, 559.5
TOP, BOTTOM = 66.0, 49.0
ROLE_GAP = 18.0
FRAGMENT_GAP = 6.0
PAGE_RULE = (.86, .89, .93)
TEXT_DARK = (.15, .19, .27)
TEXT_MID = (.43, .50, .57)
USER_ACCENT = (.30, .40, .59)
AI_ACCENT = (.83, .87, .93)
USER_COLUMN_MIN = 174.0
BACKFILL_USER_GAP = 58.0


@dataclass
class Segment:
    page: int
    clip: fitz.Rect
    output_page: int = -1
    output_y: float = 0.0
    role: str = 'assistant'
    turn_start: bool = False


@dataclass
class MessageAnchor:
    page: int
    y: float
    date: str
    user: bool


def _font_safe(s: str) -> str:
    return s.replace('\ufffd', '').strip()


def source_title(path: str | Path) -> str:
    name = Path(path).stem
    name = re.sub(r'(?i)^chatgpt[-_ ]*', '', name)
    name = re.sub(r'[-_ ]*\d{8}[-_]\d{4,6}$', '', name).strip(' -_')
    return name or 'Conversation'


def overview(pdf: fitz.Document) -> tuple[int, list[dict[str, Any]]]:
    found: list[dict[str, Any]] = []
    for i, page in enumerate(pdf):
        for block in page.get_text('blocks'):
            if int(block[6]) != 0 or block[1] > 810:
                continue
            text = str(block[4])
            match = DATE.search(text)
            if not match:
                continue
            found.append({
                'page': i,
                'y': float(block[1]),
                'date': match.group(1),
                'user': float(block[0]) > 300,
            })
    if not found:
        raise ValueError('Unsupported source: ChatGPT Exporter timestamps are missing')
    first = min(item['page'] for item in found)
    messages = [item for item in found if item['page'] >= first]
    if not any(item['user'] for item in messages):
        raise ValueError('No identifiable user turns; refusing to invent dialogue')
    return first, messages


def _blocks(page: fitz.Page):
    result = []
    for block in page.get_text('dict')['blocks']:
        x0, y0, x1, y1 = block['bbox']
        if y0 >= 810 or y1 <= 47:
            continue
        if 'lines' in block:
            txt = ''.join(span['text'] for line in block['lines'] for span in line['spans'])
            if not txt.strip() or 'Powered by ChatGPT Exporter' in txt:
                continue
            result.append((max(0.0, y0 - 4), min(808.0, y1 + 5)))
        elif 'image' in block:
            result.append((max(0.0, y0 - 8), min(808.0, y1 + 8)))
    return sorted(result)


def segments(pdf: fitz.Document, first: int) -> list[Segment]:
    parts: list[Segment] = []
    for i in range(first, len(pdf)):
        intervals = _blocks(pdf[i])
        clusters: list[tuple[float, float]] = []
        for a, b in intervals:
            if clusters and a - clusters[-1][1] < 19:
                clusters[-1] = (clusters[-1][0], max(b, clusters[-1][1]))
            else:
                clusters.append((a, b))
        for a, b in clusters:
            a = max(46.0, a - 1)
            b = min(809.0, b + 1)
            if b - a < 2:
                continue
            parts.append(Segment(i, fitz.Rect(CONTENT_LEFT, a, CONTENT_RIGHT, b)))
    if not parts:
        raise ValueError('Source contained no readable conversation regions')
    return parts


def _words(content: str) -> Counter[str]:
    s = unicodedata.normalize('NFKC', content).lower()
    return Counter(re.findall(r'[\w]+', s))


def _image_hashes(doc: fitz.Document, first: int, last: int) -> list[str]:
    items = []
    for i in range(first, last):
        for ref in doc[i].get_images(full=True):
            pic = doc.extract_image(ref[0])['image']
            items.append(sha256(pic).hexdigest())
    # A single source page is referenced by several clipped vector XObjects.
    # PDF image resources are then listed repeatedly even when rendered once.
    # Compare distinct native image streams, not resource-reference counts.
    return sorted(set(items))


def _source_tokens(doc: fitz.Document, parts: list[Segment]) -> Counter[str]:
    text = []
    for part in parts:
        text.append(doc[part.page].get_text(clip=part.clip))
    return _words(' '.join(text))


def _uri_counter_from_parts(doc: fitz.Document, parts: list[Segment]) -> Counter[str]:
    counts: Counter[str] = Counter()
    by_page: dict[int, list[Segment]] = {}
    for part in parts:
        by_page.setdefault(part.page, []).append(part)
    for page_no, page_parts in by_page.items():
        for link in doc[page_no].get_links():
            if link.get('kind') != fitz.LINK_URI or not link.get('uri'):
                continue
            rect = fitz.Rect(link['from'])
            if any(not (rect & part.clip).is_empty for part in page_parts):
                counts[str(link['uri'])] += 1
    return counts


def _uri_counter_output(doc: fitz.Document, reading_pages: int) -> Counter[str]:
    counts: Counter[str] = Counter()
    for i in range(reading_pages):
        for link in doc[i].get_links():
            if link.get('kind') == fitz.LINK_URI and link.get('uri'):
                counts[str(link['uri'])] += 1
    return counts


def _clip_blocks(page: fitz.Page, clip: fitz.Rect) -> list[tuple]:
    return [b for b in page.get_text('blocks', clip=clip)
            if int(b[6]) == 0 and str(b[4]).strip()]


def _all_blocks_right_of(page: fitz.Page, clip: fitz.Rect, minimum: float = USER_COLUMN_MIN) -> bool:
    blocks = _clip_blocks(page, clip)
    return bool(blocks) and all(float(b[0]) > minimum for b in blocks)


def _classify_roles(pdf: fitz.Document, messages: list[dict[str, Any]], parts: list[Segment]) -> dict[str, Any]:
    user_messages = [m for m in messages if m['user']]
    anchor_by_index: dict[int, dict[str, Any]] = {}
    for message in user_messages:
        for idx, part in enumerate(parts):
            if part.page == message['page'] and part.clip.y0 <= message['y'] <= part.clip.y1:
                anchor_by_index[idx] = message
                break
        else:
            raise ValueError('A user timestamp could not be matched to any reading segment')

    right_only = [_all_blocks_right_of(pdf[part.page], part.clip) for part in parts]
    user_indices = set(anchor_by_index)
    for anchor in sorted(anchor_by_index):
        pos = anchor - 1
        while pos >= 0:
            cur = parts[pos + 1]
            prev = parts[pos]
            if prev.page != cur.page:
                break
            if cur.clip.y0 - prev.clip.y1 > BACKFILL_USER_GAP:
                break
            if not right_only[pos]:
                break
            user_indices.add(pos)
            pos -= 1

    turns = []
    for idx, part in enumerate(parts):
        role = 'user' if idx in user_indices else 'assistant'
        part.role = role
        part.turn_start = idx == 0 or parts[idx - 1].role != role
        if part.turn_start:
            turns.append((idx, role))

    if sum(role == 'user' for _, role in turns) != len(user_messages):
        raise ValueError('User-turn detection drifted from timestamp count')

    return {
        'user_message_count': len(user_messages),
        'turns': turns,
        'anchor_by_index': anchor_by_index,
    }


def _draw_page_chrome(page: fitz.Page, title: str):
    page.draw_rect(fitz.Rect(0, 0, PAGE_WIDTH, 48), color=None, fill=(.978, .982, .989))
    page.draw_line((READ_LEFT, 47), (READ_RIGHT, 47), color=PAGE_RULE, width=.6)
    page.insert_text((READ_LEFT, 29), _font_safe(title)[:78], fontsize=10,
                     fontname='hebo', color=TEXT_DARK)


def _draw_footer(page: fitz.Page, number: int, total: int):
    page.draw_line((READ_LEFT, PAGE_HEIGHT - 37), (READ_RIGHT, PAGE_HEIGHT - 37),
                   color=PAGE_RULE, width=.6)
    page.insert_text((READ_LEFT, PAGE_HEIGHT - 22),
                     'Chat transcript  ·  Original formatting retained',
                     fontsize=7.5, fontname='helv', color=TEXT_MID)
    page.insert_text((READ_RIGHT - 42, PAGE_HEIGHT - 22), f'{number} / {total}',
                     fontsize=8, fontname='helv', color=TEXT_MID)


def _draw_turn_header(page: fitz.Page, role: str, y: float, *, continued: bool = False) -> float:
    suffix = '  ·  continued' if continued else ''
    if role == 'user':
        page.insert_text((READ_RIGHT - 1, y + 11), f'YOU{suffix}',
                         fontsize=8.5, fontname='hebo', color=USER_ACCENT)
    else:
        page.draw_circle((READ_LEFT + 10, y + 7), 9, fill=(.88, .91, .97), color=None)
        page.insert_text((READ_LEFT + 5.7, y + 10.8), 'AI', fontsize=7,
                         fontname='hebo', color=(.25, .34, .51))
        page.insert_text((READ_LEFT + 27, y + 10.8), f'ChatGPT{suffix}', fontsize=9,
                         fontname='hebo', color=TEXT_DARK)
    return y + 24


def _copy_links(source_page: fitz.Page, clip: fitz.Rect, target_page: fitz.Page,
                destination: fitz.Rect, scale: float):
    for link in source_page.get_links():
        if link.get('kind') != fitz.LINK_URI or not link.get('uri'):
            continue
        rect = fitz.Rect(link['from'])
        overlap = rect & clip
        if overlap.is_empty or overlap.get_area() < 1:
            continue
        mapped = fitz.Rect(
            destination.x0 + (overlap.x0 - clip.x0) * scale,
            destination.y0 + (overlap.y0 - clip.y0) * scale,
            destination.x0 + (overlap.x1 - clip.x0) * scale,
            destination.y0 + (overlap.y1 - clip.y0) * scale,
        )
        target_page.insert_link({'kind': fitz.LINK_URI, 'from': mapped, 'uri': str(link['uri'])})


def audit(source: str | Path, result: str | Path, *, reading_pages: int | None = None,
          include_original: bool = False) -> dict[str, Any]:
    with fitz.open(str(source)) as original, fitz.open(str(result)) as output:
        first, messages = overview(original)
        parts = segments(original, first)
        if reading_pages is None:
            reading_pages = len(output) - (len(original) if include_original else 0)
        if not 0 < reading_pages <= len(output):
            raise ValueError('Invalid reading-page count')
        want = _source_tokens(original, parts)
        got = _words(' '.join(output[i].get_text() for i in range(reading_pages)))
        missing = sum((want - got).values())
        images_original = _image_hashes(original, first, len(original))
        images_reading = _image_hashes(output, 0, reading_pages)
        wanted_links = _uri_counter_from_parts(original, parts)
        output_links = _uri_counter_output(output, reading_pages)
        issues = []
        if missing:
            issues.append(f'{missing} extracted source tokens absent from reformatted pages')
        if images_original != images_reading:
            issues.append('Source embedded image bytes differ from reading edition')
        if wanted_links != output_links:
            issues.append('External citation links differ from the source reading region')
        toc = output.get_toc()
        users = sum(row['user'] for row in messages)
        if len(toc) < users:
            issues.append(f'Only {len(toc)} bookmarks for {users} user turns')
        if include_original:
            if len(output) != reading_pages + len(original):
                issues.append('Incomplete original-source appendix')
            else:
                for idx, page in enumerate(original):
                    appended = output[reading_pages + idx]
                    if page.get_text() != appended.get_text():
                        issues.append(f'Source text differs at appendix page {idx+1}')
                        break
        return {
            'passed': not issues,
            'issues': issues,
            'source_pages': len(original),
            'reading_pages': reading_pages,
            'user_turns': users,
            'bookmarks': len(toc),
            'source_tokens': sum(want.values()),
            'output_tokens': sum(got.values()),
            'missing_tokens': missing,
            'images': len(images_original),
            'image_hashes_identical': images_original == images_reading,
            'source_external_links': sum(wanted_links.values()),
            'output_external_links': sum(output_links.values()),
            'external_links_identical': wanted_links == output_links,
            'original_source_appended': include_original,
        }


def extract_title_context(source: str | Path) -> dict[str, Any]:
    with fitz.open(str(source)) as pdf:
        first, entries = overview(pdf)
        user = []
        for item in entries:
            if not item['user']:
                continue
            page = pdf[item['page']]
            candidates = [b for b in page.get_text('blocks')
                          if int(b[6]) == 0 and b[0] > 175 and
                          b[1] < item['y'] and b[1] > max(40, item['y'] - 440)]
            if candidates:
                text = ' '.join(str(max(candidates, key=lambda b: b[3])[4]).split())
                if text and text not in user:
                    user.append(text[:320])
        return {
            'source': str(source),
            'suggested_title': source_title(source),
            'user_turns': sum(row['user'] for row in entries),
            'first_user_messages': user[:3],
            'last_user_messages': user[-5:],
            'conversation_starts_on_page': first + 1,
            'note': 'Norm may suggest a more descriptive title from the user turns',
        }


def create(source: str | Path, output: str | Path, *, title: str = '',
           include_original: bool = False) -> dict[str, Any]:
    src = Path(source).resolve()
    dst = Path(output).resolve()
    if src == dst or dst.exists():
        raise FileExistsError('Refusing to overwrite a source or existing PDF')
    if not src.is_file():
        raise FileNotFoundError(src)
    if src.suffix.lower() != '.pdf' or dst.suffix.lower() != '.pdf':
        raise ValueError('PDF input/output paths required')

    name = title.strip() or source_title(src)
    with fitz.open(str(src)) as original:
        first, messages = overview(original)
        regions = segments(original, first)
        role_plan = _classify_roles(original, messages, regions)
        output_pdf = fitz.open()
        try:
            page: fitz.Page | None = None
            y = TOP
            mapped: dict[int, list[Segment]] = {}
            page_used = False

            def new_page(continued_role: str | None = None):
                nonlocal page, y, page_used
                page = output_pdf.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
                _draw_page_chrome(page, name)
                y = TOP
                page_used = False
                if continued_role:
                    y = _draw_turn_header(page, continued_role, y, continued=True)
                    page_used = True
                    y += 8

            new_page()
            base_scale = (READ_RIGHT - READ_LEFT) / (CONTENT_RIGHT - CONTENT_LEFT)
            for idx, region in enumerate(regions):
                starts_turn = region.turn_start
                role = region.role
                fit_scale = min(base_scale, (PAGE_HEIGHT - TOP - BOTTOM - 38) / region.clip.height)
                h = region.clip.height * fit_scale
                gap = ROLE_GAP if starts_turn and idx > 0 else (4.0 if page_used else 0.0)
                header_space = 24.0 if starts_turn else 0.0
                need = gap + header_space + h
                if y + need > PAGE_HEIGHT - BOTTOM:
                    new_page(None if starts_turn else role)
                    gap = 3.0 if not starts_turn else 0.0
                    header_space = 24.0 if starts_turn else 0.0
                if starts_turn:
                    y += gap
                    y = _draw_turn_header(page, role, y, continued=False)
                    page_used = True
                    y += 2.0
                else:
                    y += gap if gap > 0 else FRAGMENT_GAP
                destination = fitz.Rect(READ_LEFT, y,
                                        READ_LEFT + region.clip.width * fit_scale,
                                        y + h)
                page.show_pdf_page(destination, original, region.page,
                                   clip=region.clip, keep_proportion=True)
                _copy_links(original[region.page], region.clip, page, destination, fit_scale)
                if role == 'assistant':
                    page.draw_line((READ_LEFT - 9, y - 1), (READ_LEFT - 9, y + h + 2),
                                   color=AI_ACCENT, width=2)
                region.output_page = page.number
                region.output_y = y
                mapped.setdefault(region.page, []).append(region)
                y += h
                page_used = True

            reading_pages = len(output_pdf)
            for page_number, out_page in enumerate(output_pdf, start=1):
                _draw_footer(out_page, page_number, reading_pages)
            if include_original:
                output_pdf.insert_pdf(original)

            bookmarks = []
            known_dates = set()
            for message in messages:
                if not message['user']:
                    continue
                options = mapped.get(message['page'], [])
                match = next((x for x in options if x.clip.y0 <= message['y'] <= x.clip.y1), None)
                if match is None and options:
                    match = min(options, key=lambda x: abs(x.clip.y1 - message['y']))
                if match is None:
                    raise ValueError('A user message is missing its bookmark location')
                try:
                    day = datetime.strptime(message['date'], '%m/%d/%Y, %I:%M:%S %p').strftime('%B %d, %Y')
                except ValueError:
                    day = message['date'].split(',')[0]
                if day not in known_dates:
                    bookmarks.append([1, day, match.output_page + 1])
                    known_dates.add(day)
                blocks = [b for b in original[message['page']].get_text('blocks')
                          if int(b[6]) == 0 and b[0] > 175 and b[1] < message['y']
                          and b[1] > max(40, message['y'] - 440)]
                excerpt = ' '.join(str(max(blocks, key=lambda b: b[3])[4]).split()) if blocks else 'User message'
                excerpt = _font_safe(excerpt)
                label = excerpt[:72].rstrip(' ,.') + ('…' if len(excerpt) > 72 else '')
                bookmarks.append([2, label or 'User message', match.output_page + 1])
            if include_original:
                bookmarks.append([1, f'Original source PDF ({len(original)} pages)', reading_pages + 1])
            output_pdf.set_toc(bookmarks)
            output_pdf.set_metadata({
                'title': name + ' - Rich Dialogue',
                'subject': 'Source-preserving vector dialogue reflow (chat-style transcript)',
            })

            dst.parent.mkdir(parents=True, exist_ok=True)
            pending = dst.with_name(dst.stem + f'.partial-{os.getpid()}.pdf')
            if pending.exists():
                raise FileExistsError('Temporary PDF already exists; refusing to replace it')
            try:
                output_pdf.save(str(pending), garbage=3, deflate=True)
                checks = audit(src, pending, reading_pages=reading_pages,
                               include_original=include_original)
                if not checks['passed']:
                    raise ValueError('Verification rejected generated PDF: ' + '; '.join(checks['issues']))
                if dst.exists():
                    raise FileExistsError('PDF appeared while building; no replacement permitted')
                os.rename(pending, dst)
                checks.update({
                    'pdf': str(dst),
                    'source': str(src),
                    'format': 'dialogue',
                    'assistant_turns': role_plan['user_message_count'],
                    'layout': 'chat-style transcript',
                })
                return checks
            finally:
                pending.unlink(missing_ok=True)
        finally:
            output_pdf.close()


def _main():
    import argparse
    parser = argparse.ArgumentParser(description='Rich PDF dialogue formatter (native vector, chat-style reading layout)')
    parser.add_argument('source', type=Path, help='Original ChatGPT Exporter PDF')
    parser.add_argument('--output', type=Path, help='New output path; existing files are never overwritten')
    parser.add_argument('--title', default='', help='Conversation title (also used for suggested output name)')
    parser.add_argument('--include-original', action='store_true', help='Append the original export pages unchanged')
    parser.add_argument('--verify', type=Path, help='Compare a previously generated PDF to the source instead')
    options = parser.parse_args()
    if options.verify:
        result = audit(options.source, options.verify)
    else:
        target = options.output or options.source.with_name(
            (options.title.strip() or source_title(options.source)) + ' - Rich Dialogue.pdf')
        result = create(options.source, target, title=options.title,
                        include_original=options.include_original)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(_main())
