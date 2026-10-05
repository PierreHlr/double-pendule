"""Vues « carte de chaleur » de l'application : divergence et périodicité.

L'axe horizontal porte θ₁ initial, l'axe vertical θ₂ initial, de −180° à 180°.
Chaque carte utilise une rampe qui va de l'encre (valeur faible) au papier
(valeur forte) en passant par une couleur du portfolio : terracotta pour la
divergence, ardoise pour la périodicité. Un clic applique le couple d'angles
au pendule sélectionné.
"""

from __future__ import annotations

import math
import tkinter as tk
from typing import TYPE_CHECKING

import numpy as np

import theme
from canvas_graphics import blend, draw_box, hex_to_rgb, marker_png
from chaos_maps import DIVERGENCE, PERIODICITY, Candidate, MapJob
from double_pendulum import wrap_degrees
from simulation_config import SETTINGS
from theme import format_value

if TYPE_CHECKING:
    from main import DoublePendulumApp

PERIODIC_WORST = 0.3
PERIODIC_BEST = 0.003
CLICK_STEP = 0.5

TEXTS = {
    DIVERGENCE: (
        "Divergence des trajectoires",
        "Vitesse à laquelle deux pendules presque identiques s'écartent l'un de "
        "l'autre selon leurs angles de départ (exposant de Lyapunov λ, mesuré "
        "sur {duration} s).",
    ),
    PERIODICITY: (
        "Proximité d'un mouvement périodique",
        "Plus petit écart entre l'état de départ et un état ultérieur (angles et "
        "vitesses), mesuré sur {duration} s. Les zones claires repassent presque "
        "exactement par leur position initiale.",
    ),
}


# --------------------------------------------------------------------------
# Couleurs

# Passage sRGB linéaire ↔ OKLab (Björn Ottosson, 2020).
SRGB_TO_LMS = np.array(
    [
        [0.4122214708, 0.5363325363, 0.0514459929],
        [0.2119034982, 0.6806995451, 0.1073969566],
        [0.0883024619, 0.2817188376, 0.6299787005],
    ]
)
LMS_TO_OKLAB = np.array(
    [
        [0.2104542553, 0.7936177850, -0.0040720468],
        [1.9779984951, -2.4285922050, 0.4505937099],
        [0.0259040371, 0.7827717662, -0.8086757660],
    ]
)
OKLAB_TO_LMS = np.array(
    [
        [1.0, 0.3963377774, 0.2158037573],
        [1.0, -0.1055613458, -0.0638541728],
        [1.0, -0.0894841775, -1.2914855480],
    ]
)
LMS_TO_SRGB = np.array(
    [
        [4.0767416621, -3.3077115913, 0.2309699292],
        [-1.2684380046, 2.6097574011, -0.3413193965],
        [-0.0041960863, -0.7034186147, 1.7076147010],
    ]
)


def _to_oklab(color: str) -> np.ndarray:
    rgb = np.array(hex_to_rgb(color)) / 255
    linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    return LMS_TO_OKLAB @ np.cbrt(SRGB_TO_LMS @ linear)


def _from_oklab(lab: np.ndarray) -> np.ndarray:
    """Couleurs OKLab (tableau n × 3) converties en sRGB 8 bits."""

    linear = np.clip(((lab @ OKLAB_TO_LMS.T) ** 3) @ LMS_TO_SRGB.T, 0, 1)
    rgb = np.where(linear <= 0.0031308, 12.92 * linear, 1.055 * linear ** (1 / 2.4) - 0.055)
    return np.round(rgb * 255).astype(np.uint8)


def sequential_ramp(dark: str, middle: str, light: str, steps: int = 256) -> np.ndarray:
    """Rampe du sombre au clair passant par ``middle``, interpolée dans OKLab.

    ``middle`` est placée selon sa clarté : la clarté perçue croît
    régulièrement d'un bout à l'autre de la rampe.
    """

    start, center, end = (_to_oklab(color) for color in (dark, middle, light))
    position = (center[0] - start[0]) / (end[0] - start[0])
    t = np.linspace(0, 1, steps)[:, None]
    lower = start + (center - start) * (t / position)
    upper = center + (end - center) * ((t - position) / (1 - position))
    return _from_oklab(np.where(t <= position, lower, upper))


