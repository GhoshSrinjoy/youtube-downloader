"""Widgets used by the main window: queue cards, progress line, segmented control, toast, banner."""

from __future__ import annotations

import math

from PyQt6.QtCore import QPropertyAnimation, QRectF, QSize, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolButton,
    QVBoxLayout, QWidget,
)

THUMB = QSize(136, 76)


def repolish(widget: QWidget):
    """Re-apply the stylesheet after a dynamic property the stylesheet matches on has changed."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def set_tone(widget: QWidget, tone: str):
    if widget.property('tone') != tone:
        widget.setProperty('tone', tone)
        repolish(widget)


def _dpr() -> float:
    screen = QGuiApplication.primaryScreen()
    return max(2.0, screen.devicePixelRatio() if screen else 1.0)


class ElidedLabel(QLabel):
    """Single-line label that shortens its text with an ellipsis instead of growing."""

    def __init__(self, text: str = '', parent=None):
        super().__init__(parent)
        self._full = ''
        self._tip = ''
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setText(text)

    def setText(self, text: str, tip: str = ''):
        """*tip* is a tooltip that stays; otherwise the full text shows as tooltip when it doesn't fit."""
        self._full = text or ''
        self._tip = tip
        self._elide()

    def text(self) -> str:
        return self._full

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self):
        elided = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, max(self.width(), 16))
        super().setText(elided)
        self.setToolTip(self._tip or (self._full if elided != self._full else ''))


class ProgressLine(QWidget):
    """Thin rounded progress bar with an animated busy mode, painted in theme colours."""

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._value = 0.0
        self._tone = 'accent'
        self._busy = False
        self._shown = True
        self._phase = 0.0
        self._anim = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=2400, loopCount=-1)
        self._anim.valueChanged.connect(self._tick)
        self.setFixedHeight(6)

    def set_value(self, fraction: float):
        self._value = min(max(fraction, 0.0), 1.0)
        self.update()

    def set_tone(self, tone: str):
        self._tone = tone
        self.update()

    def set_busy(self, busy: bool):
        if busy != self._busy:
            self._busy = busy
            self._anim.start() if busy else self._anim.stop()
            self.update()

    def set_shown(self, shown: bool):
        """Hide the line but keep its space, so cards don't change height."""
        self._shown = shown
        self.update()

    def _tick(self, t):
        # Glide left-right-left, easing at both ends, so the busy segment never leaves the track
        self._phase = 0.5 - 0.5 * math.cos(math.pi * (1 - abs(1 - 2 * t)))
        self.update()

    def paintEvent(self, event):
        if not self._shown:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        rect = QRectF(self.rect())
        radius = rect.height() / 2
        p.setBrush(self.theme.color('track'))
        p.drawRoundedRect(rect, radius, radius)
        if self._busy:
            width = rect.width() * 0.28
            chunk = QRectF((rect.width() - width) * self._phase, 0, width, rect.height())
        elif self._value > 0:
            chunk = QRectF(0, 0, max(rect.height(), rect.width() * self._value), rect.height())
        else:
            return
        if self._tone == 'accent':
            gradient = QLinearGradient(chunk.topLeft(), chunk.topRight())
            gradient.setColorAt(0, self.theme.color('accent'))
            gradient.setColorAt(1, self.theme.color('accent2'))
            p.setBrush(gradient)
        else:
            p.setBrush(self.theme.color(self._tone))
        p.drawRoundedRect(chunk, radius, radius)


class Segmented(QFrame):
    """A row of mutually exclusive buttons, e.g. Video | Audio."""

    changed = pyqtSignal(str)

    def __init__(self, options: dict[str, str], parent=None):
        super().__init__(parent)
        self.setObjectName('segmented')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        self._group = QButtonGroup(self)
        self.buttons = {}
        for key, label in options.items():
            button = QPushButton(label)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.toggled.connect(lambda on, k=key: on and self.changed.emit(k))
            self._group.addButton(button)
            layout.addWidget(button)
            self.buttons[key] = button

    def value(self) -> str:
        return next(key for key, button in self.buttons.items() if button.isChecked())

    def set_value(self, key: str):
        self.buttons[key].setChecked(True)


