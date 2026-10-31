"""The desktop app: main window, download queue and the background workers feeding it."""

from __future__ import annotations

import dataclasses
import itertools
import json
import logging
import os
import queue
import re
import ssl
import subprocess
import sys
import threading
import urllib.request
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler

from PyQt6.QtCore import QDir, QObject, QSettings, QSize, QStandardPaths, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QGuiApplication, QIcon, QImage, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QPushButton, QScrollArea, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from . import APP_NAME, APP_TAGLINE, REPO, REPO_URL, __version__, core, settings
from .dialogs import BatchDialog, SettingsDialog
from .theme import Theme, system_theme
from .widgets import Banner, EmptyState, ItemCard, Segmented, Toast, set_tone

log = logging.getLogger('ytdl')

ACTIVE = ('downloading', 'processing')
WAITING = ('fetching', 'queued', 'ready', 'paused')


def resource_path(*parts: str) -> str:
    """A file shipped with the app: next to the frozen executable, or in the repository when run from source."""
    base = getattr(sys, '_MEIPASS', None) or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, *parts)


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r'\d+', version)[:3])


@dataclass(eq=False)
class Item:
    """One row of the queue."""

    id: int
    url: str
    options: core.Options
    card: ItemCard
    state: str = 'fetching'
    task: core.Task | None = None
    download: core.Download | None = None
    fraction: float = 0.0
    speed: float | None = None
    picked: dict | None = None      # what yt-dlp chose: ext, quality label, size
    filepath: str | None = None
    error: str = ''
    removing: bool = False          # remove once the worker has stopped


class YtdlLogger:
    """Receives yt-dlp's messages for one item; warnings and errors go to the log file."""

    def __init__(self, label: str):
        self.label = label
        self.errors = []

    def debug(self, msg):
        pass

    info = debug

    def warning(self, msg):
        log.warning('%s: %s', self.label, msg)

    def error(self, msg):
        self.errors.append(msg)
        log.error('%s: %s', self.label, msg)


class WorkerPool:
    """A few daemon threads working through a queue. Unlike ThreadPoolExecutor, a slow job
    (say, a huge playlist) never keeps the app from quitting."""

    def __init__(self, workers: int, name: str):
        self._jobs = queue.SimpleQueue()
        for n in range(workers):
            threading.Thread(target=self._work, daemon=True, name=f'{name}-{n}').start()

    def submit(self, fn, *args):
        self._jobs.put((fn, args))

    def _work(self):
        while True:
            fn, args = self._jobs.get()
            try:
                fn(*args)
            except Exception:
                log.exception('Background job failed')