RAMPS = {
    DIVERGENCE: sequential_ramp(theme.INK, theme.TERRACOTTA, theme.PAPER),
    PERIODICITY: sequential_ramp(theme.INK, theme.SLATE, theme.PAPER),
}
PENDING_RGB = np.array(hex_to_rgb(theme.PAPER_DARK), dtype=np.uint8)


def divergence_limit(values: np.ndarray) -> float:
    """Borne haute de l'échelle : 99ᵉ centile arrondi au demi supérieur."""

    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return 1.0
    return max(0.5, math.ceil(float(np.percentile(finite, 99)) * 2) / 2)


def normalize(kind: str, values: np.ndarray, limit: float) -> np.ndarray:
    """Valeurs ramenées dans [0, 1] (1 = le plus clair) ; ``nan`` = pas encore calculé."""

    if kind == DIVERGENCE:
        return np.clip(values / limit, 0, 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        score = np.log(PERIODIC_WORST / values) / math.log(PERIODIC_WORST / PERIODIC_BEST)
    score = np.where(np.isinf(values), 0.0, score)
    return np.clip(score, 0, 1)


def heatmap_ppm(kind: str, values: np.ndarray, limit: float, size: int) -> bytes:
    """Image PPM de ``size`` pixels de côté, θ₂ croissant vers le haut."""

    level = normalize(kind, values, limit)
    pending = np.isnan(level)
    indices = np.round(np.where(pending, 0, level) * 255).astype(int)
    rgb = RAMPS[kind][indices]
    rgb[pending] = PENDING_RGB
    rgb = rgb[::-1]
    mapping = np.arange(size) * values.shape[0] // size
    image = np.ascontiguousarray(rgb[mapping][:, mapping])
    return b"P6 %d %d 255\n" % (size, size) + image.tobytes()


def format_percent(fraction: float) -> str:
    percent = fraction * 100
    digits = 0 if percent >= 10 else 1 if percent >= 1 else 2
    return f"{percent:.{digits}f}".replace(".", ",") + " %"


def describe(kind: str, value: float, period: float | None) -> tuple[str, str]:
    """Valeur principale et interprétation d'une cellule."""

    if kind == DIVERGENCE:
        text = f"λ ≈ {format_value(round(value, 2))} s⁻¹"
        if value > 0.05:
            return text, f"écart × 2 toutes les {format_value(round(math.log(2) / value, 2))} s"
        return text, "les deux pendules restent groupés"
    if not math.isfinite(value):
        return "aucun retour proche", "le pendule ne repasse pas par son départ"
    detail = f"après {format_value(round(period, 2))} s" if period and math.isfinite(period) else ""
    return f"retour à {format_percent(value)}", detail


# --------------------------------------------------------------------------
# Vue


class MapView:
    """Carte de chaleur des angles initiaux, dessinée dans la zone de scène."""

    def __init__(self, app: DoublePendulumApp, kind: str):
        self.app = app
        self.kind = kind
        self.job: MapJob | None = None
        self.plot = (0.0, 0.0, 0)
        self.panel = (0.0, 0.0, 0.0, 0.0)
        self._photo = None
        self._image_item: int | None = None
        self._image_state: tuple | None = None
        self._limit = 1.0
        self._hover: tuple[int, int] | None = None
        self._listed = 0

    # -- géométrie ------------------------------------------------------

    def _to_screen(self, theta1: float, theta2: float) -> tuple[float, float]:
        x, y, size = self.plot
        return x + (theta1 + 180) / 360 * size, y + (180 - theta2) / 360 * size

    def _to_angles(self, sx: float, sy: float) -> tuple[float, float] | None:
        x, y, size = self.plot
        if not (x <= sx < x + size and y <= sy < y + size):
            return None
        return (sx - x) / size * 360 - 180, 180 - (sy - y) / size * 360

    def contains(self, sx: float, sy: float) -> bool:
        return self._to_angles(sx, sy) is not None

    def _cell(self, sx: float, sy: float) -> tuple[int, int] | None:
        if self.job is None or self._to_angles(sx, sy) is None:
            return None
        x, y, size = self.plot
        count = self.job.resolution
        column = min(count - 1, int((sx - x) / size * count))
        row = min(count - 1, int((y + size - sy) / size * count))
        return row, column

    # -- dessin ----------------------------------------------------------

    def draw(self) -> None:
        app = self.app
        canvas, px, fonts = app.canvas, app.px, app.fonts
        x1, y1, x2, y2 = app.layout.stage
        line = app.layout.line
        self.job = app.request_map(self.kind)
        self._image_state = None
        self._hover = None

        title, description = TEXTS[self.kind]
        duration = format_value(SETTINGS.map_duration)
        canvas.create_text(
            x1, y1, text=title.upper(), anchor="nw", fill=theme.INK, font=fonts["heading"]
        )
        text = canvas.create_text(
            x1,
            y1 + fonts["heading"].metrics("linespace") + px(2),
            text=description.format(duration=duration),
            anchor="nw",
            width=x2 - x1,
            fill=theme.MUTED,
            font=fonts["label"],
        )
        top = canvas.bbox(text)[3] + px(16)
        axis_band, bottom_band = px(46), px(40)
        panel_width = px(236)
        size = int(min(x2 - x1 - axis_band - panel_width - px(28), y2 - top - bottom_band))
        size = max(size, int(px(120)))
        plot_x = round(x1 + axis_band)
        top = round(top)
        self.plot = (plot_x, top, size)
        self.panel = (plot_x + size + px(28), top, x2, y2)

        self._image_item = canvas.create_image(plot_x, top, anchor="nw", tags="map_image")
        # Cadre d'encre autour de la carte, comme les images du portfolio.
        draw_box(canvas, plot_x - line, top - line, plot_x + size + line, top + size + line, None, theme.INK, line)
        axis_font = fonts["axis"]
        tick = max(1, round(px(1)))
        for degrees in (-180, -90, 0, 90, 180):
            sx, sy = self._to_screen(degrees, degrees)
            label = format_value(degrees, "°")
            canvas.create_line(
                sx, top + size + line, sx, top + size + px(6), fill=theme.HELPER, width=tick
            )
            canvas.create_text(
                sx, top + size + px(9), text=label, anchor="n", fill=theme.HELPER, font=axis_font
            )
            canvas.create_line(plot_x - px(6), sy, plot_x - line, sy, fill=theme.HELPER, width=tick)
            canvas.create_text(
                plot_x - px(9), sy, text=label, anchor="e", fill=theme.HELPER, font=axis_font
            )
        canvas.create_text(
            plot_x + size / 2,
            top + size + px(24),
            text="θ₁ initial",
            anchor="n",
            fill=theme.MUTED,
            font=fonts["label"],
        )
        canvas.create_text(
            x1,
            top + size / 2,
            text="θ₂ initial",
            angle=90,
            anchor="n",
            fill=theme.MUTED,
            font=fonts["label"],
        )
        self.refresh()

    def refresh(self) -> None:
        """Met à jour l'image, l'avancement, les repères et le panneau."""

        self._update_image()
        self._draw_panel()
        self.draw_markers()
        self._draw_progress()
        self._draw_hover()

    def _update_image(self) -> None:
        if self.job is None or self._image_item is None:
            return
        job = self.job
        if self.kind == DIVERGENCE:
            self._limit = divergence_limit(job.values)
        state = (id(job), job.completed, self._limit, self.plot[2])
        if state == self._image_state:
            return
        self._image_state = state
        size = self.plot[2]
        self._photo = tk.PhotoImage(
            master=self.app.canvas,
            data=heatmap_ppm(self.kind, job.values, self._limit, size),
            format="ppm",
        )
        self.app.canvas.itemconfigure(self._image_item, image=self._photo)

    def _draw_progress(self) -> None:
        app = self.app
        canvas, px, fonts = app.canvas, app.px, app.fonts
        canvas.delete("map_progress")
        x, y, size = self.plot
        job = self.job
        if job is None:
            return
        if job.error:
            message, color, fraction = f"Le calcul a échoué\n{job.error}", theme.BRICK, None
        elif job.completed < job.total:
            message, color, fraction = (
                f"Calcul de la carte… {round(job.progress * 100)} %",
                theme.INK,
                job.progress,
            )
        else:
            return
        font = fonts["tag"]
        lines = message.split("\n")
        width = max(font.measure(text) for text in lines) + px(32)
        height = len(lines) * font.metrics("linespace") + px(20) + (px(10) if fraction is not None else 0)
        left, top = x + size / 2 - width / 2, y + size / 2 - height / 2
        draw_box(
            canvas,
            left,
            top,
            left + width,
            top + height,
            theme.PAPER,
            theme.INK,
            app.layout.line,
            tags="map_progress",
        )
        canvas.create_text(
            left + width / 2,
            top + px(10),
            text=message,
            anchor="n",
            justify="center",
            fill=color,
            font=font,
            tags="map_progress",
        )
        if fraction is not None:
            bar_y = round(top + height - px(14))
            bar_x1, bar_x2 = left + px(16), left + width - px(16)
            bar_height = round(px(4))
            draw_box(canvas, bar_x1, bar_y, bar_x2, bar_y + bar_height, theme.LINE_SOFT, tags="map_progress")
            draw_box(
                canvas,
                bar_x1,
                bar_y,
                bar_x1 + (bar_x2 - bar_x1) * fraction,
                bar_y + bar_height,
                theme.INK,
                tags="map_progress",
            )

    def _marker(self, radius: float, ring: str):
        px = self.app.px
        return self.app.sprites.photo(
            ("marker", ring, round(radius, 2)),
            lambda: marker_png(radius, ring, px(2.2), theme.PAPER, px(1.6)),
        )

    def _badge(self, sx: float, sy: float, number: int, tags: str | tuple[str, ...]) -> None:
        """Numéro d'un mouvement presque périodique, dans un carré cadré d'encre."""

        app = self.app
        half = round(app.px(9))
        left, top = round(sx) - half, round(sy) - half
        draw_box(app.canvas, left, top, left + 2 * half, top + 2 * half, theme.PAPER, theme.INK, app.layout.line, tags=tags)
        app.canvas.create_text(
            left + half, top + half, text=str(number), fill=theme.INK, font=app.fonts["key"], tags=tags
        )

    def draw_markers(self) -> None:
        """Couples choisis pour les pendules, et candidats périodiques."""

        app = self.app
        canvas, px, fonts = app.canvas, app.px, app.fonts
        canvas.delete("map_markers")
        if self.job is None:
            return
        # Seuls les candidats listés dans le panneau sont repérés sur la carte.
        for number, candidate in enumerate(self._candidates()[: self._listed], start=1):
            sx, sy = self._to_screen(candidate.theta1, candidate.theta2)
            self._badge(sx, sy, number, "map_markers")

        selected = app.definitions[app.selected]
        order = [i for i in range(len(app.definitions)) if i != app.selected] + [app.selected]
        for index in order:
            definition = app.definitions[index]
            if definition.parameters() != selected.parameters():
                continue
            chosen = index == app.selected
            sx, sy = self._to_screen(definition.theta1_degrees, definition.theta2_degrees)
            radius = px(9) if chosen else px(6.5)
            canvas.create_image(
                sx, sy, image=self._marker(radius, definition.color2), tags="map_markers"
            )
            if not chosen:
                continue
            font = fonts["mark"]
            width = round(font.measure(definition.name) + px(12))
            height = round(font.metrics("linespace") + px(4))
            x, _, size = self.plot
            side = -1 if sx + radius + px(6) + width > x + size else 1
            label_x = sx + side * (radius + px(4) + width / 2)
            canvas.create_image(label_x, sy, image=app.veil(width, height, 0.85), tags="map_markers")
            canvas.create_text(
                label_x,
                sy,
                text=definition.name,
                fill=blend(definition.color2, theme.INK, 0.8),
                font=font,
                tags="map_markers",
            )

    def _candidates(self) -> list[Candidate]:
        if self.kind != PERIODICITY or self.job is None:
            return []
        return self.job.candidates or []

    def _value_at(self, theta1: float, theta2: float) -> tuple[float, float | None] | None:
        job = self.job
        if job is None:
            return None
        count = job.resolution
        column = min(count - 1, int((wrap_degrees(theta1) + 180) / 360 * count))
        row = min(count - 1, int((wrap_degrees(theta2) + 180) / 360 * count))
        value = float(job.values[row, column])
        if math.isnan(value):
            return None
        period = float(job.periods[row, column]) if job.periods is not None else None
        return value, period

    def _draw_panel(self) -> None:
        app = self.app
        canvas, px, fonts = app.canvas, app.px, app.fonts
        canvas.delete("map_panel")
        x1, y, x2, y2 = self.panel
        width = x2 - x1
        tags = "map_panel"

        def overline(text: str, top: float) -> float:
            return app.overline(x1, top, x2, text, tags) + px(10)

        # Légende : dégradé cadré d'encre et graduations.
        y = overline("ÉCHELLE", y)
        ramp = RAMPS[self.kind]
        steps = 64
        bar_height = round(px(10))
        y = round(y)
        for step in range(steps):
            left = round(x1 + step * width / steps)
            right = round(x1 + (step + 1) * width / steps)
            color = "#%02x%02x%02x" % tuple(ramp[round(step / (steps - 1) * 255)])
            canvas.create_rectangle(left, y, right, y + bar_height, fill=color, outline="", tags=tags)
        draw_box(canvas, x1, y, x2, y + bar_height, None, theme.INK, max(1, round(px(1))), tags=tags)
        if self.kind == DIVERGENCE:
            limit = self._limit
            ticks = ((0, "0"), (0.5, format_value(limit / 2)), (1, f"{format_value(limit)} s⁻¹"))
            ends = ("régulier", "chaotique")
        else:
            ticks = ((0, "≥ 30 %"), (0.5, "3 %"), (1, "≤ 0,3 %"))
            ends = ("irrégulier", "périodique")
        label_y = y + bar_height + px(4)
        for position, label in ticks:
            anchor = "nw" if position == 0 else "ne" if position == 1 else "n"
            canvas.create_text(
                x1 + position * width, label_y, text=label, anchor=anchor, fill=theme.MUTED, font=fonts["axis"], tags=tags
            )
        y = label_y + fonts["axis"].metrics("linespace") + px(1)
        canvas.create_text(x1, y, text=ends[0], anchor="nw", fill=theme.HELPER, font=fonts["small"], tags=tags)
        canvas.create_text(x2, y, text=ends[1], anchor="ne", fill=theme.HELPER, font=fonts["small"], tags=tags)
        y += fonts["small"].metrics("linespace") + px(22)

        # Valeur au couple du pendule sélectionné.
        definition = app.definitions[app.selected]
        y = overline(definition.name.upper(), y)
        angles = (
            f"θ₁ {format_value(definition.theta1_degrees, '°')} · "
            f"θ₂ {format_value(definition.theta2_degrees, '°')}"
        )
        reading = self._value_at(definition.theta1_degrees, definition.theta2_degrees)
        headline, detail = describe(self.kind, *reading) if reading else ("calcul en cours", "")
        for text, font, color in (
            (headline, fonts["card_title"], theme.INK),
            (detail, fonts["label"], theme.MUTED),
            (angles, fonts["label"], theme.HELPER),
        ):
            if not text:
                continue
            item = canvas.create_text(
                x1, y, text=text, anchor="nw", width=width, fill=color, font=font, tags=tags
            )
            y = canvas.bbox(item)[3] + px(2)
        y += px(20)

        if self.kind == DIVERGENCE:
            y = overline("INTERPRÉTATION", y)
            for text in (
                "λ ≈ 0 : les deux pendules restent groupés, le mouvement est régulier.",
                "λ = 1 s⁻¹ : leur écart est multiplié par e ≈ 2,7 à chaque seconde.",
            ):
                item = canvas.create_text(
                    x1, y, text=text, anchor="nw", width=width, fill=theme.MUTED, font=fonts["label"], tags=tags
                )
                y = canvas.bbox(item)[3] + px(6)
        else:
            y = overline("PRESQUE PÉRIODIQUES", y)
            candidates = self._candidates()
            if self.job is not None and (self.job.refining or self.job.completed < self.job.total):
                canvas.create_text(
                    x1, y, text="Recherche en cours…", anchor="nw", fill=theme.MUTED, font=fonts["label"], tags=tags
                )
            row_height = fonts["label"].metrics("linespace") * 2 + px(8)
            footer = fonts["small"].metrics("linespace") * 2 + px(12)
            self._listed = 0
            for number, candidate in enumerate(candidates, start=1):
                if y + row_height > y2 - footer:
                    break
                self._draw_candidate(number, candidate, x1, y, x2, row_height)
                self._listed = number
                y += row_height + px(4)

        canvas.create_text(
            x1,
            y2,
            text="Clic : appliquer au pendule sélectionné\nDouble-clic : appliquer et lancer",
            anchor="sw",
            width=width,
            fill=theme.HELPER,
            font=fonts["small"],
            tags=tags,
        )

    def _draw_candidate(
        self, number: int, candidate: Candidate, x1: float, y: float, x2: float, height: float
    ) -> None:
        app = self.app
        canvas, px, fonts = app.canvas, app.px, app.fonts
        tags = ("map_panel", f"candidate:{number - 1}")
        draw_box(canvas, x1, y, x2, y + height, theme.PAPER, tags=tags)
        center_y = y + height / 2
        self._badge(x1 + px(15), center_y, number, tags)
        text_x = x1 + px(34)
        line = fonts["label"].metrics("linespace")
        canvas.create_text(
            text_x,
            center_y - line / 2 - px(1),
            text=(
                f"θ₁ {format_value(candidate.theta1, '°')}   "
                f"θ₂ {format_value(candidate.theta2, '°')}"
            ),
            anchor="w",
            fill=theme.INK,
            font=fonts["label"],
            tags=tags,
        )
        canvas.create_text(
            text_x,
            center_y + line / 2 + px(1),
            text=f"période ≈ {format_value(round(candidate.period, 2))} s",
            anchor="w",
            fill=theme.MUTED,
            font=fonts["label"],
            tags=tags,
        )
        canvas.create_text(
            x2 - px(10),
            center_y - line / 2 - px(1),
            text=format_percent(candidate.distance),
            anchor="e",
            fill=theme.INK,
            font=fonts["mark"],
            tags=tags,
        )

    def _draw_hover(self) -> None:
        app = self.app
        canvas, px, fonts = app.canvas, app.px, app.fonts
        canvas.delete("map_hover")
        if self._hover is None or self.job is None:
            return
        row, column = self._hover
        value = float(self.job.values[row, column])
        if math.isnan(value):
            return
        x, y, size = self.plot
        count = self.job.resolution
        left = x + column * size / count
        top = y + (count - 1 - row) * size / count
        cell = size / count
        # Cadre d'encre doublé de papier : visible sur les zones sombres comme claires.
        line = app.layout.line
        draw_box(canvas, left - 2 * line, top - 2 * line, left + cell + 2 * line, top + cell + 2 * line, None, theme.PAPER, line, tags="map_hover")
        draw_box(canvas, left - line, top - line, left + cell + line, top + cell + line, None, theme.INK, line, tags="map_hover")
        period = float(self.job.periods[row, column]) if self.job.periods is not None else None
        headline, detail = describe(self.kind, value, period)
        axis = self.job.axis
        angles = f"θ₁ {format_value(round(float(axis[column]), 2), '°')} · θ₂ {format_value(round(float(axis[row]), 2), '°')}"
        lines = [(headline, fonts["tag"], theme.INK)]
        if detail:
            lines.append((detail, fonts["label"], theme.MUTED))
        lines.append((angles, fonts["label"], theme.HELPER))
        width = max(font.measure(text) for text, font, _ in lines) + px(24)
        height = sum(font.metrics("linespace") for _, font, _ in lines) + px(16)
        tip_x = left + cell + px(12)
        if tip_x + width > app.layout.stage[2]:
            tip_x = left - px(12) - width
        tip_y = min(max(top - height / 2, app.layout.stage[1]), app.layout.stage[3] - height)
        draw_box(
            canvas,
            tip_x,
            tip_y,
            tip_x + width,
            tip_y + height,
            theme.PAPER,
            theme.INK,
            line,
            tags="map_hover",
        )
        text_y = tip_y + px(8)
        for text, font, color in lines:
            canvas.create_text(
                tip_x + px(12), text_y, text=text, anchor="nw", fill=color, font=font, tags="map_hover"
            )
            text_y += font.metrics("linespace")

    # -- interactions ----------------------------------------------------

    def motion(self, sx: float, sy: float) -> str:
        cell = self._cell(sx, sy)
        if cell != self._hover:
            self._hover = cell
            self._draw_hover()
        return "crosshair" if cell is not None else ""

    def leave(self) -> None:
        if self._hover is not None:
            self._hover = None
            self._draw_hover()

    def press(self, sx: float, sy: float) -> bool:
        angles = self._to_angles(sx, sy)
        if angles is None:
            return False
        theta1, theta2 = (round(value / CLICK_STEP) * CLICK_STEP for value in angles)
        self.app.set_angles(self.app.selected, theta1, theta2, announce=True)
        return True

    def apply_candidate(self, index: int) -> None:
        candidates = self._candidates()
        if 0 <= index < len(candidates):
            candidate = candidates[index]
            self.app.set_angles(self.app.selected, candidate.theta1, candidate.theta2, announce=True)
