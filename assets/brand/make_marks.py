"""Generate the lexinform marks: font-free SVG plus the PNG and ICO sizes each surface needs."""

from __future__ import annotations

import pathlib
import subprocess
import tempfile

from fontTools.misc.transform import Transform
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTCollection, TTFont
from PIL import Image

HERE = pathlib.Path(__file__).parent
PNG = HERE / "png"

# Didot ships with macOS and is only ever read here: the marks carry its outlines, not the font.
DIDOT = ("/System/Library/Fonts/Supplemental/Didot.ttc", 0)

INK = "#10161F"
PAPER = "#F4EFE6"
RED = "#C0362C"

SIZE = 512.0
MID = SIZE / 2

SURFACES = {
    "channel": (INK, PAPER, RED),
    "chat": (PAPER, INK, RED),
    "bot": (RED, PAPER, INK),
}


def _font(spec: tuple[str, int]) -> TTFont:
    path, index = spec
    return TTCollection(path).fonts[index] if path.endswith(".ttc") else TTFont(path)


def _outline(spec: tuple[str, int], char: str) -> tuple[str, tuple[float, float, float, float]]:
    """A glyph as SVG path data in a 1000-unit em, with its ink box."""
    font = _font(spec)
    upm = font["head"].unitsPerEm
    glyph = font.getGlyphSet()[font.getBestCmap()[ord(char)]]
    pen = SVGPathPen(font.getGlyphSet(), ntos=lambda v: f"{v:.2f}")
    glyph.draw(TransformPen(pen, Transform(1000 / upm, 0, 0, -1000 / upm, 0, 0)))
    bounds = BoundsPen(font.getGlyphSet())
    glyph.draw(bounds)
    return pen.getCommands(), tuple(v * 1000 / upm for v in bounds.bounds)


def mark(bg: str, fg: str, hot: str, height: float = 282, bar: float = 30, gap: float = 52) -> str:
    """The knot: a Didone section sign beside the margin bar that flags an amended provision."""
    path, (x0, y0, x1, y1) = _outline(DIDOT, "§")
    scale = height / (y1 - y0)
    glyph_w = (x1 - x0) * scale
    total = bar + gap + glyph_w
    cx = MID + total / 2 - glyph_w / 2
    bar_h = height * 0.84
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">'
        f'<rect width="512" height="512" fill="{bg}"/>'
        f'<path fill="{fg}" d="{path}" transform="translate('
        f"{cx - (x0 + x1) / 2 * scale:.2f} {MID + (y0 + y1) / 2 * scale:.2f}) "
        f'scale({scale:.5f})"/>'
        f'<rect x="{MID - total / 2:.2f}" y="{MID - bar_h / 2:.2f}" width="{bar}" '
        f'height="{bar_h:.2f}" rx="{bar / 2}" fill="{hot}"/>'
        "</svg>"
    )


def render(svg_path: pathlib.Path) -> Image.Image:
    """QuickLook is the only SVG rasteriser macOS ships; 512 is its ceiling for these files."""
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["qlmanage", "-t", "-s", "512", "-o", tmp, str(svg_path)],
            capture_output=True,
            check=True,
        )
        out = pathlib.Path(tmp) / f"{svg_path.name}.png"
        if not out.exists():
            raise SystemExit(f"qlmanage rendered nothing for {svg_path}")
        return Image.open(out).convert("RGB")


def save(image: Image.Image, name: str, size: int) -> None:
    image.resize((size, size), Image.LANCZOS).save(PNG / name)


def main() -> None:
    PNG.mkdir(exist_ok=True)
    for name, colors in SURFACES.items():
        path = HERE / f"{name}.svg"
        path.write_text(mark(*colors))
        save(render(path), f"{name}-512.png", 512)

    # The site mark is set larger and the bar thicker: at 16 px the bar is what stays legible.
    favicon = HERE / "favicon.svg"
    favicon.write_text(mark(INK, PAPER, RED, height=360, bar=40, gap=44))
    art = render(favicon)
    for size in (16, 32, 48):
        save(art, f"favicon-{size}.png", size)
    save(art, "apple-touch-icon-180.png", 180)
    for size in (192, 512):
        save(art, f"icon-{size}.png", size)
    art.save(HERE / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    print(f"wrote {len(list(HERE.glob('*.svg')))} svg, {len(list(PNG.glob('*.png')))} png, 1 ico")


if __name__ == "__main__":
    main()
