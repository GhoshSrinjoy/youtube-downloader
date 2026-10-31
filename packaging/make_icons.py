"""Regenerate assets/icon.png, icon.ico (Windows) and icon.icns (macOS) from assets/icon.svg.

    python packaging/make_icons.py      (needs PyQt6 and Pillow)
"""

import io
import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PIL import Image
from PyQt6.QtCore import QBuffer, QRectF, Qt
from PyQt6.QtGui import QGuiApplication, QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer

ASSETS = Path(__file__).resolve().parent.parent / 'assets'


def render(size: int) -> Image.Image:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(str(ASSETS / 'icon.svg')).render(painter, QRectF(0, 0, size, size))
    painter.end()
    buffer = QBuffer()
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    image.save(buffer, 'PNG')
    return Image.open(io.BytesIO(bytes(buffer.data())))


def main():
    big = render(1024)
    render(512).save(ASSETS / 'icon.png', optimize=True)
    big.save(ASSETS / 'icon.ico', sizes=[(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)])
    big.save(ASSETS / 'icon.icns')
    print('Wrote', ', '.join(p.name for p in sorted(ASSETS.glob('icon.*'))))


if __name__ == '__main__':
    app = QGuiApplication(sys.argv)  # Qt needs an application object to paint
    main()
