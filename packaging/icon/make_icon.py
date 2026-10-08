"""Draw the app icon (draft E: two scales clicking into sync) and write Chronon.icns (macOS),
Chronon.ico (Windows) and the window icon src/chronon/gui/icon.png. Run on a Mac (iconutil):
uv run --extra gui python packaging/icon/make_icon.py"""

import math
import shutil
import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
BLUE, TEAL, DARK, LIGHT = "#2F7DE1", "#2B8C7E", "#16181C", "#F2F4F7"


def ticks(r0: float, r1: float, n: int, color: str, w: float, major: int, rmaj: float,
          wmaj: float, minor: bool) -> str:  # fmt: skip
    out = []
    for k in range(n):
        big = k % major == 0
        if not big and not minor:
            continue
        a = 2 * math.pi * k / n
        ra = rmaj if big else r0
        out.append(
            f'<line x1="{512 + ra * math.sin(a):.1f}" y1="{512 - ra * math.cos(a):.1f}" '
            f'x2="{512 + r1 * math.sin(a):.1f}" y2="{512 - r1 * math.cos(a):.1f}" '
            f'stroke="{color}" stroke-width="{wmaj if big else w}" stroke-linecap="round"/>'
        )
    return "".join(out)


def svg(small: bool = False) -> str:
    """The full icon, or the one for 16–64 px: only the major ticks, thicker lines."""
    s = 1.6 if small else 1.0
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" '
        'viewBox="0 0 1024 1024">'
        f'<rect x="100" y="100" width="824" height="824" rx="185" fill="{DARK}"/>'
        f'<circle cx="512" cy="512" r="320" fill="none" stroke="{BLUE}" stroke-width="{18 * s}"/>'
        + ticks(250, 300, 60, BLUE, 6, 5, 230, 12 * s, not small)
        + f'<circle cx="512" cy="512" r="200" fill="none" stroke="{TEAL}" '
        f'stroke-width="{18 * s}"/>'
        + ticks(150, 182, 40, TEAL, 6, 5, 132, 12 * s, not small)
        + f'<line x1="512" y1="150" x2="512" y2="420" stroke="{LIGHT}" '
        f'stroke-width="{20 * s}" stroke-linecap="round"/>'
        f'<circle cx="512" cy="512" r="{40 * (1.2 if small else 1)}" fill="{LIGHT}"/></svg>'
    )


def render(text: str, size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(text.encode())).render(p)
    p.end()
    return image


def main() -> None:
    QGuiApplication([])
    (HERE / "Chronon.svg").write_text(svg())
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "Chronon.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = size * scale
                name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
                render(svg(small=px <= 64), px).save(str(iconset / name))
        if shutil.which("iconutil"):
            subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o",
                            str(HERE / "Chronon.icns")], check=True)  # fmt: skip
    render(svg(), 256).save(str(HERE / "Chronon.ico"))
    render(svg(), 256).save(str(ROOT / "src" / "chronon" / "gui" / "icon.png"))


if __name__ == "__main__":
    main()
