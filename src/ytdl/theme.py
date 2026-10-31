"""Colours, stylesheet and icons for the desktop app (dark and light)."""

from __future__ import annotations

import os
import re

from PyQt6.QtCore import QByteArray, QDir, QRectF, QStandardPaths, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPalette, QPixmap
from PyQt6.QtSvg import QSvgRenderer

PALETTES = {
    'dark': {
        'bg': '#0d0f14', 'surface': '#151821', 'field': '#1b1f29', 'field_hi': '#222734',
        'border': '#262b37', 'border_hi': '#353c4c', 'text': '#e9ebf1', 'muted': '#9aa1b0',
        'faint': '#636b7b', 'accent': '#7c5cff', 'accent_hi': '#8f74ff', 'accent_lo': '#6747f0',
        'accent2': '#3ea6ff', 'on_accent': '#ffffff', 'success': '#2fd08a', 'warning': '#f4b740',
        'danger': '#ff5e6e', 'track': '#232836', 'selection': '#4a3ba8', 'toast': '#e9ebf1',
        'on_toast': '#0d0f14', 'shadow': '#000000',
    },
    'light': {
        'bg': '#f3f4f7', 'surface': '#ffffff', 'field': '#f0f2f6', 'field_hi': '#e7eaf0',
        'border': '#e0e3ea', 'border_hi': '#cbd0da', 'text': '#14171f', 'muted': '#5b6372',
        'faint': '#8b92a0', 'accent': '#6a4bf0', 'accent_hi': '#7a5ef5', 'accent_lo': '#5a3bdd',
        'accent2': '#2f8cf0', 'on_accent': '#ffffff', 'success': '#119a62', 'warning': '#b97c0b',
        'danger': '#dc3446', 'track': '#e4e7ee', 'selection': '#d8ceff', 'toast': '#14171f',
        'on_toast': '#ffffff', 'shadow': '#5b6372',
    },
}

# 24x24 stroke icons drawn for this app.
ICONS = {
    'plus': '<path d="M12 5v14M5 12h14"/>',
    'x': '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    'check': '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    'download': '<path d="M12 4v11"/><path d="M7 10.5l5 5 5-5"/><path d="M5 20h14"/>',
    'folder': '<path d="M3.5 7.5a2 2 0 0 1 2-2h3.6l2 2.2h7.4a2 2 0 0 1 2 2v7.8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
    'clipboard': '<rect x="6" y="5" width="12" height="15.5" rx="2"/><path d="M9.5 3.5h5v3h-5z"/>',
    'link': '<path d="M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1.2 1.2"/>'
            '<path d="M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1.2-1.2"/>',
    'list': '<path d="M9.5 6.5h10M9.5 12h10M9.5 17.5h10M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01"/>',
    'sliders': '<path d="M4 7h8.5M17.5 7H20M4 17h2.5M11.5 17H20"/><circle cx="15" cy="7" r="2.5"/><circle cx="9" cy="17" r="2.5"/>',
    'moon': '<path d="M19.5 14.5A7.5 7.5 0 1 1 9.5 4.5a6 6 0 0 0 10 10z"/>',
    'sun': '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4'
           'M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>',
    'pause': '<path d="M9 6v12M15 6v12"/>',
    'play': '<path d="M8.5 6.2v11.6a.8.8 0 0 0 1.2.7l9.2-5.8a.8.8 0 0 0 0-1.4L9.7 5.5a.8.8 0 0 0-1.2.7z"/>',
    'retry': '<path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3L4.5 9"/><path d="M4.5 4.5V9H9"/>',
    'trash': '<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l.8 12.2a1.5 1.5 0 0 0 1.5 1.3h6.4a1.5 1.5 0 0 0 1.5-1.3L17.5 7"/>',
    'external': '<path d="M14 4.5h5.5V10M19.5 4.5L11 13"/>'
                '<path d="M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>',
    'alert': '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.8v4.7M12 16h.01"/>',
    'video': '<rect x="3" y="6" width="12.5" height="12" rx="2.5"/><path d="M15.5 10.2l5-2.7v9l-5-2.7"/>',
    'music': '<path d="M9 17.5V6.2l10-2v11.3"/><circle cx="6.5" cy="17.5" r="2.5"/><circle cx="16.5" cy="15.5" r="2.5"/>',
    'chevron-down': '<path d="M6.5 9.5l5.5 5.5 5.5-5.5"/>',
    'chevron-up': '<path d="M6.5 14.5L12 9l5.5 5.5"/>',
    'sparkles': '<path d="M12 3.5l1.8 4.7 4.7 1.8-4.7 1.8L12 16.5l-1.8-4.7L5.5 10l4.7-1.8z"/><path d="M18.5 15.5l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z"/>',
    'inbox': '<path d="M3.5 13.5l2.6-7.1A2 2 0 0 1 8 5h8a2 2 0 0 1 1.9 1.4l2.6 7.1"/>'
             '<path d="M3.5 13.5V18a2 2 0 0 0 2 2h13a2 2 0 0 0 2-2v-4.5h-5a3.5 3.5 0 0 1-7 0z"/>',
}
_FILLED = {'play'}

