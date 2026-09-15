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
SLATE = "#2B3443"
SLATE_MUTED = "#9AA4B4"

SIZE = 512.0
MID = SIZE / 2

SURFACES = {
    "channel": (INK, PAPER, RED),
    "chat": (PAPER, INK, RED),
    "bot": (RED, PAPER, INK),
    # The technical channel is the only surface with the accent off: nothing here is for readers.
    "tech": (SLATE, PAPER, SLATE_MUTED),
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


def mark(
    bg: str,
    fg: str,
    hot: str,
    height: float = 282,
    bar: float = 30,
    gap: float = 52,
    dark: tuple[str, str, str] | None = None,
) -> str:
    """The knot: a Didone section sign beside the margin bar that flags an amended provision."""
    path, (x0, y0, x1, y1) = _outline(DIDOT, "§")
    scale = height / (y1 - y0)
    glyph_w = (x1 - x0) * scale
    total = bar + gap + glyph_w
    cx = MID + total / 2 - glyph_w / 2
    bar_h = height * 0.84
    # Only the dark rule lives in CSS: a rasteriser that ignores the stylesheet still gets the
    # light colours off the fill attributes, which a stylesheet rule overrides where one applies.
    style = ""
    if dark is not None:
        style = (
            "<style>@media (prefers-color-scheme: dark){"
            f".bg{{fill:{dark[0]}}}.fg{{fill:{dark[1]}}}.hot{{fill:{dark[2]}}}}}</style>"
        )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">'
        f"{style}"
        f'<rect class="bg" width="512" height="512" fill="{bg}"/>'
        f'<path class="fg" fill="{fg}" d="{path}" transform="translate('
        f"{cx - (x0 + x1) / 2 * scale:.2f} {MID + (y0 + y1) / 2 * scale:.2f}) "
        f'scale({scale:.5f})"/>'
        f'<rect class="hot" x="{MID - total / 2:.2f}" y="{MID - bar_h / 2:.2f}" width="{bar}" '
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
    # It also carries the dark theme, because an ink tile all but vanishes in a dark tab strip.
    site = {"height": 360, "bar": 40, "gap": 44}
    light = mark(INK, PAPER, RED, **site)
    (HERE / "favicon.svg").write_text(mark(INK, PAPER, RED, **site, dark=(PAPER, INK, RED)))
    # Rasterise the light mark: QuickLook honours prefers-color-scheme, and the PNGs must not.
    with tempfile.TemporaryDirectory() as tmp:
        flat = pathlib.Path(tmp) / "favicon-light.svg"
        flat.write_text(light)
        art = render(flat)
    for size in (16, 32, 48):
        save(art, f"favicon-{size}.png", size)
    save(art, "apple-touch-icon-180.png", 180)
    for size in (192, 512):
        save(art, f"icon-{size}.png", size)
    art.save(HERE / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    print(f"wrote {len(list(HERE.glob('*.svg')))} svg, {len(list(PNG.glob('*.png')))} png, 1 ico")


if __name__ == "__main__":
    main()
