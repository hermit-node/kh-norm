"""Compact left-hand selective exporter and bottom Ask Norm panel for Qt browser.

All RPC, export and Redis operations run in QThreads; the WebEngine GUI event
loop is never blocked. This is a browser workspace, not a second Norm console.
"""
from __future__ import annotations
import importlib.util
import time
from pathlib import Path
from PySide6.QtCore import Qt,QThread,Signal
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
    QTreeWidget,QTreeWidgetItem,QComboBox,QCheckBox,QGroupBox,QPlainTextEdit,
    QMessageBox,QLineEdit)

ROOT=Path(__file__).resolve().parents[2]


def _plugin():
    path=ROOT/'plugins/norm_browser/src/main.py'
    spec=importlib.util.spec_from_file_location('norm_browser_ui_plugin',path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod

class WorkThread(QThread):
    succeeded=Signal(object)
    failed=Signal(str)
    def __init__(self,mode,**args):
        super().__init__();self.mode=mode;self.args=args
    def run(self):
        try:
            if self.mode=='preview':
                api=_plugin()
                s=api.conversation_preview(refresh=True)
                for _ in range(75):
                    if s.get('state') in ('ready','error'):break
                    time.sleep(.45)
                    s=api.conversation_preview()
                if s.get('state')!='ready':
                    raise RuntimeError('Conversation extraction: '+str(s.get('error') or s.get('state')))
                items=[]
                for start in range(0,int(s['count']),100):
                    page=api.conversation_preview(start=start,count=100)
                    items.extend(page.get('messages',[]))
                self.succeeded.emit({'status':s,'messages':items})
            elif self.mode=='export':
                self.succeeded.emit(_plugin().conversation_export(**self.args))
            elif self.mode=='ask':
                from task_submit import enqueue_browser_task
                self.succeeded.emit(enqueue_browser_task(**self.args))
        except Exception as exc:self.failed.emit(str(exc))

class ExportPanel(QWidget):
    def __init__(self,on_close,parent=None):
        super().__init__(parent);self.on_close=on_close;self.worker=None
        self.setMinimumWidth(300);self.setMaximumWidth(470)
        layout=QVBoxLayout(self);layout.setContentsMargins(8,8,8,8);layout.setSpacing(6)
        header=QHBoxLayout();header.addWidget(QLabel('Conversation export'),1)
        close=QPushButton('✕');close.setFixedWidth(32);close.clicked.connect(on_close)
        header.addWidget(close);layout.addLayout(header)
        self.notice=QLabel('Select turns and export.');self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        refresh=QPushButton('Load conversation');refresh.clicked.connect(self.refresh);layout.addWidget(refresh)
        categories=QHBoxLayout()
        for name,which in [('All','all'),('User','user'),('Assistant','assistant'),('None','none')]:
            b=QPushButton(name);b.clicked.connect(lambda _=False,w=which:self.bulk(w));categories.addWidget(b)
        layout.addLayout(categories)
        categories2=QHBoxLayout()
        for name,which in [('Has media','media'),('Has code','code'),('Invert','invert')]:
            b=QPushButton(name);b.clicked.connect(lambda _=False,w=which:self.bulk(w))
            categories2.addWidget(b)
        layout.addLayout(categories2)
        self.tree=QTreeWidget();self.tree.setHeaderLabels(['Turns','Role / preview'])
        self.tree.setRootIsDecorated(False);self.tree.setColumnWidth(0,50)
        self.tree.setAlternatingRowColors(True)
        layout.addWidget(self.tree,1)
        options=QGroupBox('Export settings');settings=QVBoxLayout(options)
        name_row=QHBoxLayout();name_row.addWidget(QLabel('Title'))
        self.title=QLineEdit();self.title.setPlaceholderText('Conversation title')
        name_row.addWidget(self.title,1);settings.addLayout(name_row)
        fmt=QHBoxLayout();fmt.addWidget(QLabel('Format'))
        self.format=QComboBox()
        self.format.addItem('ZIP · Markdown + files + JSON','zip')
        self.format.addItem('PDF · locally generated','pdf')
        self.format.addItem('Markdown + assets folder','md')
        self.format.addItem('Structured JSON','json')
        fmt.addWidget(self.format,1);settings.addLayout(fmt)
        self.timestamps=QCheckBox('Include timestamps');self.timestamps.setChecked(True)
        self.models=QCheckBox('Include model names')
        self.attachments=QCheckBox('Include images and attachments');self.attachments.setChecked(True)
        for control in [self.timestamps,self.models,self.attachments]:settings.addWidget(control)
        size=QHBoxLayout();size.addWidget(QLabel('Paper'))
        self.paper=QComboBox();self.paper.addItems(['Letter','A4']);size.addWidget(self.paper)
        size.addWidget(QLabel('Spacing'))
        self.density=QComboBox();self.density.addItems(['Comfortable','Compact']);size.addWidget(self.density)
        settings.addLayout(size);layout.addWidget(options)
        self.export_button=QPushButton('Export selected messages');self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export);layout.addWidget(self.export_button)
        self.open_folder=QPushButton('Open export folder')
        self.open_folder.setEnabled(False);self.open_folder.clicked.connect(self._open_folder)
        layout.addWidget(self.open_folder)
        self.last_export_path=None
        self.refresh_button=refresh

    def _work(self,mode,success,**kwargs):
        if self.worker is not None and self.worker.isRunning():return
        self.worker=WorkThread(mode,**kwargs)
        self.worker.succeeded.connect(success)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(lambda: self.refresh_button.setEnabled(True))
        self.worker.start()

    def _failed(self,message):
        self.notice.setText('Export error: '+message)
        self.export_button.setEnabled(bool(self.tree.topLevelItemCount()))

    def refresh(self):
        self.notice.setText('Retrieving conversation (API first; DOM fallback)...')
        self.refresh_button.setEnabled(False);self.export_button.setEnabled(False)
        self._work('preview',self._loaded)

    def _loaded(self,result):
        status=result['status'];self.tree.clear()
        for item in result['messages']:
            row=QTreeWidgetItem([str(item['index']+1),
                f"{item['role']} · {str(item.get('preview') or '').replace(chr(10),' ')[:130]}"])
            row.setFlags(row.flags()|Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(0,Qt.CheckState.Checked)
            row.setData(0,Qt.ItemDataRole.UserRole,int(item['index']))
            row.setData(1,Qt.ItemDataRole.UserRole,{'media':bool(item.get('attachment_count')),
                'code':bool(item.get('has_code'))})
            self.tree.addTopLevelItem(row)
        self.title.setText(str(status.get('title') or 'ChatGPT'))
        source=status.get('source','?')
        completeness='complete selected branch' if status.get('complete') else 'PARTIAL — not verified'
        self.notice.setText(f"{len(result['messages'])} turns · {source.upper()} · {completeness}"+
            (f"\nAPI fallback: {status['error']}" if status.get('error') else ''))
        self.export_button.setEnabled(bool(result['messages']))

    def bulk(self,which):
        for i in range(self.tree.topLevelItemCount()):
            row=self.tree.topLevelItem(i)
            role=row.text(1).split(' ·',1)[0].lower()
            category=row.data(1,Qt.ItemDataRole.UserRole) or {}
            if which=='invert':
                checked=row.checkState(0)!=Qt.CheckState.Checked
            else:checked=which=='all' or which==role or bool(category.get(which))
            row.setCheckState(0,Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def export(self):
        selected=[self.tree.topLevelItem(i).data(0,Qt.ItemDataRole.UserRole)
                  for i in range(self.tree.topLevelItemCount())
                  if self.tree.topLevelItem(i).checkState(0)==Qt.CheckState.Checked]
        if not selected:
            self.notice.setText('Select at least one message to export.');return
        self.export_button.setEnabled(False)
        self.notice.setText(f'Exporting {len(selected)} selected turns locally...')
        self._work('export',self._exported,selected_indices=selected,
                   fmt=self.format.currentData(),title=self.title.text().strip(),timestamps=self.timestamps.isChecked(),
                   models=self.models.isChecked(),attachments=self.attachments.isChecked(),
                   page_size=self.paper.currentText(),density=self.density.currentText().lower())

    def _exported(self,result):
        self.notice.setText('Export saved: '+str(result.get('path'))+
           '\nMessages: '+str(result.get('message_count'))+
           ' · Missing attachments: '+str(result.get('missing_assets')))
        self.last_export_path=result.get('path')
        self.open_folder.setEnabled(bool(self.last_export_path))
        self.export_button.setEnabled(True)

    def _open_folder(self):
        if self.last_export_path:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.last_export_path).parent)))

