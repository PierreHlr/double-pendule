"""Outils de dessin lissé pour le canvas Tk, sans dépendance externe.

Sous Windows, Tk ne lisse pas les formes du canvas mais affiche correctement
les images PNG semi-transparentes. Les éléments ronds (masses, coins arrondis,
voyants, icône) sont donc calculés pixel par pixel une seule fois, encodés en
PNG avec la bibliothèque standard, puis réutilisés comme images.
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

WHITE: RGB = (255.0, 255.0, 255.0)


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


def mass_png(color: str, radius: float, glow_radius: float) -> bytes:
    """Masse sphérique avec reflet, ombrage et halo lumineux."""

    base = hex_to_rgb(color)
    shade = tuple(channel * 0.5 for channel in base)
    size = 2 * math.ceil(glow_radius) + 2
    center = size / 2
    highlight_x = center - 0.36 * radius
    highlight_y = center - 0.42 * radius
    glow_start = radius * 0.7

    def shader(x: float, y: float) -> tuple[float, float, float, float]:
        distance = math.hypot(x - center, y - center)
        spread = _clamp01((distance - glow_start) / (glow_radius - glow_start))
        glow = 0.30 * (1 - spread) ** 2.4
        coverage = _clamp01(radius - distance + 0.5)
        if coverage <= 0:
            return (*base, glow)
        light = max(0.0, 1 - math.hypot(x - highlight_x, y - highlight_y) / (radius * 1.05))
        body = mix(base, WHITE, 0.62 * light * light)
        body = mix(body, shade, 0.5 * min(1.0, distance / radius) ** 3)
        rgb, alpha = _over((base, glow), (body, coverage))
        return (*rgb, alpha)

    return rasterize(size, size, shader)


def hub_png(color: str, hole: str, radius: float, hole_radius: float) -> bytes:
    """Axe de rotation : anneau lumineux percé d'un trou sombre."""

    ring = hex_to_rgb(color)
    center_rgb = hex_to_rgb(hole)
    glow_radius = radius * 2.6
    size = 2 * math.ceil(glow_radius) + 2
    center = size / 2

    def shader(x: float, y: float) -> tuple[float, float, float, float]:
        distance = math.hypot(x - center, y - center)
        spread = _clamp01((distance - radius) / (glow_radius - radius))
        glow = 0.28 * (1 - spread) ** 2.2
        coverage = _clamp01(radius - distance + 0.5)
        inner = _clamp01(hole_radius - distance + 0.5)
        light = max(0.0, 1 - math.hypot(x - center + radius * 0.4, y - center + radius * 0.5) / radius)
        body = mix(mix(ring, WHITE, 0.45 * light * light), center_rgb, inner)
        rgb, alpha = _over((ring, glow), (body, coverage))
        return (*rgb, alpha)

    return rasterize(size, size, shader)


def dot_png(color: str, radius: float) -> bytes:
    """Disque plein aux bords lissés."""

    rgb = hex_to_rgb(color)
    size = 2 * math.ceil(radius) + 2
    center = size / 2

    def shader(x: float, y: float) -> tuple[float, float, float, float]:
        return (*rgb, _clamp01(radius - math.hypot(x - center, y - center) + 0.5))

    return rasterize(size, size, shader)


def pulse_png(color: str, radius: float, halo_radius: float, phase: float) -> bytes:
    """Voyant « en direct » : disque plein et onde qui s'élargit."""

    rgb = hex_to_rgb(color)
    size = 2 * math.ceil(halo_radius) + 2
    center = size / 2
    wave_radius = radius + (halo_radius - radius - 1) * phase
    wave_alpha = 0.7 * (1 - phase) ** 1.5
    thickness = 1.4

    def shader(x: float, y: float) -> tuple[float, float, float, float]:
        distance = math.hypot(x - center, y - center)
        wave = wave_alpha * _clamp01(thickness / 2 - abs(distance - wave_radius) + 0.5)
        core = _clamp01(radius - distance + 0.5)
        return (*rgb, core + wave * (1 - core))

    return rasterize(size, size, shader)


def capsule_png(width: int, height: int, color: str, opacity: float) -> bytes:
    """Capsule translucide placée derrière une étiquette."""

    rgb = hex_to_rgb(color)
    radius = height / 2

    def shader(x: float, y: float) -> tuple[float, float, float, float]:
        dx = max(abs(x - width / 2) - (width / 2 - radius), 0.0)
        dy = abs(y - height / 2)
        return (*rgb, opacity * _clamp01(radius - math.hypot(dx, dy) + 0.5))

    return rasterize(width, height, shader)


def marker_png(
    radius: float,
    ring: str,
    ring_width: float,
    fill: str | None = None,
    fill_opacity: float = 0.0,
    halo: str | None = None,
    halo_width: float = 0.0,
) -> bytes:
    """Repère circulaire : anneau coloré, liseré sombre et fond optionnels."""

    ring_rgb = hex_to_rgb(ring)
    fill_rgb = hex_to_rgb(fill) if fill else ring_rgb
    halo_rgb = hex_to_rgb(halo) if halo else ring_rgb
    outer = radius + (halo_width if halo else 0.0)
    size = 2 * math.ceil(outer) + 2
    center = size / 2

    def annulus(distance: float, inner: float, outer_radius: float) -> float:
        return max(0.0, _clamp01(outer_radius - distance + 0.5) - _clamp01(inner - distance + 0.5))

    def shader(x: float, y: float) -> tuple[float, float, float, float]:
        distance = math.hypot(x - center, y - center)
        pixel: tuple[RGB, float] = (fill_rgb, 0.0)
        if fill and fill_opacity:
            pixel = (fill_rgb, fill_opacity * _clamp01(radius - ring_width - distance + 0.5))
        if halo and halo_width:
            coverage = annulus(distance, radius - ring_width - halo_width, radius + halo_width)
            pixel = _over(pixel, (halo_rgb, 0.85 * coverage))
        pixel = _over(pixel, (ring_rgb, annulus(distance, radius - ring_width, radius)))
        return (*pixel[0], pixel[1])

    return rasterize(size, size, shader)


