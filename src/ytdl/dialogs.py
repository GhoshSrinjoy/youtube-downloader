"""Settings and batch-add dialogs."""

from __future__ import annotations

import dataclasses

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from . import core
from .settings import Prefs, default_output_dir

THEMES = {'system': 'Match system', 'dark': 'Dark', 'light': 'Light'}


def _combo(choices: dict[str, str], current: str) -> QComboBox:
    combo = QComboBox()
    for key, label in choices.items():
        combo.addItem(label, key)
    combo.setCurrentIndex(max(0, combo.findData(current)))
    return combo


def _hint(text: str) -> QLabel:
    label = QLabel(text, objectName='hint')
    label.setWordWrap(True)
    return label


def _with_hint(widget: QWidget, text: str) -> QWidget:
    box = QWidget(objectName='transparent')
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    layout.addWidget(widget)
    layout.addWidget(_hint(text))
    return box


def _page(*rows) -> QWidget:
    """A tab page holding one card with a two-column form. Rows are (label, widget) or a widget."""
    page = QWidget(objectName='transparent')
    card = QFrame(objectName='formCard')
    form = QFormLayout(card)
    form.setContentsMargins(18, 16, 18, 16)
    form.setHorizontalSpacing(18)
    form.setVerticalSpacing(12)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    for row in rows:
        if isinstance(row, tuple):
            form.addRow(row[0], row[1])
        else:
            form.addRow('', row)
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 10, 0, 0)
    layout.addWidget(card)
    layout.addStretch()
    return page


class SettingsDialog(QDialog):
    def __init__(self, options: core.Options, prefs: Prefs, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Settings')
        self.setMinimumWidth(600)
        self._options = options
        self._prefs = prefs

        # General
        self.theme = _combo(THEMES, prefs.theme)
        self.folder = QLineEdit(options.output_dir)
        browse = QPushButton('Browse…')
        browse.clicked.connect(self._browse)
        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(browse)
        folder = QWidget(objectName='transparent')
        folder.setLayout(folder_row)
        folder_row.setContentsMargins(0, 0, 0, 0)
        self.filename = _combo({k: v[0] for k, v in core.FILENAME_TEMPLATES.items()}, options.filename)
        self.playlist_folder = QCheckBox('Put playlists in their own numbered folder')
        self.auto_start = QCheckBox('Start downloads as soon as they are added')
        self.check_updates = QCheckBox('Check for new versions on startup')

        # Downloads
        self.parallel = QSpinBox(minimum=1, maximum=8)
        self.fragments = QSpinBox(minimum=1, maximum=16)
        self.rate_limit = QDoubleSpinBox(minimum=0, maximum=1000, decimals=1, singleStep=0.5, suffix=' MB/s')
        self.rate_limit.setSpecialValueText('Unlimited')
        self.codec = _combo(core.CODECS, options.codec)
        self.video_only = QCheckBox('Download video without the audio track')

        # Extras
        self.subtitles = QCheckBox('Download subtitles')
        self.sub_langs = QLineEdit()
        self.sub_langs.setPlaceholderText('en')
        self.auto_subs = QCheckBox('Include auto-generated subtitles')
        self.embed_subs = QCheckBox('Embed into the video (otherwise saved as .srt files)')
        self.subtitles.toggled.connect(self._sync)
        self.embed_thumbnail = QCheckBox('Embed the thumbnail as cover art')
        self.embed_metadata = QCheckBox('Embed title, chapters and other metadata')
        self.save_thumbnail = QCheckBox('Thumbnail image')
        self.save_description = QCheckBox('Description (.description)')
        self.save_info_json = QCheckBox('Video info (.info.json)')

        # Network
        self.cookies = _combo(core.BROWSERS, options.cookies_browser)
        self.proxy = QLineEdit()
        self.proxy.setPlaceholderText('e.g. socks5://127.0.0.1:1080')

        tabs = QTabWidget()
        tabs.addTab(_page(
            ('Theme', self.theme),
            ('Save to', folder),
            ('File names', self.filename),
            self.playlist_folder, self.auto_start, self.check_updates,
        ), 'General')
        tabs.addTab(_page(
            ('Parallel downloads', _with_hint(self.parallel, 'How many items download at the same time.')),
            ('Connections per item', _with_hint(self.fragments, 'Fetches pieces of streamed formats in parallel. 4–8 is plenty.')),
            ('Speed limit', _with_hint(self.rate_limit, 'Per download. Leave at Unlimited for full speed.')),
            ('Video codec', _with_hint(self.codec, 'H.264 plays everywhere but YouTube only offers it up to 1080p. '
                                                   'Auto picks the best quality, often AV1 or VP9.')),
            self.video_only,
        ), 'Downloads')
        tabs.addTab(_page(
            ('Subtitles', self.subtitles),
            ('Languages', _with_hint(self.sub_langs, 'Comma separated, e.g. "en, de". "en.*" matches every English variant.')),
            self.auto_subs, self.embed_subs,
            ('Embedding', self.embed_thumbnail), self.embed_metadata,
            ('Also save', self.save_thumbnail), self.save_description, self.save_info_json,
        ), 'Extras')
        tabs.addTab(_page(
            ('Browser cookies', _with_hint(self.cookies, 'Needed for age-restricted or members-only videos and when YouTube asks '
                                                         'you to sign in. Firefox works best; Chrome and Edge lock their cookies while open.')),
            ('Proxy', self.proxy),
        ), 'Network')

        reset = QPushButton('Restore defaults')
        reset.setProperty('variant', 'ghost')
        reset.clicked.connect(lambda: self._load(core.Options(output_dir=default_output_dir()), Prefs()))
        cancel = QPushButton('Cancel')
        cancel.clicked.connect(self.reject)
        save = QPushButton('Save')
        save.setProperty('variant', 'primary')
        save.setDefault(True)
        save.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addWidget(reset)
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(save)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)
        layout.addWidget(QLabel('Settings', objectName='dialogTitle'))
        layout.addWidget(tabs)
        layout.addLayout(buttons)
        self._load(options, prefs)

    def _load(self, o: core.Options, p: Prefs):
        self.theme.setCurrentIndex(max(0, self.theme.findData(p.theme)))
        self.folder.setText(o.output_dir)
        self.filename.setCurrentIndex(max(0, self.filename.findData(o.filename)))
        self.playlist_folder.setChecked(o.playlist_folder)
        self.auto_start.setChecked(p.auto_start)
        self.check_updates.setChecked(p.check_updates)
        self.parallel.setValue(p.parallel)
        self.fragments.setValue(o.fragments)
        self.rate_limit.setValue(o.rate_limit)
        self.codec.setCurrentIndex(max(0, self.codec.findData(o.codec)))
        self.video_only.setChecked(o.video_only)
        self.subtitles.setChecked(o.subtitles)
        self.sub_langs.setText(o.subtitle_langs)
        self.auto_subs.setChecked(o.auto_subtitles)
        self.embed_subs.setChecked(o.embed_subtitles)
        self.embed_thumbnail.setChecked(o.embed_thumbnail)
        self.embed_metadata.setChecked(o.embed_metadata)
        self.save_thumbnail.setChecked(o.save_thumbnail)
        self.save_description.setChecked(o.save_description)
        self.save_info_json.setChecked(o.save_info_json)
        self.cookies.setCurrentIndex(max(0, self.cookies.findData(o.cookies_browser)))
        self.proxy.setText(o.proxy)
        self._sync()

    def _sync(self):
        for widget in (self.sub_langs, self.auto_subs, self.embed_subs):
            widget.setEnabled(self.subtitles.isChecked())

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, 'Save downloads to', self.folder.text())
        if path:
            self.folder.setText(path)

    def values(self) -> tuple[core.Options, Prefs]:
        options = dataclasses.replace(
            self._options,
            output_dir=self.folder.text().strip() or default_output_dir(),
            filename=self.filename.currentData(),
            playlist_folder=self.playlist_folder.isChecked(),
            fragments=self.fragments.value(),
            rate_limit=self.rate_limit.value(),
            codec=self.codec.currentData(),
            video_only=self.video_only.isChecked(),
            subtitles=self.subtitles.isChecked(),
            subtitle_langs=self.sub_langs.text().strip() or 'en',
            auto_subtitles=self.auto_subs.isChecked(),
            embed_subtitles=self.embed_subs.isChecked(),
            embed_thumbnail=self.embed_thumbnail.isChecked(),
            embed_metadata=self.embed_metadata.isChecked(),
            save_thumbnail=self.save_thumbnail.isChecked(),
            save_description=self.save_description.isChecked(),
            save_info_json=self.save_info_json.isChecked(),
            cookies_browser=self.cookies.currentData(),
            proxy=self.proxy.text().strip(),
        )
        prefs = dataclasses.replace(
            self._prefs,
            theme=self.theme.currentData(),
            parallel=self.parallel.value(),
            auto_start=self.auto_start.isChecked(),
            check_updates=self.check_updates.isChecked(),
        )
        return options, prefs