def thumbnail_pixmap(theme, image: QImage | None, badge: str = '', size: QSize = THUMB, radius: float = 10) -> QPixmap:
    """Rounded, center-cropped thumbnail with an optional duration badge; a placeholder when there's no image."""
    dpr = _dpr()
    w, h = size.width(), size.height()
    pm = QPixmap(round(w * dpr), round(h * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, w, h), radius, radius)
    p.setClipPath(clip)
    if image is not None and not image.isNull():
        scaled = image.scaled(pm.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        x, y = (scaled.width() - pm.width()) // 2, (scaled.height() - pm.height()) // 2
        p.drawImage(QRectF(0, 0, w, h), scaled.copy(x, y, pm.width(), pm.height()))
    else:
        p.fillRect(QRectF(0, 0, w, h), theme.color('field_hi'))
        icon = theme.pixmap('video', 'faint', 26)
        p.drawPixmap(round((w - 26) / 2), round((h - 26) / 2), icon)
    if badge:
        font = QFont(p.font())
        font.setPixelSize(10)
        font.setWeight(QFont.Weight.DemiBold)
        p.setFont(font)
        text_w = p.fontMetrics().horizontalAdvance(badge)
        box = QRectF(w - text_w - 14, h - 21, text_w + 9, 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 185))
        p.drawRoundedRect(box, 4, 4)
        p.setPen(QColor('#ffffff'))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, badge)
    p.end()
    return pm


class ItemCard(QFrame):
    """One entry in the download queue."""

    action = pyqtSignal(str)

    # state -> buttons shown as (action, icon, tooltip)
    ACTIONS = {
        'fetching': [('remove', 'x', 'Remove')],
        'ready': [('start', 'play', 'Start download'), ('remove', 'x', 'Remove')],
        'queued': [('remove', 'x', 'Remove')],
        'downloading': [('pause', 'pause', 'Pause'), ('remove', 'x', 'Cancel and remove')],
        'processing': [],
        'paused': [('resume', 'play', 'Resume'), ('remove', 'x', 'Cancel and remove')],
        'done': [('open', 'external', 'Open file'), ('folder', 'folder', 'Show in folder'), ('remove', 'x', 'Remove from list')],
        'failed': [('retry', 'retry', 'Try again'), ('remove', 'x', 'Remove')],
    }

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.setObjectName('card')
        self.theme = theme
        self._image = None
        self._badge = ''
        self._state = None

        self.thumb = QLabel()
        self.thumb.setFixedSize(THUMB)
        self.title = ElidedLabel()
        self.title.setObjectName('cardTitle')
        self.meta = ElidedLabel()
        self.meta.setObjectName('cardMeta')
        self.bar = ProgressLine(theme)
        self.status = ElidedLabel()
        self.status.setObjectName('cardStatus')

        text = QVBoxLayout()
        text.setSpacing(4)
        text.addStretch()
        text.addWidget(self.title)
        text.addWidget(self.meta)
        text.addSpacing(5)
        text.addWidget(self.bar)
        text.addSpacing(1)
        text.addWidget(self.status)
        text.addStretch()

        # Fixed width for up to three buttons, so progress bars line up whatever the state
        actions = QWidget(objectName='transparent')
        actions.setFixedWidth(3 * 34 + 2 * 2)
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(2)
        actions_row = QHBoxLayout(actions)
        actions_row.setContentsMargins(0, 0, 0, 0)
        actions_row.addStretch()
        actions_row.addLayout(self.buttons)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 12, 12, 12)
        row.setSpacing(14)
        row.addWidget(self.thumb)
        row.addLayout(text, 1)
        row.addWidget(actions)
        self._render_thumbnail()

    def set_thumbnail(self, image: QImage | None = None, badge: str | None = None):
        if image is not None:
            self._image = image
        if badge is not None:
            self._badge = badge
        self._render_thumbnail()

    def _render_thumbnail(self):
        self.thumb.setPixmap(thumbnail_pixmap(self.theme, self._image, self._badge))

    def set_state(self, state: str):
        if state == self._state:
            return
        self._state = state
        while self.buttons.count():
            self.buttons.takeAt(0).widget().deleteLater()
        for action, icon, tip in self.ACTIONS[state]:
            button = QToolButton()
            button.setIcon(self.theme.icon(icon, 'muted'))
            button.setIconSize(QSize(18, 18))
            button.setFixedSize(34, 34)
            button.setToolTip(tip)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setProperty('icon_name', icon)
            button.clicked.connect(lambda _, a=action: self.action.emit(a))
            self.buttons.addWidget(button)
        bar = {
            'fetching': ('accent', True, True), 'ready': ('accent', False, False), 'queued': ('accent', False, False),
            'downloading': ('accent', False, True), 'processing': ('accent', True, True),
            'paused': ('warning', False, True), 'done': ('success', False, True), 'failed': ('danger', False, False),
        }[state]
        self.bar.set_tone(bar[0])
        self.bar.set_busy(bar[1])
        self.bar.set_shown(bar[2])
        if state == 'done':
            self.bar.set_value(1.0)

    def set_status(self, text: str, tone: str = '', tooltip: str = ''):
        self.status.setText(text, tooltip)
        set_tone(self.status, tone)

    def set_theme(self, theme):
        self.theme = theme
        self.bar.theme = theme
        for i in range(self.buttons.count()):
            button = self.buttons.itemAt(i).widget()
            button.setIcon(theme.icon(button.property('icon_name'), 'muted'))
        self._render_thumbnail()
        self.bar.update()