class AskNormPanel(QWidget):
    def __init__(self,browser,parent=None):
        super().__init__(parent);self.browser=browser;self.worker=None
        lay=QVBoxLayout(self);lay.setContentsMargins(8,6,8,8);lay.setSpacing(4)
        header=QHBoxLayout();header.addWidget(QLabel('Ask Norm · use this page'),1)
        hide=QPushButton('Collapse');hide.clicked.connect(lambda:self.setVisible(False))
        header.addWidget(hide);lay.addLayout(header)
        self.editor=QPlainTextEdit();self.editor.setPlaceholderText(
            'e.g., Export this entire conversation and include the images...')
        self.editor.setMaximumHeight(86);lay.addWidget(self.editor)
        bottom=QHBoxLayout();self.note=QLabel('Tasks use the existing N1/N2 queue')
        bottom.addWidget(self.note,1)
        self.send=QPushButton('Send to Norm');self.send.clicked.connect(self.submit)
        bottom.addWidget(self.send);lay.addLayout(bottom)
    def submit(self):
        text=self.editor.toPlainText().strip()
        if not text:return
        self.send.setEnabled(False);self.note.setText('Submitting browser task...')
        self.worker=WorkThread('ask',text=text,page_url=self.browser.page.url().toString(),
                               page_title=self.browser.page.title())
        self.worker.succeeded.connect(self._done)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(lambda:self.send.setEnabled(True))
        self.worker.start()
    def _done(self,result):
        self.note.setText('Queued for Norm · '+result['prompt_id'][:8])
        self.editor.clear()
    def _failed(self,error):self.note.setText('Not queued: '+error)
