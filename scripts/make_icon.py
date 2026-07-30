#!/usr/bin/env python3
"""Generate resources/icon.ico.

Qt can render and write PNGs but its ICO *write* support is not guaranteed to be
present in every build, so the container is assembled by hand here. Vista and
later accept PNG-compressed entries inside an .ico, which keeps this to a few
lines of struct packing and avoids a Pillow dependency.

    python scripts/make_icon.py
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QBuffer, QByteArray, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QBrush,
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QApplication  # noqa: E402

from torrentapp.ui.theme import COLORS  # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(size: int) -> QImage:
    """A dark rounded tile with a cyan download arrow over a baseline."""
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # --- tile ---------------------------------------------------------------
    inset = size * 0.045
    body = QRectF(inset, inset, size - inset * 2, size - inset * 2)
    radius = size * 0.22

    backdrop = QLinearGradient(body.topLeft(), body.bottomRight())
    backdrop.setColorAt(0.0, QColor("#1d2b2b"))
    backdrop.setColorAt(1.0, QColor(COLORS["bg"]))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(backdrop))
    painter.drawRoundedRect(body, radius, radius)

    painter.setPen(QPen(QColor(COLORS["accent_dim"]), max(1.0, size * 0.018)))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(body, radius, radius)

    # --- arrow --------------------------------------------------------------
    accent = QLinearGradient(0, body.top(), 0, body.bottom())
    accent.setColorAt(0.0, QColor(COLORS["accent_bright"]))
    accent.setColorAt(1.0, QColor(COLORS["accent"]))

    cx = size / 2.0
    shaft_w = size * 0.135
    shaft_top = size * 0.24
    shaft_bottom = size * 0.55
    head_half = size * 0.23
    tip_y = size * 0.75

    arrow = QPainterPath()
    arrow.moveTo(cx - shaft_w / 2, shaft_top)
    arrow.lineTo(cx + shaft_w / 2, shaft_top)
    arrow.lineTo(cx + shaft_w / 2, shaft_bottom)
    arrow.lineTo(cx + head_half, shaft_bottom)
    arrow.lineTo(cx, tip_y)
    arrow.lineTo(cx - head_half, shaft_bottom)
    arrow.lineTo(cx - shaft_w / 2, shaft_bottom)
    arrow.closeSubpath()

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(accent))
    painter.drawPath(arrow)

    # --- baseline -----------------------------------------------------------
    bar_h = max(1.5, size * 0.062)
    bar = QRectF(cx - size * 0.26, size * 0.815, size * 0.52, bar_h)
    painter.setBrush(QColor(COLORS["fg"]))
    painter.drawRoundedRect(bar, bar_h / 2, bar_h / 2)

    painter.end()
    return image


def to_png(image: QImage) -> bytes:
    # The QByteArray must be held in a named variable: QBuffer keeps a raw
    # pointer to it, so passing a temporary lets Python free the storage out
    # from under Qt and the process dies without a traceback.
    storage = QByteArray()
    buffer = QBuffer(storage)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError("Qt failed to encode PNG")
    buffer.close()
    return bytes(storage)


def build_ico(images: dict[int, bytes]) -> bytes:
    """Pack PNG payloads into an ICONDIR container."""
    count = len(images)
    header = struct.pack("<HHH", 0, 1, count)  # reserved, type=icon, count
    offset = 6 + count * 16

    directory = b""
    payload = b""
    for size, data in sorted(images.items()):
        # 256 is encoded as 0 in the single-byte width/height fields.
        dimension = 0 if size >= 256 else size
        directory += struct.pack(
            "<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(data), offset
        )
        payload += data
        offset += len(data)

    return header + directory + payload


def main() -> int:
    app = QApplication(sys.argv[:1])  # noqa: F841 - QPainter needs a QGuiApplication

    images = {size: to_png(render(size)) for size in SIZES}
    ico = build_ico(images)

    target = ROOT / "resources" / "icon.ico"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(ico)

    # A PNG copy is handy for READMEs and shortcuts that prefer one.
    (target.parent / "icon.png").write_bytes(images[256])

    print(f"wrote {target} ({len(ico):,} bytes, {len(SIZES)} sizes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