class EmptyState(QWidget):
    """Shown instead of the queue while it's empty."""

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.setObjectName('transparent')
        self.art = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        title = QLabel('Your queue is empty', objectName='emptyTitle', alignment=Qt.AlignmentFlag.AlignCenter)
        text = QLabel(
            'Paste a link and press Download, or drop links anywhere on this window.\n'
            'Videos, whole playlists and channels from YouTube and 1000+ other sites.',
            objectName='emptyText', alignment=Qt.AlignmentFlag.AlignCenter)
        text.setWordWrap(True)
        tips = QHBoxLayout()
        tips.addStretch()
        for tip in ('Ctrl+V pastes links', 'Drag & drop works', 'Playlists become batches'):
            tips.addWidget(QLabel(tip, objectName='chip'))
        tips.addStretch()
        layout = QVBoxLayout(self)
        layout.addStretch(3)
        layout.addWidget(self.art)
        layout.addSpacing(10)
        layout.addWidget(title)
        layout.addWidget(text)
        layout.addSpacing(10)
        layout.addLayout(tips)
        layout.addStretch(4)
        self.set_theme(theme)

    def set_theme(self, theme):
        size, dpr = 76, _dpr()
        pm = QPixmap(round(size * dpr), round(size * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        soft = theme.color('accent')
        soft.setAlphaF(0.14 if theme.dark else 0.10)
        p.setBrush(soft)
        p.drawEllipse(QRectF(0, 0, size, size))
        p.drawPixmap(19, 19, theme.pixmap('download', 'accent', 38, width=1.7))
        p.end()
        self.art.setPixmap(pm)


class Toast(QLabel):
    """A short message that fades out near the bottom of its parent window."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName('toast')
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._effect = QGraphicsOpacityEffect(self, opacity=0.0)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b'opacity', self, duration=180)
        self._fade.finished.connect(lambda: self._effect.opacity() == 0 and self.hide())
        self._timer = QTimer(self, singleShot=True, timeout=lambda: self._animate(0.0))
        self.hide()

    def show_message(self, text: str, msec: int = 2800):
        self.setText(text)
        self.adjustSize()
        self.reposition()
        self.raise_()
        self.show()
        self._animate(1.0)
        self._timer.start(msec)

    def reposition(self):
        parent = self.parentWidget()
        self.move((parent.width() - self.width()) // 2, parent.height() - self.height() - 52)

    def _animate(self, target: float):
        self._fade.stop()
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(target)
        self._fade.start()


class Banner(QFrame):
    """A dismissable notice below the header (missing ffmpeg, update available, ...)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('banner')
        self.icon = QLabel()
        self.text = QLabel(objectName='bannerText')
        self.detail = QLabel(objectName='hint')
        self.detail.setWordWrap(True)
        self.action = QPushButton()
        self.action.setProperty('variant', 'primary')
        self.action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dismiss = QToolButton()
        self.dismiss.setToolTip('Dismiss')
        self.dismiss.clicked.connect(self.hide)
        words = QVBoxLayout()
        words.setSpacing(1)
        words.addWidget(self.text)
        words.addWidget(self.detail)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 10, 10)
        row.setSpacing(12)
        row.addWidget(self.icon)
        row.addLayout(words, 1)
        row.addWidget(self.action)
        row.addWidget(self.dismiss)
        self._callback = None
        self.action.clicked.connect(lambda: self._callback and self._callback())
        self.hide()

    def show_notice(self, theme, tone: str, icon: str, text: str, detail: str = '', action: str = '', callback=None):
        set_tone(self, tone)
        self.icon.setPixmap(theme.pixmap(icon, 'warning' if tone == 'warning' else 'accent', 22))
        self.dismiss.setIcon(theme.icon('x', 'muted', 16))
        self.text.setText(text)
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))
        self.action.setText(action)
        self.action.setVisible(bool(action))
        self._callback = callback
        self.show()