class Bridge(QObject):
    """Hands results from worker threads to the UI thread (cross-thread signals are queued)."""

    fetched = pyqtSignal(int, object)
    fetch_failed = pyqtSignal(int, str)
    thumbnail = pyqtSignal(int, object)
    progress = pyqtSignal(int, object)
    stage = pyqtSignal(int, str)
    picked = pyqtSignal(int, object)
    finished = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)
    stopped = pyqtSignal(int, str)
    environment = pyqtSignal(object)
    update = pyqtSignal(str, str)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.options, self.prefs = settings.load()
        self.theme = Theme(self._theme_name())
        self.items: dict[int, Item] = {}
        self._ids = itertools.count(1)
        self._filling = False
        self._was_busy = False
        self.bridge = Bridge()
        self.fetch_pool = WorkerPool(4, 'fetch')
        self.thumb_pool = WorkerPool(4, 'thumbnail')

        self._build()
        self._connect()
        self._apply_theme()
        self._fill_pickers()
        self._update_counts()
        self._restore_geometry()
        self._ticker = QTimer(self, interval=500, timeout=self._tick)
        self._ticker.start()

    # ------------------------------------------------------------------ layout

    def _build(self):
        self.setWindowTitle(f'{APP_NAME} · {APP_TAGLINE}')
        self.setMinimumSize(800, 580)
        self.setAcceptDrops(True)
        central = QWidget(objectName='central')
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(24, 18, 24, 12)
        root.setSpacing(14)

        # Header
        self.logo = QLabel()
        words = QVBoxLayout()
        words.setSpacing(0)
        words.addWidget(QLabel(APP_NAME, objectName='appTitle'))
        words.addWidget(QLabel(f'{APP_TAGLINE} · v{__version__}', objectName='appSubtitle'))
        self.theme_button = self._header_button('Switch between light and dark')
        self.settings_button = self._header_button('Settings (Ctrl+,)')
        header = QHBoxLayout()
        header.setSpacing(12)
        header.addWidget(self.logo)
        header.addLayout(words)
        header.addStretch()
        header.addWidget(self.theme_button)
        header.addWidget(self.settings_button)
        root.addLayout(header)

        self.banner = Banner()
        root.addWidget(self.banner)

        # Link input and quick options
        card = QFrame(objectName='inputCard')
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 16, 16, 14)
        box.setSpacing(12)
        self.url_input = QLineEdit(objectName='urlInput')
        self.url_input.setPlaceholderText('Paste a video, playlist or channel link…')
        self.url_input.setClearButtonEnabled(True)
        self.link_action = self.url_input.addAction(QIcon(), QLineEdit.ActionPosition.LeadingPosition)
        self.paste_action = self.url_input.addAction(QIcon(), QLineEdit.ActionPosition.TrailingPosition)
        self.paste_action.setToolTip('Paste from clipboard')
        self.batch_button = QPushButton(' Batch')
        self.batch_button.setToolTip('Add many links at once (Ctrl+B)')
        self.download_button = QPushButton(' Download')
        self.download_button.setProperty('variant', 'primary')
        self.download_button.setToolTip('Add the link to the queue (Enter). Empty field? Uses the clipboard.')
        for button in (self.batch_button, self.download_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setMinimumHeight(42)
            button.setIconSize(QSize(18, 18))
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self.url_input, 1)
        row.addWidget(self.batch_button)
        row.addWidget(self.download_button)
        box.addLayout(row)

        self.mode = Segmented({'video': ' Video', 'audio': ' Audio'})
        self.quality = QComboBox()
        self.quality.setToolTip('Quality')
        self.quality.setMinimumWidth(150)
        self.format = QComboBox()
        self.format.setToolTip('File format')
        self.format.setMinimumWidth(130)
        self.folder_button = QPushButton(objectName='folderButton')
        self.folder_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.folder_button.setIconSize(QSize(16, 16))
        quick = QHBoxLayout()
        quick.setSpacing(10)
        quick.addWidget(self.mode)
        quick.addWidget(self.quality)
        quick.addWidget(self.format)
        quick.addStretch()
        quick.addWidget(QLabel('Save to', objectName='hint'))
        quick.addWidget(self.folder_button)
        box.addLayout(quick)
        root.addWidget(card)

        # Queue header
        self.chips = {key: QLabel(objectName='chip') for key in ('active', 'waiting', 'done', 'failed')}
        for key, tone in (('active', 'accent'), ('done', 'success'), ('failed', 'danger')):
            set_tone(self.chips[key], tone)
        self.start_all = self._ghost_button(' Start all')
        self.pause_all = self._ghost_button(' Pause all')
        self.clear_done = self._ghost_button(' Clear finished')
        self.open_folder = self._ghost_button(' Open folder')
        queue_header = QHBoxLayout()
        queue_header.setSpacing(8)
        queue_header.addWidget(QLabel('Downloads', objectName='sectionTitle'))
        queue_header.addSpacing(4)
        for chip in self.chips.values():
            queue_header.addWidget(chip)
        queue_header.addStretch()
        for button in (self.start_all, self.pause_all, self.clear_done, self.open_folder):
            queue_header.addWidget(button)
        root.addLayout(queue_header)

        # Queue
        content = QWidget(objectName='queueContent')
        self.queue = QVBoxLayout(content)
        self.queue.setContentsMargins(0, 0, 8, 4)
        self.queue.setSpacing(8)
        self.queue.addStretch()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidget(content)
        self.scroll.viewport().setObjectName('queueViewport')
        self.empty = EmptyState(self.theme)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self.scroll)
        root.addWidget(self.stack, 1)

        # Footer
        self.env_label = QLabel('Loading yt-dlp…', objectName='footer')
        self.speed_label = QLabel(objectName='footerStrong')
        footer = QHBoxLayout()
        footer.addWidget(self.env_label)
        footer.addStretch()
        footer.addWidget(self.speed_label)
        root.addLayout(footer)

        self.toast = Toast(central)

    def _header_button(self, tip: str) -> QToolButton:
        button = QToolButton(objectName='headerButton')
        button.setToolTip(tip)
        button.setIconSize(QSize(18, 18))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _ghost_button(self, text: str) -> QPushButton:
        button = QPushButton(text)
        button.setProperty('variant', 'ghost')
        button.setIconSize(QSize(16, 16))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _connect(self):
        b = self.bridge
        b.fetched.connect(self._on_fetched)
        b.fetch_failed.connect(self._on_fetch_failed)
        b.thumbnail.connect(self._on_thumbnail)
        b.progress.connect(self._on_progress)
        b.stage.connect(self._on_stage)
        b.picked.connect(self._on_picked)
        b.finished.connect(self._on_finished)
        b.failed.connect(self._on_failed)
        b.stopped.connect(self._on_stopped)
        b.environment.connect(self._on_environment)
        b.update.connect(self._on_update)

        self.url_input.returnPressed.connect(self.download_clicked)
        self.paste_action.triggered.connect(lambda: self.url_input.setText(QGuiApplication.clipboard().text().strip()))
        self.download_button.clicked.connect(self.download_clicked)
        self.batch_button.clicked.connect(self.open_batch)
        self.settings_button.clicked.connect(self.open_settings)
        self.theme_button.clicked.connect(self._toggle_theme)
        self.mode.changed.connect(self._mode_changed)
        self.quality.currentIndexChanged.connect(self._picker_changed)
        self.format.currentIndexChanged.connect(self._picker_changed)
        self.folder_button.clicked.connect(self._choose_folder)
        self.start_all.clicked.connect(self._start_all)
        self.pause_all.clicked.connect(self._pause_all)
        self.clear_done.clicked.connect(self._clear_finished)
        self.open_folder.clicked.connect(lambda: self._open_path(self.options.output_dir))
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._system_theme_changed)

        shortcuts = {
            QKeySequence.StandardKey.Paste: self._paste_shortcut,
            QKeySequence('Ctrl+B'): self.open_batch,
            QKeySequence('Ctrl+,'): self.open_settings,
            QKeySequence('Ctrl+O'): lambda: self._open_path(self.options.output_dir),
            QKeySequence('Ctrl+L'): self.url_input.setFocus,
        }
        for keys, slot in shortcuts.items():
            QShortcut(keys, self, activated=slot)

    # ------------------------------------------------------------------ theme

    def _theme_name(self) -> str:
        return system_theme() if self.prefs.theme == 'system' else self.prefs.theme

    def _toggle_theme(self):
        self.prefs.theme = 'light' if self.theme.dark else 'dark'
        settings.save(self.options, self.prefs)
        self._apply_theme()

    def _system_theme_changed(self):
        if self.prefs.theme == 'system':
            self._apply_theme()

    def _apply_theme(self):
        self.theme = Theme(self._theme_name())
        t = self.theme
        t.apply(QApplication.instance())
        logo = QIcon(resource_path('assets', 'icon.png')).pixmap(QSize(40, 40), self.devicePixelRatioF())
        self.logo.setPixmap(logo)
        self.theme_button.setIcon(t.icon('sun' if t.dark else 'moon', 'muted'))
        self.settings_button.setIcon(t.icon('sliders', 'muted'))
        self.link_action.setIcon(t.icon('link', 'faint'))
        self.paste_action.setIcon(t.icon('clipboard', 'muted'))
        self.batch_button.setIcon(t.icon('list', 'text'))
        self.download_button.setIcon(t.icon('download', 'on_accent'))
        self.folder_button.setIcon(t.icon('folder', 'muted'))
        for key, name in (('video', 'video'), ('audio', 'music')):
            icon = QIcon(t.pixmap(name, 'muted', 16))
            icon.addPixmap(t.pixmap(name, 'on_accent', 16), QIcon.Mode.Normal, QIcon.State.On)
            self.mode.buttons[key].setIcon(icon)
            self.mode.buttons[key].setIconSize(QSize(16, 16))
        for button, name in ((self.start_all, 'play'), (self.pause_all, 'pause'), (self.clear_done, 'check'),
                             (self.open_folder, 'folder')):
            button.setIcon(t.icon(name, 'muted', 16))
        self.empty.set_theme(t)
        for item in self.items.values():
            item.card.set_theme(t)
        if self.banner.isVisible() and self.banner.property('tone') == 'warning':
            self.banner.icon.setPixmap(t.pixmap('alert', 'warning', 22))

    # ------------------------------------------------------------------ quick options

    def _fill_pickers(self):
        """Fill quality and format for the current mode (video resolutions or audio bitrates)."""
        o = self.options
        audio = o.mode == 'audio'
        self._filling = True
        self.mode.set_value(o.mode)
        for combo, choices, current in (
            (self.quality, core.AUDIO_QUALITIES if audio else core.VIDEO_QUALITIES, o.audio_quality if audio else o.quality),
            (self.format, core.AUDIO_FORMATS if audio else core.VIDEO_FORMATS, o.audio_format if audio else o.video_format),
        ):
            combo.clear()
            for key, label in choices.items():
                combo.addItem(label, key)
            combo.setCurrentIndex(max(0, combo.findData(current)))
        self.quality.setEnabled(not (audio and o.audio_format in core.LOSSLESS_AUDIO))
        self._filling = False
        self._show_folder()

    def _mode_changed(self, mode: str):
        if not self._filling:
            self.options.mode = mode
            self._fill_pickers()
            settings.save(self.options, self.prefs)

    def _picker_changed(self):
        if self._filling:
            return
        o = self.options
        if o.mode == 'audio':
            o.audio_quality, o.audio_format = self.quality.currentData(), self.format.currentData()
            self.quality.setEnabled(o.audio_format not in core.LOSSLESS_AUDIO)
        else:
            o.quality, o.video_format = self.quality.currentData(), self.format.currentData()
        settings.save(self.options, self.prefs)

    def _show_folder(self):
        path = QDir.toNativeSeparators(self.options.output_dir)
        text = self.folder_button.fontMetrics().elidedText(path, Qt.TextElideMode.ElideMiddle, 230)
        self.folder_button.setText(f' {text}')
        self.folder_button.setToolTip(f'Downloads are saved to {path}\nClick to change')

    def _choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, 'Save downloads to', self.options.output_dir)
        if path:
            self.options.output_dir = path
            self._show_folder()
            settings.save(self.options, self.prefs)

    # ------------------------------------------------------------------ adding links

    def download_clicked(self):
        text = self.url_input.text().strip() or QGuiApplication.clipboard().text()
        if self.add_links(text):
            self.url_input.clear()
        elif not core.find_urls(text):
            self.toast.show_message('Paste a link first (or copy one and press Download)')
            self.url_input.setFocus()

    def _paste_shortcut(self):
        if not self.add_links(QGuiApplication.clipboard().text()):
            self.toast.show_message('No link on the clipboard')

    def open_batch(self):
        clipboard = QGuiApplication.clipboard().text()
        dialog = BatchDialog(clipboard if len(core.find_urls(clipboard)) > 1 else '', self)
        if dialog.exec():
            self.add_links(dialog.urls())

    def add_links(self, links: str | list[str]) -> int:
        """Queue every link in *links* (text or list); returns how many were new."""
        urls = core.find_urls(links) if isinstance(links, str) else links
        known = {item.url for item in self.items.values() if item.state != 'failed'}
        new = [url for url in urls if url not in known]
        for url in new:
            self._add_item(url)
        if urls and not new:
            self.toast.show_message('Already in the queue')
        elif len(new) > 1:
            self.toast.show_message(f'Added {len(new)} links')
        return len(new)

    def _add_item(self, url: str, task: core.Task | None = None, index: int | None = None) -> Item:
        card = ItemCard(self.theme)
        item = Item(id=next(self._ids), url=url, options=dataclasses.replace(self.options), card=card)
        self.items[item.id] = item
        card.action.connect(lambda action, i=item.id: self._on_action(i, action))
        card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        card.customContextMenuRequested.connect(lambda pos, i=item.id: self._context_menu(i, pos))
        self.queue.insertWidget(self.queue.count() - 1 if index is None else index, card)
        self.stack.setCurrentWidget(self.scroll)
        if task is None:
            card.title.setText(url)
            card.meta.setText(item.options.describe())
            self._fetch(item)
        else:
            self._apply_task(item, task)
        if index is None:
            QTimer.singleShot(50, lambda: self.scroll.ensureWidgetVisible(card))
        self._update_counts()
        return item

    def _fetch(self, item: Item):
        self._set_state(item, 'fetching', 'Fetching details…')
        self.fetch_pool.submit(self._fetch_worker, item.id, item.url, item.options)

    def _fetch_worker(self, item_id: int, url: str, options: core.Options):
        try:
            result = core.expand(url, options, YtdlLogger(url))
        except Exception as e:
            self.bridge.fetch_failed.emit(item_id, str(e))
        else:
            self.bridge.fetched.emit(item_id, result)

    def _on_fetched(self, item_id: int, result):
        item = self.items.get(item_id)
        if item is None:
            return  # removed while fetching
        playlist, tasks = result
        if playlist is None:
            self._apply_task(item, tasks[0])
        elif not tasks:
            self._set_state(item, 'failed', 'This playlist has no videos', 'danger')
        else:
            known = {i.url for i in self.items.values() if i.state != 'failed'}
            new = [task for task in tasks if task.url not in known]
            index = self.queue.indexOf(item.card)
            self._drop(item)
            self.setUpdatesEnabled(False)
            for n, task in enumerate(new):
                self._add_item(task.url, task, index + n)
            self.setUpdatesEnabled(True)
            skipped = len(tasks) - len(new)
            self.toast.show_message(f'Added {len(new)} videos from “{playlist}”'
                                    + (f' ({skipped} already in the queue)' if skipped else ''))
        self._schedule()

    def _on_fetch_failed(self, item_id: int, message: str):
        item = self.items.get(item_id)
        if item:
            item.error = core.clean_error(message)
            self._set_state(item, 'failed', core.friendly_error(message), 'danger', item.error)
            self._update_counts()

    def _apply_task(self, item: Item, task: core.Task):
        item.task = task
        item.card.title.setText(task.title or item.url)
        item.card.set_thumbnail(badge=core.human_duration(task.meta.get('duration')))
        self._show_meta(item)
        thumbnail = core.thumbnail_url(task.meta)
        if thumbnail:
            self.thumb_pool.submit(self._thumbnail_worker, item.id, thumbnail, item.options)
        if self.prefs.auto_start:
            self._set_state(item, 'queued', 'Queued')
        else:
            self._set_state(item, 'ready', 'Ready. Press ▶ or Start all')

    def _thumbnail_worker(self, item_id: int, url: str, options: core.Options):
        try:
            self.bridge.thumbnail.emit(item_id, core.fetch_bytes(url, options))
        except Exception:
            pass  # the placeholder stays

    def _on_thumbnail(self, item_id: int, data: bytes):
        item = self.items.get(item_id)
        image = QImage.fromData(data)
        if item and not image.isNull():
            item.card.set_thumbnail(image)

    def _show_meta(self, item: Item):
        meta = item.task.meta if item.task else {}
        o = item.options
        parts = [meta.get('uploader') or meta.get('channel') or meta.get('extractor_key') or '']
        if item.picked and o.mode == 'video' and item.picked.get('quality'):
            parts.append(f"{item.picked['quality']} · {(item.picked.get('ext') or o.video_format).upper()}")
        else:
            parts.append(o.describe())
        item.card.meta.setText(' · '.join(p for p in parts if p))

    # ------------------------------------------------------------------ downloading

    def _schedule(self):
        """Start queued items while there are free slots."""
        running = sum(item.state in ACTIVE for item in self.items.values())
        for item in list(self.items.values()):
            if running >= self.prefs.parallel:
                break
            if item.state == 'queued' and item.task:
                self._start(item)
                running += 1
        self._update_counts()

    def _start(self, item: Item):
        logger = YtdlLogger(item.task.title or item.url)
        i = item.id
        item.download = core.Download(
            item.task, item.options, logger=logger,
            on_progress=lambda p: self.bridge.progress.emit(i, p),
            on_stage=lambda s: self.bridge.stage.emit(i, s),
            on_format=lambda f: self.bridge.picked.emit(i, f))
        item.speed = None
        self._set_state(item, 'downloading', 'Starting…')
        threading.Thread(target=self._download_worker, args=(i, item.download, logger), daemon=True,
                         name=f'download-{i}').start()

    def _download_worker(self, item_id: int, download: core.Download, logger: YtdlLogger):
        from yt_dlp.utils import DownloadCancelled

        try:
            path = download.run()
        except DownloadCancelled:
            self.bridge.stopped.emit(item_id, download.stop_reason or 'cancel')
        except Exception as e:
            self.bridge.failed.emit(item_id, str(e) or (logger.errors[-1] if logger.errors else 'Download failed'))
        else:
            self.bridge.finished.emit(item_id, path)

    def _on_progress(self, item_id: int, p: dict):
        item = self.items.get(item_id)
        if item is None or item.state != 'downloading' or item.removing:
            return
        item.speed = p.get('speed')
        parts = []
        if p.get('fraction') is not None:
            item.fraction = p['fraction']
            item.card.bar.set_value(item.fraction)
            parts.append(f'{item.fraction:.0%}')
        if p.get('total'):
            parts.append(f"{core.human_size(p.get('downloaded'))} of {core.human_size(p['total'])}")
        if p.get('speed'):
            parts.append(f"{core.human_size(p['speed'])}/s")
        if p.get('eta'):
            parts.append(f"{core.human_duration(p['eta'])} left")
        item.card.set_status(' · '.join(parts) or 'Downloading…')

    def _on_stage(self, item_id: int, text: str):
        item = self.items.get(item_id)
        if item and item.state in ACTIVE:
            item.speed = None
            self._set_state(item, 'processing', f'{text}…', 'accent')

    def _on_picked(self, item_id: int, picked: dict):
        item = self.items.get(item_id)
        if item:
            item.picked = picked
            self._show_meta(item)

    def _on_finished(self, item_id: int, path: str | None):
        item = self.items.get(item_id)
        if item is None:
            return
        item.filepath = path
        item.speed = None
        item.download = None
        if item.removing:
            self._drop(item)
        else:
            size = os.path.getsize(path) if path and os.path.isfile(path) else None
            text = 'Done' + (f' · {core.human_size(size)}' if size else '') + (f' · {os.path.basename(path)}' if path else '')
            self._set_state(item, 'done', text, 'success', path or '')
        self._schedule()

    def _on_failed(self, item_id: int, message: str):
        item = self.items.get(item_id)
        if item is None:
            return
        item.speed = None
        if item.task:
            item.task.info = None  # retrying should start from a fresh extraction
        if item.removing:
            self._drop(item)
        else:
            item.error = core.clean_error(message)
            self._set_state(item, 'failed', core.friendly_error(message), 'danger', item.error)
        self._schedule()

    def _on_stopped(self, item_id: int, reason: str):
        item = self.items.get(item_id)
        if item is None:
            return
        item.speed = None
        if item.removing or reason == 'cancel':
            self._drop(item)
        else:
            self._set_state(item, 'paused', f'Paused at {item.fraction:.0%}', 'warning')
        self._schedule()

    # ------------------------------------------------------------------ item actions

    def _set_state(self, item: Item, state: str, status: str = '', tone: str = '', tooltip: str = ''):
        item.state = state
        item.card.set_state(state)
        item.card.set_status(status, tone, tooltip)
        self._update_counts()

    def _on_action(self, item_id: int, action: str):
        item = self.items.get(item_id)
        if item is None:
            return
        if action == 'pause' and item.download:
            item.download.stop(pause=True)
            item.card.set_status('Pausing…')
        elif action in ('start', 'resume'):
            self._set_state(item, 'queued', 'Queued')
            self._schedule()
        elif action == 'retry':
            item.error, item.fraction = '', 0.0
            if item.task:
                self._set_state(item, 'queued', 'Queued')
                self._schedule()
            else:
                self._fetch(item)
        elif action == 'remove':
            self._remove(item)
        elif action == 'open' and item.filepath:
            QDesktopServices.openUrl(QUrl.fromLocalFile(item.filepath))
        elif action == 'folder':
            self._reveal(item.filepath or item.options.output_dir)

    def _remove(self, item: Item):
        if item.state == 'downloading' and item.download:
            item.removing = True
            item.download.stop()  # the worker reports back and the card goes away then
            item.card.set_status('Cancelling…')
            return
        if item.state == 'processing':
            return
        if item.download and item.state != 'done':
            item.download.cleanup()  # .part files of a paused or failed attempt
        self._drop(item)
        self._schedule()

    def _drop(self, item: Item):
        self.items.pop(item.id, None)
        item.card.setParent(None)
        item.card.deleteLater()
        if not self.items:
            self.stack.setCurrentWidget(self.empty)
        self._update_counts()

    def _context_menu(self, item_id: int, pos):
        item = self.items.get(item_id)
        if item is None:
            return
        menu = QMenu(self)
        menu.addAction(self.theme.icon('link', 'muted', 16), 'Copy link', lambda: QGuiApplication.clipboard().setText(item.url))
        menu.addAction(self.theme.icon('external', 'muted', 16), 'Open page in browser', lambda: QDesktopServices.openUrl(QUrl(item.url)))
        if item.filepath:
            menu.addAction(self.theme.icon('folder', 'muted', 16), 'Show in folder', lambda: self._reveal(item.filepath))
        if item.error:
            menu.addAction(self.theme.icon('alert', 'muted', 16), 'Copy error details', lambda: QGuiApplication.clipboard().setText(item.error))
        if item.state != 'processing':
            menu.addSeparator()
            menu.addAction(self.theme.icon('x', 'muted', 16), 'Remove', lambda: self._remove(item))
        menu.exec(item.card.mapToGlobal(pos))

    def _start_all(self):
        for item in self.items.values():
            if item.state in ('ready', 'paused'):
                self._set_state(item, 'queued', 'Queued')
        self._schedule()

    def _pause_all(self):
        for item in self.items.values():
            if item.state == 'downloading' and item.download:
                item.download.stop(pause=True)
                item.card.set_status('Pausing…')
            elif item.state == 'queued':
                self._set_state(item, 'paused', 'Paused', 'warning')

    def _clear_finished(self):
        for item in [i for i in self.items.values() if i.state == 'done']:
            self._drop(item)

    def _update_counts(self):
        states = [item.state for item in self.items.values()]
        counts = {
            'active': (sum(s in ACTIVE for s in states), 'downloading'),
            'waiting': (sum(s in WAITING for s in states), 'waiting'),
            'done': (states.count('done'), 'done'),
            'failed': (states.count('failed'), 'failed'),
        }
        for key, (n, label) in counts.items():
            self.chips[key].setText(f'{n} {label}')
            self.chips[key].setVisible(bool(n))
        self.start_all.setVisible(any(s in ('ready', 'paused') for s in states))
        self.pause_all.setVisible(any(s in ('downloading', 'queued') for s in states))
        self.clear_done.setVisible('done' in states)

    def _tick(self):
        """Twice a second: total speed in the footer and progress in the window title."""
        active = [item for item in self.items.values() if item.state in ACTIVE]
        speed = sum(item.speed or 0 for item in active)
        self.speed_label.setText(f'↓ {core.human_size(speed)}/s' if speed else '')
        if active:
            progress = sum(item.fraction for item in active) / len(active)
            self.setWindowTitle(f'{progress:.0%} · {len(active)} downloading · {APP_NAME}')
        else:
            self.setWindowTitle(f'{APP_NAME} · {APP_TAGLINE}')
        busy = bool(active) or any(item.state in ('queued', 'fetching') for item in self.items.values())
        if self._was_busy and not busy and any(item.state == 'done' for item in self.items.values()):
            failed = sum(item.state == 'failed' for item in self.items.values())
            self.toast.show_message('All downloads finished' + (f' ({failed} failed)' if failed else ''))
            QApplication.alert(self)
        self._was_busy = busy

    # ------------------------------------------------------------------ files

    def _open_path(self, path: str):
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _reveal(self, path: str):
        """Open the file manager with *path* selected (or just the folder, where selecting isn't supported)."""
        try:
            if os.path.isfile(path) and sys.platform == 'win32':
                subprocess.Popen(['explorer', '/select,', os.path.normpath(path)])
                return
            if os.path.isfile(path) and sys.platform == 'darwin':
                subprocess.Popen(['open', '-R', path])
                return
        except OSError:
            pass
        self._open_path(path if os.path.isdir(path) else os.path.dirname(path))

    # ------------------------------------------------------------------ settings, drops, startup checks

    def open_settings(self):
        dialog = SettingsDialog(self.options, self.prefs, self)
        if dialog.exec():
            old_theme = self.prefs.theme
            self.options, self.prefs = dialog.values()
            settings.save(self.options, self.prefs)
            self._fill_pickers()
            if self.prefs.theme != old_theme:
                self._apply_theme()
            self._schedule()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event):
        data = event.mimeData()
        texts = [data.text()] if data.hasText() else []
        for url in data.urls():
            path = url.toLocalFile()
            if path and path.lower().endswith(('.txt', '.csv', '.md')) and os.path.isfile(path):
                with open(path, encoding='utf-8', errors='replace') as f:
                    texts.append(f.read())  # a dropped list of links
            elif not path:
                texts.append(url.toString())
        if not self.add_links('\n'.join(texts)) and not core.find_urls('\n'.join(texts)):
            self.toast.show_message('No links found in what you dropped')

    def start_background_tasks(self):
        threading.Thread(target=self._environment_worker, daemon=True, name='environment').start()
        if self.prefs.check_updates:
            threading.Thread(target=self._update_worker, daemon=True, name='update-check').start()

    def _environment_worker(self):
        try:
            env = core.environment()
        except Exception as e:
            log.exception('Checking helper tools failed')
            env = {'error': str(e)}
        self.bridge.environment.emit(env)

    def _on_environment(self, env: dict):
        if env.get('error'):
            self.env_label.setText(f"yt-dlp failed to load: {env['error']}")
            return
        parts = [f"yt-dlp {env['yt-dlp']}", f"ffmpeg {env['ffmpeg']}" if env.get('ffmpeg') else 'no ffmpeg',
                 env.get('js') or 'no JavaScript runtime']
        self.env_label.setText('  ·  '.join(parts))
        if not env.get('ffmpeg'):
            self.banner.show_notice(
                self.theme, 'warning', 'alert', 'FFmpeg was not found',
                'It is needed to merge HD video with its audio and to convert to MP3. '
                'Install it (or run: pip install imageio-ffmpeg) and restart.',
                'Get FFmpeg', lambda: QDesktopServices.openUrl(QUrl('https://ffmpeg.org/download.html')))
        elif not env.get('js'):
            self.banner.show_notice(
                self.theme, 'warning', 'alert', 'No JavaScript runtime found',
                'YouTube downloads need Deno (recommended) or Node.js 22+. Without one some videos fail or offer fewer formats.',
                'Get Deno', lambda: QDesktopServices.openUrl(QUrl('https://deno.com')))

    def _update_worker(self):
        try:
            import certifi
            context = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            context = ssl.create_default_context()
        request = urllib.request.Request(
            f'https://api.github.com/repos/{REPO}/releases/latest',
            headers={'Accept': 'application/vnd.github+json', 'User-Agent': f'{APP_NAME}/{__version__}'})
        try:
            with urllib.request.urlopen(request, timeout=10, context=context) as response:
                release = json.load(response)
        except Exception:
            return  # offline, rate limited or no release yet; not worth bothering anyone
        latest = (release.get('tag_name') or '').lstrip('v')
        if latest and _version_tuple(latest) > _version_tuple(__version__):
            self.bridge.update.emit(latest, release.get('html_url') or f'{REPO_URL}/releases/latest')

    def _on_update(self, version: str, url: str):
        if self.banner.isVisible() and self.banner.property('tone') == 'warning':
            return  # a missing tool matters more
        self.banner.show_notice(
            self.theme, 'accent', 'sparkles', f'{APP_NAME} {version} is available',
            f'You have {__version__}. New versions ship the latest yt-dlp, which keeps up with YouTube changes.',
            'Download', lambda: QDesktopServices.openUrl(QUrl(url)))

    # ------------------------------------------------------------------ window state

    def _restore_geometry(self):
        geometry = QSettings().value('geometry')
        if geometry is None or not self.restoreGeometry(geometry):
            self.resize(1060, 760)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.toast.isVisible():
            self.toast.reposition()

    def closeEvent(self, event):
        running = [item for item in self.items.values() if item.state in ACTIVE]
        if running:
            answer = QMessageBox.question(
                self, f'Quit {APP_NAME}?',
                f'{len(running)} download{"s are" if len(running) > 1 else " is"} still running.\n'
                'Quit anyway? Adding the same links again later resumes where they stopped.')
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            for item in running:
                if item.download:
                    item.download.stop(pause=True)  # keep .part files so they can resume
        settings.save(self.options, self.prefs)
        QSettings().setValue('geometry', self.saveGeometry())
        event.accept()


