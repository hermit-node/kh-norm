"""Offline tests: real native-vector PDF + direct slash path; no services/network."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf as fitz
from PIL import Image as PillowImage

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
sys.path.insert(0,str(ROOT/'core'))
sys.path.insert(0,str(ROOT/'tools/shared_browser'))

from norm_pdf_commands import _parts, _action, run_pdf_command
from norm_runtime.plugin_identity import verify_identity
from norm_plugin_help import get_help
import rich_dialogue_pdf as formatter

spec=importlib.util.spec_from_file_location('shared_pdf_r5', ROOT/'plugins/shared_browser/src/main.py')
shared=importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)


class PDFIntegration(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='norm-r5-pdf-')
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.src=self.base/'Exporter original.pdf'
        self._make_pdf(self.src)
        self.messages=[]

    def _make_pdf(self,where):
        doc=fitz.open()
        image=PillowImage.new('RGB',(48,80),'#8dc5fb')
        image_file=self.base/'image.png'
        image.save(image_file)
        for n in range(4):
            page=doc.new_page(width=595,height=842)
            page.draw_rect(fitz.Rect(202,68,568,160),color=None,fill=(.9,.95,1))
            page.insert_text((210,103),f'User question {n} about structured programming?',fontsize=12)
            page.insert_text((425,175),f'10/{10+n}/2026, 9:16:2{n} AM',fontsize=9)
            page.insert_text((36,235),'Assistant natural-language response.',fontsize=12)
            page.insert_text((36,264),'Bold section heading',fontname='hebo',fontsize=15)
            page.draw_rect(fitz.Rect(45,300,555,386),fill=(.95,.96,.97),color=None)
            page.insert_text((60,336),'if current_vector == canonical:',fontname='cour',fontsize=10)
            page.insert_text((60,359),'    return preserved_image',fontname='cour',fontsize=10)
            page.insert_text((37,460),'1. First preserve layout',fontsize=12)
            page.insert_text((37,486),'2. Then verify source evidence',fontsize=12)
            if n==2:page.insert_image(fitz.Rect(40,525,88,605),filename=str(image_file))
            page.insert_text((196,826),'Powered by ChatGPT Exporter',fontsize=9)
        doc.save(where)
        doc.close()

    def test_slug_and_quick_help(self):
        assert _parts(r'/pdf-chat "D:\Code Projects\conversation.pdf" --name "My Title"')==[
            '/pdf-chat',r'D:\Code Projects\conversation.pdf','--name','My Title']
        self.assertTrue(run_pdf_command('/pdf',ROOT,self.messages.append))
        self.assertIn('/pdf-chat',self.messages[-1])
        self.assertFalse(run_pdf_command('/pdf-unknown',ROOT,self.messages.append))
        self.assertIn('/pdf-chat',get_help(ROOT,'shared-browser'))

    def test_native_preservation_and_source_fidelity(self):
        out=self.base/'My technical dialogue.pdf'
        state=formatter.create(self.src,out,title='Technical Dialogue')
        self.assertTrue(state['passed'])
        self.assertEqual(state['missing_tokens'],0)
        self.assertTrue(state['image_hashes_identical'])
        self.assertEqual(state['images'],1)
        self.assertEqual(state['bookmarks'],8)
        self.assertLess(state['reading_pages'],4)
        self.assertEqual(formatter.audit(self.src,out)['missing_tokens'],0)
        with fitz.open(out) as pdf:
            words=' '.join(p.get_text() for p in pdf)
            self.assertIn('canonical',words)
            self.assertIn('Bold section heading',words)
            self.assertEqual(len(pdf.get_toc()),8)
        with self.assertRaises(FileExistsError):formatter.create(self.src,out)

    def test_original_appendix(self):
        result=formatter.create(self.src,self.base/'appendix.pdf',include_original=True)
        self.assertTrue(result['passed'])
        with fitz.open(str(self.base/'appendix.pdf')) as combined:
            self.assertEqual(len(combined),result['reading_pages']+4)
            self.assertEqual(len(combined.get_toc()),9)

    def test_source_plugin_export_and_recheck(self):
        target=self.base/'Technical - Rich Dialogue.pdf'
        with patch.object(shared,'_pdf_file',side_effect=lambda name,mode: Path(name)), \
             patch.object(shared,'_pdf_engine',return_value=formatter):
            self.assertGreater(shared.browser_shared_pdf_title_context(str(self.src))['user_turns'],0)
            result=shared.browser_shared_pdf_export('dialogue',source=str(self.src),name='Technical',output=str(target))
            self.assertTrue(result['passed'])
            self.assertEqual(result['title'],'Technical')
            self.assertTrue(target.with_suffix('.verification.json').is_file())
            check=shared.browser_shared_pdf_verify(str(self.src),str(target))
            self.assertTrue(check['passed'])
            with self.assertRaises(FileExistsError):
                shared.browser_shared_pdf_export('dialogue',source=str(self.src),name='Technical',output=str(target))

    def test_direct_shortcuts_and_protected_cleanup(self):
        with patch('norm_pdf_commands._plugin',return_value=shared), \
             patch.object(shared,'_pdf_file',side_effect=lambda name,mode: Path(name)), \
             patch.object(shared,'_pdf_engine',return_value=formatter):
            target=self.base/'direct.pdf'
            _action(f'/pdf-chat "{self.src}" --output "{target}" --name "Norm chat"',ROOT,self.messages.append)
            self.assertTrue(target.is_file())
            _action(f'/pdf-verify "{self.src}" "{target}"',ROOT,self.messages.append)
            self.assertTrue(any('"passed": true' in line for line in self.messages))
            partial=self.base/'forgotten.partial-99999.pdf'
            partial.write_text('temp')
            os.utime(partial,(time.time()-2*86400,time.time()-2*86400))
            _action(f'/pdf-cleanup "{self.base}"',ROOT,self.messages.append)
            self.assertTrue(partial.exists())
            _action(f'/pdf-cleanup "{self.base}" --execute',ROOT,self.messages.append)
            self.assertFalse(partial.exists())
            self.assertTrue(self.src.exists())
            self.assertTrue(target.is_file())
            self.assertTrue(target.with_suffix('.verification.json').is_file())
        for line in ['/pdf-chat "x.pdf" --whoops','/pdf-verify "a.pdf"',
                     '/pdf-cleanup "C:\\" --bad','/pdf-text one two']:
            with self.subTest(line=line),self.assertRaises(ValueError):
                _action(line,ROOT,self.messages.append)

    def test_plugin_identity(self):
        identity=verify_identity(ROOT/'plugins/shared_browser')
        self.assertEqual(identity['version'],'0.1.3')


if __name__=='__main__':unittest.main(verbosity=2)
