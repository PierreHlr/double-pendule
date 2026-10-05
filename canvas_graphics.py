"""Outils de dessin lissé pour le canvas Tk, sans dépendance externe.

Sous Windows, Tk ne lisse pas les formes du canvas mais affiche correctement
les images PNG semi-transparentes. Les éléments ronds (masses, axe, repères,
icône) sont donc calculés pixel par pixel une seule fois, encodés en PNG avec
la bibliothèque standard, puis réutilisés comme images.
"""

from __future__ import annotations

import ctypes
import math
import struct
import sys
import tkinter as tk
import zlib
from collections.abc import Callable, Hashable, Iterable

RGB = tuple[float, float, float]
Shader = Callable[[float, float], tuple[float, float, float, float]]


# --------------------------------------------------------------------------
# Couleurs


def hex_to_rgb(color: str) -> RGB:
    return tuple(float(int(color[index : index + 2], 16)) for index in (1, 3, 5))


def rgb_to_hex(rgb: Iterable[float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(channel))):02x}" for channel in rgb)


def mix(first: RGB, second: RGB, fraction: float) -> RGB:
    """Interpole linéairement de ``first`` (0) vers ``second`` (1)."""

    return tuple(a + (b - a) * fraction for a, b in zip(first, second))


def blend(color: str, background: str, fraction: float) -> str:
    """Couleur opaque équivalente à ``color`` posée sur ``background``."""

    fraction = max(0.0, min(1.0, fraction))
    return rgb_to_hex(mix(hex_to_rgb(background), hex_to_rgb(color), fraction))


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value


def _over(
    bottom: tuple[RGB, float], top: tuple[RGB, float]
) -> tuple[RGB, float]:
    """Composition « source over » en alpha non prémultiplié."""

    (bottom_rgb, bottom_alpha), (top_rgb, top_alpha) = bottom, top
    alpha = top_alpha + bottom_alpha * (1 - top_alpha)
    if alpha <= 0:
        return bottom_rgb, 0.0
    rgb = tuple(
        (t * top_alpha + b * bottom_alpha * (1 - top_alpha)) / alpha
        for t, b in zip(top_rgb, bottom_rgb)
    )
    return rgb, alpha


# --------------------------------------------------------------------------
# Encodage PNG et rastérisation


def encode_png(width: int, height: int, pixels: bytes | bytearray) -> bytes:
    """Encode des pixels RGBA 8 bits (ligne par ligne) au format PNG."""

    stride = width * 4
    raw = b"".join(
        b"\x00" + bytes(pixels[row * stride : (row + 1) * stride])
        for row in range(height)
    )

    def chunk(kind: bytes, data: bytes) -> bytes:
        checksum = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def rasterize(width: int, height: int, shader: Shader) -> bytes:
    """Évalue ``shader`` au centre de chaque pixel et renvoie un PNG."""

    pixels = bytearray(width * height * 4)
    offset = 0
    for row in range(height):
        y = row + 0.5
        for column in range(width):
            red, green, blue, alpha = shader(column + 0.5, y)
            if alpha > 0.002:
                pixels[offset] = max(0, min(255, round(red)))
                pixels[offset + 1] = max(0, min(255, round(green)))
                pixels[offset + 2] = max(0, min(255, round(blue)))
                pixels[offset + 3] = max(0, min(255, round(alpha * 255)))
            offset += 4
    return encode_png(width, height, pixels)


# --------------------------------------------------------------------------
# Sprites


def _disc(distance: float, radius: float) -> float:
    """Couverture lissée d'un disque de rayon ``radius``."""

    return _clamp01(radius - distance + 0.5)


def mass_png(color: str, radius: float, outline: str, outline_width: float) -> bytes:
    """Masse : disque de couleur plate cerclé d'encre."""

    fill_rgb, outline_rgb = hex_to_rgb(color), hex_to_rgb(outline)
    size = 2 * math.ceil(radius) + 2
    center = size / 2

    def shader(x: float, y: float) -> tuple[float, float, float, float]:
        distance = math.hypot(x - center, y - center)
        inner = _disc(distance, radius - outline_width)
        return (*mix(outline_rgb, fill_rgb, inner), _disc(distance, radius))

    return rasterize(size, size, shader)


def hub_png(color: str, hole: str, radius: float, hole_radius: float) -> bytes:
    """Axe de rotation : anneau plein percé d'un trou."""

    ring_rgb, hole_rgb = hex_to_rgb(color), hex_to_rgb(hole)
    size = 2 * math.ceil(radius) + 2
    center = size / 2

    def shader(x: float, y: float) -> tuple[float, float, float, float]:
        distance = math.hypot(x - center, y - center)
        return (*mix(ring_rgb, hole_rgb, _disc(distance, hole_radius)), _disc(distance, radius))

    return rasterize(size, size, shader)


def veil_png(width: int, height: int, color: str, opacity: float) -> bytes:
    """Rectangle translucide placé derrière une étiquette, pour la détacher du dessin."""

    red, green, blue = (round(channel) for channel in hex_to_rgb(color))
    pixel = bytes((red, green, blue, round(255 * _clamp01(opacity))))
    return encode_png(width, height, pixel * (width * height))