_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{fill}" stroke="{color}" '
        'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')

STYLESHEET = """
* { outline: 0; }
QWidget { color: @text; font-size: 13px; }
QMainWindow, QDialog, QWidget#central, QScrollArea, QWidget#queueViewport, QStackedWidget { background: @bg; }
QWidget#queueContent, QWidget#transparent { background: transparent; }
QToolTip { color: @text; background: @surface; border: 1px solid @border_hi; border-radius: 6px; padding: 5px 8px; }

QLabel#appTitle { font-size: 19px; font-weight: 700; }
QLabel#appSubtitle, QLabel#hint, QLabel#emptyText { color: @muted; font-size: 12px; }
QLabel#emptyText { font-size: 13px; }
QLabel#emptyTitle { font-size: 16px; font-weight: 700; }
QLabel#sectionTitle { font-size: 15px; font-weight: 700; }
QLabel#dialogTitle { font-size: 17px; font-weight: 700; }
QLabel#footer { color: @faint; font-size: 12px; }
QLabel#footerStrong { color: @muted; font-size: 12px; font-weight: 600; }
QLabel#chip { color: @muted; background: @field; border: 1px solid @border; border-radius: 10px; padding: 1px 9px; font-size: 12px; font-weight: 600; }
QLabel#chip[tone="accent"] { color: @accent_hi; background: @accent_soft; border-color: @accent_line; }
QLabel#chip[tone="success"] { color: @success; background: @success_soft; border-color: @success_line; }
QLabel#chip[tone="danger"] { color: @danger; background: @danger_soft; border-color: @danger_line; }

QFrame#inputCard { background: @surface; border: 1px solid @border; border-radius: 16px; }
QFrame#formCard { background: @surface; border: 1px solid @border; border-radius: 12px; }
QLineEdit#urlInput { background: @field; border: 1px solid @border; border-radius: 11px; padding: 10px 10px; font-size: 14px; }
QLineEdit#urlInput:focus { border-color: @accent; }

QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {
    background: @field; border: 1px solid @border; border-radius: 9px; padding: 6px 10px;
    selection-background-color: @selection; selection-color: @text;
}
QPlainTextEdit { padding: 8px; }
QSpinBox, QDoubleSpinBox { padding-right: 24px; }
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: @accent; }
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled { color: @faint; }
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border; width: 22px; border: none; background: transparent;
}
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-position: top right; margin-top: 3px; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-position: bottom right; margin-bottom: 3px; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url(ytdl:@name-chevron-up.png); width: 10px; height: 10px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url(ytdl:@name-chevron-down.png); width: 10px; height: 10px; }

QComboBox { background: @field; border: 1px solid @border; border-radius: 9px; padding: 6px 30px 6px 11px; min-height: 20px; }
QComboBox:hover { border-color: @border_hi; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: center right; width: 28px; border: none; }
QComboBox::down-arrow { image: url(ytdl:@name-chevron-down.png); width: 12px; height: 12px; }
QComboBox QAbstractItemView {
    background: @surface; color: @text; border: 1px solid @border_hi; border-radius: 8px; padding: 4px;
    selection-background-color: @accent_soft; selection-color: @text; outline: 0;
}

QPushButton { background: @field; color: @text; border: 1px solid @border; border-radius: 9px; padding: 8px 14px; font-weight: 600; }
QPushButton:hover { background: @field_hi; border-color: @border_hi; }
QPushButton:pressed { background: @border; }
QPushButton:disabled { color: @faint; }
QPushButton[variant="primary"] { background: @accent; color: @on_accent; border-color: @accent; }
QPushButton[variant="primary"]:hover { background: @accent_hi; border-color: @accent_hi; }
QPushButton[variant="primary"]:pressed { background: @accent_lo; border-color: @accent_lo; }
QPushButton[variant="ghost"] { background: transparent; border-color: transparent; color: @muted; padding: 6px 10px; }
QPushButton[variant="ghost"]:hover { background: @field; color: @text; }
QPushButton[variant="ghost"]:disabled { color: @faint; }
QPushButton#folderButton { text-align: left; padding: 7px 12px; }

QToolButton { background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 5px; }
QToolButton:hover { background: @field_hi; }
QToolButton:pressed { background: @border; }
QToolButton#headerButton { background: @surface; border-color: @border; border-radius: 10px; padding: 7px; }
QToolButton#headerButton:hover { background: @field; border-color: @border_hi; }

QFrame#segmented { background: @field; border: 1px solid @border; border-radius: 10px; }
QFrame#segmented QPushButton { background: transparent; border: none; border-radius: 7px; padding: 5px 14px; color: @muted; }
QFrame#segmented QPushButton:hover { color: @text; }
QFrame#segmented QPushButton:checked { background: @accent; color: @on_accent; }

QFrame#card { background: @surface; border: 1px solid @border; border-radius: 14px; }
QFrame#card:hover { border-color: @border_hi; }
QLabel#cardTitle { font-size: 14px; font-weight: 600; }
QLabel#cardMeta, QLabel#cardStatus { color: @muted; font-size: 12px; }
QLabel#cardStatus[tone="danger"] { color: @danger; }
QLabel#cardStatus[tone="success"] { color: @success; }
QLabel#cardStatus[tone="warning"] { color: @warning; }
QLabel#cardStatus[tone="accent"] { color: @accent_hi; }

QFrame#banner { background: @accent_soft; border: 1px solid @accent_line; border-radius: 12px; }
QFrame#banner[tone="warning"] { background: @warning_soft; border-color: @warning_line; }
QLabel#bannerText { font-weight: 600; }
QLabel#toast { background: @toast; color: @on_toast; border-radius: 10px; padding: 9px 16px; font-weight: 600; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px 1px; }
QScrollBar::handle:vertical { background: @border_hi; border-radius: 4px; min-height: 36px; }
QScrollBar::handle:vertical:hover { background: @faint; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QScrollBar:horizontal { height: 0; }

QCheckBox { spacing: 9px; }
QCheckBox::indicator { width: 17px; height: 17px; border-radius: 5px; border: 1px solid @border_hi; background: @field; }
QCheckBox::indicator:hover { border-color: @accent; }
QCheckBox::indicator:checked { background: @accent; border-color: @accent; image: url(ytdl:@name-check.png); }
QCheckBox:disabled { color: @faint; }
QCheckBox::indicator:disabled { background: @bg; border-color: @border; }
QCheckBox::indicator:checked:disabled { background: @border_hi; border-color: @border_hi; }

QTabWidget::pane { border: none; }
QTabBar::tab { background: transparent; color: @muted; padding: 7px 14px; margin-right: 4px; border-radius: 8px; font-weight: 600; }
QTabBar::tab:selected { background: @field_hi; color: @text; }
QTabBar::tab:hover:!selected { color: @text; }

QMenu { background: @surface; border: 1px solid @border_hi; border-radius: 10px; padding: 6px; }
QMenu::item { padding: 7px 18px 7px 10px; border-radius: 6px; }
QMenu::item:selected { background: @accent_soft; }
QMenu::separator { height: 1px; background: @border; margin: 5px 8px; }
"""