def corner_png(
    radius: int, border_width: int, fill: str, border: str, corner: str
) -> bytes:
    """Quart de disque servant de coin à un rectangle arrondi."""

    fill_rgb, border_rgb = hex_to_rgb(fill), hex_to_rgb(border)
    center_x = radius if "w" in corner else 0
    center_y = radius if "n" in corner else 0
    inner_radius = radius - border_width

    def shader(x: float, y: float) -> tuple[float, float, float, float]:
        distance = math.hypot(x - center_x, y - center_y)
        outer = _clamp01(radius - distance + 0.5)
        inner = _clamp01(inner_radius - distance + 0.5) if border_width else 1.0
        return (*mix(border_rgb, fill_rgb, inner), outer)

    return rasterize(radius, radius, shader)


def app_icon_png(
    size: int, background: str, pivot: str, rod: str, first: str, second: str
) -> bytes:
    """Icône de fenêtre : un double pendule stylisé sur fond arrondi."""

    scale = size / 64
    corner = 14 * scale
    points = ((32 * scale, 17 * scale), (17 * scale, 33 * scale), (40 * scale, 47 * scale))
    background_rgb, rod_rgb = hex_to_rgb(background), hex_to_rgb(rod)
    pivot_rgb, first_rgb, second_rgb = hex_to_rgb(pivot), hex_to_rgb(first), hex_to_rgb(second)

    def segment_distance(x: float, y: float, start, end) -> float:
        (ax, ay), (bx, by) = start, end
        dx, dy = bx - ax, by - ay
        t = _clamp01(((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy))
        return math.hypot(x - ax - t * dx, y - ay - t * dy)

    def shader(x: float, y: float) -> tuple[float, float, float, float]:
        qx = max(abs(x - size / 2) - (size / 2 - corner), 0.0)
        qy = max(abs(y - size / 2) - (size / 2 - corner), 0.0)
        tile = _clamp01(corner - math.hypot(qx, qy) + 0.5)
        pixel = (background_rgb, tile)
        for start, end in zip(points, points[1:]):
            coverage = _clamp01(2.2 * scale - segment_distance(x, y, start, end) + 0.5)
            pixel = _over(pixel, (rod_rgb, coverage))
        for (cx, cy), color, radius in (
            (points[0], pivot_rgb, 4.0),
            (points[1], first_rgb, 7.0),
            (points[2], second_rgb, 8.5),
        ):
            coverage = _clamp01(radius * scale - math.hypot(x - cx, y - cy) + 0.5)
            pixel = _over(pixel, (color, coverage))
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


def draw_rounded_rect(
    canvas: tk.Canvas,
    sprites: SpriteCache,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    radius: float,
    fill: str,
    border: str | None = None,
    border_width: int = 0,
    tags: str | tuple[str, ...] = (),
) -> None:
    """Rectangle arrondi aux coins lissés : quatre sprites et des bandes pleines."""

    x1, y1, x2, y2 = round(x1), round(y1), round(x2), round(y2)
    r = int(min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    width = border_width if border else 0
    border = border or fill

    def band(left: int, top: int, right: int, bottom: int, color: str) -> None:
        if right > left and bottom > top:
            canvas.create_rectangle(
                left, top, right, bottom, fill=color, outline="", width=0, tags=tags
            )

    band(x1 + width, y1 + r, x2 - width, y2 - r, fill)
    band(x1 + r, y1 + width, x2 - r, y2 - width, fill)
    if width:
        band(x1 + r, y1, x2 - r, y1 + width, border)
        band(x1 + r, y2 - width, x2 - r, y2, border)
        band(x1, y1 + r, x1 + width, y2 - r, border)
        band(x2 - width, y1 + r, x2, y2 - r, border)
    if r < 1:
        return
    for corner, x, y in (
        ("nw", x1, y1),
        ("ne", x2 - r, y1),
        ("sw", x1, y2 - r),
        ("se", x2 - r, y2 - r),
    ):
        image = sprites.photo(
            ("corner", r, width, fill, border, corner),
            lambda corner=corner: corner_png(r, width, fill, border, corner),
        )
        canvas.create_image(x, y, image=image, anchor="nw", tags=tags)


def tracked(text: str) -> str:
    """Espace légèrement les lettres, pour les petites capitales."""

    return " ".join(text)


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
    """Assortit la barre de titre Windows 11 au thème sombre de l'application."""

    if sys.platform != "win32":
        return

    def colorref(color: str) -> int:
        red, green, blue = (int(channel) for channel in hex_to_rgb(color))
        return red | green << 8 | blue << 16

    try:
        root.update_idletasks()
        window = int(root.wm_frame(), 16)
        for attribute, value in (
            (20, 1),  # DWMWA_USE_IMMERSIVE_DARK_MODE
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