def marker_png(radius: float, ring: str, ring_width: float, edge: str, edge_width: float) -> bytes:
    """Repère circulaire : anneau coloré bordé d'un liseré qui le détache du fond."""

    ring_rgb, edge_rgb = hex_to_rgb(ring), hex_to_rgb(edge)
    outer = radius + edge_width
    size = 2 * math.ceil(outer) + 2
    center = size / 2

    def annulus(distance: float, inner: float, outer_radius: float) -> float:
        return max(0.0, _disc(distance, outer_radius) - _disc(distance, inner))

    def shader(x: float, y: float) -> tuple[float, float, float, float]:
        distance = math.hypot(x - center, y - center)
        pixel = (edge_rgb, annulus(distance, radius - ring_width - edge_width, outer))
        pixel = _over(pixel, (ring_rgb, annulus(distance, radius - ring_width, radius)))
        return (*pixel[0], pixel[1])

    return rasterize(size, size, shader)


def app_icon_png(size: int, paper: str, ink: str, first: str, second: str) -> bytes:
    """Icône de fenêtre : un double pendule à l'encre sur un carré de papier."""

    scale = size / 64
    frame = 3 * scale
    points = ((32 * scale, 15 * scale), (17 * scale, 32 * scale), (41 * scale, 46 * scale))
    paper_rgb, ink_rgb = hex_to_rgb(paper), hex_to_rgb(ink)
    first_rgb, second_rgb = hex_to_rgb(first), hex_to_rgb(second)

    def segment_distance(x: float, y: float, start, end) -> float:
        (ax, ay), (bx, by) = start, end
        dx, dy = bx - ax, by - ay
        t = _clamp01(((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy))
        return math.hypot(x - ax - t * dx, y - ay - t * dy)

    def shader(x: float, y: float) -> tuple[float, float, float, float]:
        inside = min(x, y, size - x, size - y) > frame
        pixel = (paper_rgb if inside else ink_rgb, 1.0)
        for start, end in zip(points, points[1:]):
            coverage = _clamp01(2.4 * scale - segment_distance(x, y, start, end) + 0.5)
            pixel = _over(pixel, (ink_rgb, coverage))
        for (cx, cy), color, radius in (
            (points[0], ink_rgb, 4.0),
            (points[1], first_rgb, 7.5),
            (points[2], second_rgb, 9.0),
        ):
            distance = math.hypot(x - cx, y - cy)
            body = mix(ink_rgb, color, _disc(distance, (radius - 2.2) * scale))
            pixel = _over(pixel, (body, _disc(distance, radius * scale)))
        return (*pixel[0], pixel[1])

    return rasterize(size, size, shader)


class SpriteCache:
    """Conserve les images Tk générées (et empêche leur destruction)."""

    def __init__(self, master: tk.Misc):
        self.master = master
        self._images: dict[Hashable, tk.PhotoImage] = {}

    def photo(self, key: Hashable, factory: Callable[[], bytes]) -> tk.PhotoImage:
        image = self._images.get(key)
        if image is None:
            image = tk.PhotoImage(master=self.master, data=factory(), format="png")
            self._images[key] = image
        return image


def draw_box(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    fill: str | None,
    border: str | None = None,
    border_width: int = 0,
    tags: str | tuple[str, ...] = (),
) -> None:
    """Rectangle à angles droits, bordure tracée à l'intérieur au pixel près.

    Tk centre le contour d'un rectangle sur son bord, ce qui le décale d'un
    pixel selon l'épaisseur : la bordure est donc faite de quatre bandes pleines.
    """

    x1, y1, x2, y2 = round(x1), round(y1), round(x2), round(y2)
    width = border_width if border else 0

    def band(left: int, top: int, right: int, bottom: int, color: str) -> None:
        if right > left and bottom > top:
            canvas.create_rectangle(
                left, top, right, bottom, fill=color, outline="", width=0, tags=tags
            )

    if fill:
        band(x1 + width, y1 + width, x2 - width, y2 - width, fill)
    if width:
        band(x1, y1, x2, y1 + width, border)
        band(x1, y2 - width, x2, y2, border)
        band(x1, y1 + width, x1 + width, y2 - width, border)
        band(x2 - width, y1 + width, x2, y2 - width, border)


# --------------------------------------------------------------------------
# Intégration Windows


def enable_high_dpi() -> None:
    """Évite le flou d'agrandissement de Windows sur les écrans haute densité."""

    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def style_title_bar(root: tk.Tk, caption: str, text: str, border: str) -> None:
    """Assortit la barre de titre Windows 11 au papier de l'application."""

    if sys.platform != "win32":
        return

    def colorref(color: str) -> int:
        red, green, blue = (int(channel) for channel in hex_to_rgb(color))
        return red | green << 8 | blue << 16

    try:
        root.update_idletasks()
        window = int(root.wm_frame(), 16)
        for attribute, value in (
            (20, 0),  # DWMWA_USE_IMMERSIVE_DARK_MODE : boutons de fenêtre clairs
            (34, colorref(border)),  # DWMWA_BORDER_COLOR
            (35, colorref(caption)),  # DWMWA_CAPTION_COLOR
            (36, colorref(text)),  # DWMWA_TEXT_COLOR
        ):
            data = ctypes.c_int(value)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                window, attribute, ctypes.byref(data), ctypes.sizeof(data)
            )
    except (AttributeError, OSError, ValueError, tk.TclError):
        pass