def _rgba(hex_color: str, alpha: float) -> str:
    c = QColor(hex_color)
    return f'rgba({c.red()}, {c.green()}, {c.blue()}, {round(alpha * 255)})'


class Theme:
    def __init__(self, name: str):
        self.name = name if name in PALETTES else 'dark'
        self.c = dict(PALETTES[self.name])
        soft = 0.16 if self.name == 'dark' else 0.10
        for key in ('accent', 'success', 'warning', 'danger'):
            self.c[f'{key}_soft'] = _rgba(self.c[key], soft)
            self.c[f'{key}_line'] = _rgba(self.c[key], soft * 2.6)
        self._icons = {}

    @property
    def dark(self) -> bool:
        return self.name == 'dark'

    def color(self, key: str) -> QColor:
        return QColor(self.c[key])

    def stylesheet(self) -> str:
        tokens = {**self.c, 'name': self.name}
        return re.sub(r'@(\w+)', lambda m: tokens.get(m.group(1), m.group(0)), STYLESHEET)

    def palette(self) -> QPalette:
        c = self.color
        p = QPalette()
        roles = {
            QPalette.ColorRole.Window: 'bg', QPalette.ColorRole.WindowText: 'text', QPalette.ColorRole.Base: 'field',
            QPalette.ColorRole.AlternateBase: 'surface', QPalette.ColorRole.ToolTipBase: 'surface',
            QPalette.ColorRole.ToolTipText: 'text', QPalette.ColorRole.Text: 'text', QPalette.ColorRole.Button: 'field',
            QPalette.ColorRole.ButtonText: 'text', QPalette.ColorRole.BrightText: 'danger',
            QPalette.ColorRole.Highlight: 'accent', QPalette.ColorRole.HighlightedText: 'on_accent',
            QPalette.ColorRole.PlaceholderText: 'faint', QPalette.ColorRole.Link: 'accent',
            QPalette.ColorRole.Light: 'field_hi', QPalette.ColorRole.Midlight: 'border_hi',
            QPalette.ColorRole.Mid: 'border', QPalette.ColorRole.Dark: 'bg', QPalette.ColorRole.Shadow: 'shadow',
        }
        for role, key in roles.items():
            p.setColor(role, c(key))
        for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
            p.setColor(QPalette.ColorGroup.Disabled, role, c('faint'))
        return p

    def pixmap(self, name: str, color: str | None = None, size: int = 18, width: float = 1.8) -> QPixmap:
        """Render one of ICONS at the screen's pixel density. *color* is a palette key or hex."""
        color = self.c.get(color or 'text', color)
        screen = QGuiApplication.primaryScreen()
        dpr = max(2.0, screen.devicePixelRatio() if screen else 1.0)
        svg = _SVG.format(fill=color if name in _FILLED else 'none', color=color, width=width, body=ICONS[name])
        renderer = QSvgRenderer(QByteArray(svg.encode()))
        pm = QPixmap(round(size * dpr), round(size * dpr))
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        renderer.render(painter, QRectF(0, 0, pm.width(), pm.height()))
        painter.end()
        pm.setDevicePixelRatio(dpr)
        return pm

    def icon(self, name: str, color: str | None = None, size: int = 18) -> QIcon:
        key = (name, color, size)
        if key not in self._icons:
            icon = QIcon(self.pixmap(name, color, size))
            icon.addPixmap(self.pixmap(name, 'faint', size), QIcon.Mode.Disabled)
            self._icons[key] = icon
        return self._icons[key]

    def apply(self, app):
        """Install palette, stylesheet and the small images the stylesheet refers to."""
        folder = os.path.join(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation), 'theme')
        os.makedirs(folder, exist_ok=True)
        for name, color in (('chevron-down', 'muted'), ('chevron-up', 'muted'), ('check', 'on_accent')):
            path = os.path.join(folder, f'{self.name}-{name}.png')
            self.pixmap(name, color, 12, width=2.4 if name == 'check' else 2.0).save(path)
        QDir.setSearchPaths('ytdl', [folder])
        app.setStyle('Fusion')
        app.setPalette(self.palette())
        app.setStyleSheet(self.stylesheet())


def system_theme() -> str:
    scheme = QGuiApplication.styleHints().colorScheme()
    return 'light' if scheme == Qt.ColorScheme.Light else 'dark'