# ---------------------------------------------------------------------- entry point

def _setup_logging() -> str:
    folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, 'ytdl.log')
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=1, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s [%(threadName)s] %(message)s'))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    return path


def _smoke_test(app: QApplication) -> int:
    """Used by the build: start the UI, check the bundled tools, write a report and quit."""
    import yt_dlp_ejs.yt.solver

    window = MainWindow()
    window.show()
    app.processEvents()
    report = {'version': __version__, **core.environment(), 'ejs': len(yt_dlp_ejs.yt.solver.core()) > 0}
    url = os.environ.get('YTDL_SMOKE_URL')  # optional real download (needs network), into a temp folder
    if url:
        import tempfile

        folder = tempfile.mkdtemp(prefix='ytdl-smoke-')
        for name, options in (('video', core.Options(quality='360', output_dir=folder)),
                              ('audio', core.Options(mode='audio', audio_quality='128', output_dir=folder))):
            try:
                path = core.Download(core.expand(url, options)[1][0], options).run()
                report[name] = f'{os.path.basename(path)} ({os.path.getsize(path)} bytes)'
            except Exception as e:
                report[name] = f'FAILED: {e}'
        report['ok'] = all('FAILED' not in report[k] for k in ('video', 'audio'))
    window.close()
    path = os.environ.get('YTDL_SMOKE_REPORT')
    if path:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(report, f)
    print(json.dumps(report))
    return 0 if report['ffmpeg'] and report['js'] and report['ejs'] and report.get('ok', True) else 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    for name in ('stdout', 'stderr'):
        if getattr(sys, name) is None:  # windowed builds have no console
            setattr(sys, name, open(os.devnull, 'w', encoding='utf-8'))
    if sys.platform == 'win32':
        try:  # own taskbar icon and grouping instead of python.exe's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f'GhoshSrinjoy.{APP_NAME}')
        except Exception:
            pass

    smoke = '--smoke-test' in argv
    app = QApplication(argv)
    app.setOrganizationName(APP_NAME)
    app.setApplicationName(f'{APP_NAME}-smoke-test' if smoke else APP_NAME)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QIcon(resource_path('assets', 'icon.png')))
    log_path = _setup_logging()

    def excepthook(kind, error, tb):
        log.error('Unexpected error', exc_info=(kind, error, tb))
        if smoke:  # never block a build on a dialog
            sys.__excepthook__(kind, error, tb)
            os._exit(1)
        QMessageBox.critical(None, APP_NAME, f'Something went wrong: {error}\n\nDetails were written to {log_path}')

    sys.excepthook = excepthook
    if smoke:
        return _smoke_test(app)

    window = MainWindow()
    window.show()
    window.start_background_tasks()
    if len(argv) > 1:
        window.add_links(' '.join(argv[1:]))  # links passed on the command line or via "Open with"
    return app.exec()