class BatchDialog(QDialog):
    """Paste or import many links at once."""

    def __init__(self, text: str = '', parent=None):
        super().__init__(parent)
        self.setWindowTitle('Add many links')
        self.resize(620, 440)
        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText(
            'https://www.youtube.com/watch?v=…\nhttps://www.youtube.com/playlist?list=…\nhttps://vimeo.com/…')
        self.edit.textChanged.connect(self._count)
        self.count = QLabel(objectName='hint')
        load = QPushButton('Import from file…')
        load.clicked.connect(self._import)
        cancel = QPushButton('Cancel')
        cancel.clicked.connect(self.reject)
        self.add = QPushButton()
        self.add.setProperty('variant', 'primary')
        self.add.setDefault(True)
        self.add.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addWidget(load)
        buttons.addWidget(self.count)
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(self.add)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)
        layout.addWidget(QLabel('Add many links', objectName='dialogTitle'))
        layout.addWidget(_hint('Paste as many links as you like, in any format: one per line, separated by spaces, '
                               'or straight out of a document. Playlists and channels expand into their videos.'))
        layout.addWidget(self.edit, 1)
        layout.addLayout(buttons)
        self.edit.setPlainText(text)
        self._count()

    def urls(self) -> list[str]:
        return core.find_urls(self.edit.toPlainText())

    def _count(self):
        n = len(self.urls())
        self.count.setText(f'{n} link{"s" * (n != 1)} found' if n else '')
        self.add.setText(f'Add {n} link{"s" * (n != 1)}' if n else 'Add links')
        self.add.setEnabled(bool(n))

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Import links', '', 'Text files (*.txt *.csv *.md);;All files (*)')
        if path:
            with open(path, encoding='utf-8', errors='replace') as f:
                text = f.read()
            current = self.edit.toPlainText().rstrip()
            self.edit.setPlainText(f'{current}\n{text}' if current else text)
