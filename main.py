"""Visualisation interactive d'un ou plusieurs doubles pendules.

Au lancement, l'application est en mode réglage : on choisit les angles de
départ de chaque pendule (en faisant glisser les masses, au clavier, dans les
cartes du panneau ou en cliquant sur une carte de chaleur), puis Espace lance
la simulation. Longueurs, masses et couleurs ne se modifient que dans
``simulation_config.py``.

Trois vues partagent la scène (touches 1, 2 et 3) : les pendules, la carte de
divergence et la carte de périodicité.

Le canvas est reconstruit à chaque changement de taille ou de vue ; à chaque
image, seuls les éléments mobiles et le chronomètre sont mis à jour.
"""

from __future__ import annotations

import math
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from collections import deque
from dataclasses import dataclass, replace

from canvas_graphics import (
    SpriteCache,
    app_icon_png,
    blend,
    capsule_png,
    dot_png,
    draw_rounded_rect,
    enable_high_dpi,
    hub_png,
    mass_png,
    pulse_png,
    style_title_bar,
    tracked,
)
from double_pendulum import DoublePendulum, wrap_degrees
from simulation_config import PENDULUMS, SETTINGS, PendulumDefinition
import theme
from theme import format_clock, format_value

try:
    import chaos_maps
    from map_view import MapView
except ModuleNotFoundError as error:  # NumPy absent : les cartes sont désactivées.
    if error.name != "numpy":
        raise
    chaos_maps = MapView = None

TRAIL_SEGMENTS = 24
PULSE_FRAMES = 20
PULSE_PERIOD = 1.6
TOAST_DURATION = 2.2
DRAG_STEP = 0.5
FINE_STEP = 0.1
COARSE_STEP = 10.0
SHIFT_MASK = 0x0001
CONTROL_MASK = 0x0004

PENDULUM_VIEW = "pendulums"
VIEWS = (
    (PENDULUM_VIEW, "Pendules"),
    ("divergence", "Divergence"),
    ("periodicity", "Périodicité"),
)


def parse_angle(text: str) -> float | None:
    """Lit un angle saisi (« 120,5 », « -10° », « −3 »…)."""

    cleaned = (
        text.strip()
        .replace("°", "")
        .replace("−", "-")
        .replace(",", ".")
        .replace(" ", "")
        .replace(" ", "")
    )
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def normalize_angle(angle: float) -> float:
    """Angle dans ]−180°, 180°], arrondi au centième de degré."""

    value = round(wrap_degrees(angle), 2) + 0.0
    return 180.0 if value == -180 else value


# Touches de vue reconnues par leur position (claviers AZERTY compris) :
# symboles produits sans Maj, pavé numérique et codes de touche Windows.
VIEW_KEYSYMS = {
    "1": 0, "ampersand": 0, "KP_1": 0, "KP_End": 0,
    "2": 1, "eacute": 1, "KP_2": 1, "KP_Down": 1,
    "3": 2, "quotedbl": 2, "KP_3": 2, "KP_Next": 2,
}
VIEW_KEYCODES = {0x31: 0, 0x32: 1, 0x33: 2, 0x61: 0, 0x62: 1, 0x63: 2}


def angle_step(event: tk.Event) -> float:
    """Pas d'un réglage : 1°, 0,1° avec Maj, 10° avec Ctrl."""

    if event.state & SHIFT_MASK:
        return FINE_STEP
    if event.state & CONTROL_MASK:
        return COARSE_STEP
    return 1.0


@dataclass
class ActivePendulum:
    definition: PendulumDefinition
    model: DoublePendulum
    trail: deque[tuple[float, float]]


@dataclass
class PendulumItems:
    """Identifiants des éléments mobiles d'un pendule sur le canvas."""

    trail: list[int]
    trail_shown: list[bool]
    rod_shadows: tuple[int, int]
    rods: tuple[int, int]
    masses: tuple[int, int]
    label: int
    label_back: int
    label_size: tuple[float, float]
    mass_radii: tuple[float, float]


@dataclass(frozen=True)
class Layout:
    width: int
    height: int
    margin: float
    header_height: float
    tabs_top: float
    sidebar: tuple[float, float, float, float]
    stage: tuple[float, float, float, float]
    command_top: float
    origin: tuple[float, float]
    reach: float
    scale: float


class DoublePendulumApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        # Facteur d'échelle de l'écran : 1 à 96 ppp, 1,75 à 175 %, etc.
        self.ui = root.winfo_fpixels("1i") / 96
        self.sprites = SpriteCache(root)
        self.fonts = self._create_fonts()

        self.root.title("Double pendule — exploration du chaos")
        self._place_window(1280, 800)
        self.root.minsize(round(self.px(960)), round(self.px(640)))
        self.root.configure(bg=theme.BACKGROUND)
        self._set_icon()
        style_title_bar(self.root, theme.BACKGROUND, theme.MUTED, theme.BORDER)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

        self.canvas = tk.Canvas(
            root, bg=theme.BACKGROUND, highlightthickness=0, borderwidth=0
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_configure)

        self.definitions: list[PendulumDefinition] = list(PENDULUMS)
        self.selected = 0
        self.setup = True
        self.paused = False
        self.view = PENDULUM_VIEW
        self.commands_visible = True
        self.trails_visible = True
        self.fullscreen = False
        self.accumulator = 0.0
        self.last_clock = time.perf_counter()
        self.pendulums: list[ActivePendulum] = []
        self.maps = chaos_maps.MapService() if chaos_maps else None
        self.map_views = (
            {kind: MapView(self, kind) for kind, _ in VIEWS[1:]} if MapView else {}
        )

        self.layout: Layout | None = None
        self.items: list[PendulumItems] = []
        self._canvas_size = (0, 0)
        self._rebuild_pending = False
        self._trail_chunk = max(1, math.ceil(SETTINGS.trail_points / TRAIL_SEGMENTS))
        self._time_item: int | None = None
        self._clock_length = 0
        self._pulse_item: int | None = None
        self._pulse_index = -1
        self._toast: tuple[str, str, float] | None = None
        self._title_right = 0.0
        self._status_left = 0.0
        self._overlay_drawn = False
        self._drag: tuple[int, int] | None = None
        self._cursor = ""
        self._field_boxes: dict[tuple[int, int], tuple[float, float, float, float, str]] = {}
        self._editor: tk.Entry | None = None
        self._editor_window: int | None = None
        self._editor_target: tuple[int, int] | None = None

        self._bind_shortcuts()
        self._bind_mouse()
        self._reset_models()
        self.root.after(16, self._tick)

    # ------------------------------------------------------------------
    # Mise en place de la fenêtre

    def px(self, value: float) -> float:
        """Convertit une dimension de maquette (à 96 ppp) en pixels réels."""

        return value * self.ui

    def _create_fonts(self) -> dict[str, tkfont.Font]:
        available = set(tkfont.families(self.root))

        def pick(*candidates: tuple[str, str]) -> tuple[str, str]:
            for family, weight in candidates:
                if family in available:
                    return family, weight
            return candidates[-1]

        display = pick(
            ("Segoe UI Variable Display Semib", "normal"),
            ("Segoe UI Semibold", "normal"),
            ("Helvetica", "bold"),
        )
        text = pick(("Segoe UI Variable Text", "normal"), ("Segoe UI", "normal"), ("Helvetica", "normal"))
        strong = pick(
            ("Segoe UI Variable Text Semibold", "normal"),
            ("Segoe UI Semibold", "normal"),
            ("Helvetica", "bold"),
        )
        small_strong = pick(
            ("Segoe UI Variable Small Semibol", "normal"),
            ("Segoe UI Semibold", "normal"),
            ("Helvetica", "bold"),
        )
        mono_strong = pick(
            ("Cascadia Mono SemiBold", "normal"),
            ("Consolas", "bold"),
            ("Courier", "bold"),
        )

        def font(choice: tuple[str, str], size: int) -> tkfont.Font:
            family, weight = choice
            return tkfont.Font(root=self.root, family=family, size=size, weight=weight)

        return {
            "title": font(display, 19),
            "subtitle": font(text, 10),
            "overline": font(small_strong, 8),
            "card_title": font(strong, 11),
            "label": font(text, 9),
            "value": font(display, 13),
            "compact": font(strong, 10),
            "clock": font(mono_strong, 11),
            "status": font(small_strong, 8),
            "key": font(small_strong, 8),
            "hint": font(text, 9),
            "tag": font(strong, 9),
            "tab": font(strong, 9),
            "axis": font(text, 8),
        }

    def _place_window(self, width: float, height: float) -> None:
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        window_width = min(round(self.px(width)), int(screen_width * 0.9))
        window_height = min(round(self.px(height)), int(screen_height * 0.82))
        x = (screen_width - window_width) // 2
        y = max(0, (screen_height - window_height) // 2 - round(self.px(24)))
        self.root.geometry(f"{window_width}x{window_height}+{x}+{y}")

    def _set_icon(self) -> None:
        first = PENDULUMS[0]
        icons = [
            self.sprites.photo(
                ("icon", size),
                lambda size=size: app_icon_png(
                    size,
                    theme.SURFACE_RAISED,
                    theme.AMBER,
                    first.rod_color,
                    first.color1,
                    first.color2,
                ),
            )
            for size in (64, 32)
        ]
        self.root.iconphoto(True, *icons)

    def _bind_shortcuts(self) -> None:
        def bind(sequences: tuple[str, ...], action) -> None:
            for sequence in sequences:
                try:
                    self.root.bind(sequence, lambda event, action=action: action(event) or "break")
                except tk.TclError:
                    pass  # Séquence inconnue sur ce système (ex. ISO_Left_Tab).

        bind(("<space>",), lambda _event: self.primary_action())
        bind(("<Key-r>", "<Key-R>"), lambda _event: self.reset_action())
        bind(("<Key-h>", "<Key-H>"), lambda _event: self.toggle_commands())
        bind(("<Key-t>", "<Key-T>"), lambda _event: self.toggle_trails())
        bind(("<Key-f>", "<Key-F>"), lambda _event: self.toggle_fullscreen())
        bind(("<Escape>",), lambda _event: self.quit())
        bind(("<Tab>",), lambda _event: self.select(self.selected + 1))
        bind(("<Shift-Tab>", "<ISO_Left_Tab>"), lambda _event: self.select(self.selected - 1))
        bind(("<Left>",), lambda event: self.nudge(event, 1, -1))
        bind(("<Right>",), lambda event: self.nudge(event, 1, 1))
        bind(("<Down>",), lambda event: self.nudge(event, 2, -1))
        bind(("<Up>",), lambda event: self.nudge(event, 2, 1))
        self.root.bind("<KeyPress>", self._on_other_key)

    def _on_other_key(self, event: tk.Event) -> str | None:
        """Touches sans raccourci dédié : 1, 2 et 3 choisissent la vue."""

        index = VIEW_KEYSYMS.get(event.keysym)
        if index is None and sys.platform == "win32":
            index = VIEW_KEYCODES.get(event.keycode)
        if index is None:
            return None
        self.set_view(VIEWS[index][0])
        return "break"

    def _bind_mouse(self) -> None:
        canvas = self.canvas
        canvas.bind("<Motion>", self._on_motion)
        canvas.bind("<Leave>", self._on_leave)
        canvas.bind("<ButtonPress-1>", self._on_press)
        canvas.bind("<B1-Motion>", self._on_drag)
        canvas.bind("<ButtonRelease-1>", self._on_release)
        canvas.bind("<Double-Button-1>", self._on_double_click)
        canvas.bind("<MouseWheel>", self._on_wheel)

    # ------------------------------------------------------------------
    # Commandes

    def primary_action(self) -> None:
        if self.setup:
            self.launch()
        else:
            self.toggle_pause()

    def launch(self) -> None:
        """Quitte le réglage et lance la simulation depuis les angles choisis."""

        self._close_editor(commit=True)
        self.setup = False
        self.paused = False
        self._reset_models()
        if self.view != PENDULUM_VIEW:
            self.set_view(PENDULUM_VIEW)
        else:
            self._refresh_mode()

    def enter_setup(self) -> None:
        """Revient à t = 0 avec les angles choisis, prêts à être modifiés."""

        self.setup = True
        self.paused = False
        self._reset_models()
        self._refresh_mode()

    def reset_action(self) -> None:
        if not self.setup:
            self.enter_setup()
            self._show_toast("Réglage des angles de départ", theme.ACCENT)
            return
        self.definitions = list(PENDULUMS)
        self._reset_models()
        self._after_angles_changed()
        self._show_toast("Angles de simulation_config.py rétablis", theme.TEXT)

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        self.last_clock = time.perf_counter()
        self._draw_status()
        self._draw_commands()

    def toggle_commands(self) -> None:
        self.commands_visible = not self.commands_visible
        self._draw_commands()

    def toggle_trails(self) -> None:
        self.trails_visible = not self.trails_visible
        self._update_scene()
        self._draw_commands()
        self._show_toast(
            "Traînées affichées" if self.trails_visible else "Traînées masquées",
            theme.TEXT,
        )

    def toggle_fullscreen(self) -> None:
        self.fullscreen = not self.fullscreen
        self.root.attributes("-fullscreen", self.fullscreen)
        self._draw_commands()

    def set_view(self, view: str) -> None:
        if view == self.view:
            return
        self._close_editor(commit=True)
        self.view = view
        if view != PENDULUM_VIEW and self.maps is not None:
            # Les deux cartes se calculent ensemble : la seconde est prête plus tôt.
            for kind, _ in VIEWS[1:]:
                self.request_map(kind)
        self._rebuild()

    def select(self, index: int) -> None:
        index %= len(self.definitions)
        if index == self.selected:
            return
        previous = self.definitions[self.selected].parameters()
        self.selected = index
        if self.view != PENDULUM_VIEW and self.definitions[index].parameters() != previous:
            self._rebuild()  # Autres longueurs ou masses : autre carte.
            return
        self._draw_sidebar()
        self._update_scene()
        self._refresh_map_overlay()

    def set_angles(
        self, index: int, theta1: float, theta2: float, announce: bool = False
    ) -> None:
        """Change les angles de départ d'un pendule (et revient au réglage)."""

        definition = self.definitions[index]
        theta1 = normalize_angle(theta1)
        theta2 = normalize_angle(theta2)
        self.definitions[index] = replace(
            definition, theta1_degrees=theta1, theta2_degrees=theta2
        )
        self.selected = index
        if self.setup:
            self._reset_models()
            self._after_angles_changed()
        else:
            self.enter_setup()
        if announce:
            self._show_toast(
                f"{definition.name} : θ₁ {format_value(theta1, '°')} · "
                f"θ₂ {format_value(theta2, '°')}",
                theme.ACCENT,
            )

    def nudge(self, event: tk.Event, which: int, direction: int) -> None:
        if not self.setup:
            self._show_toast("R : revenir au réglage pour changer les angles", theme.TEXT)
            return
        definition = self.definitions[self.selected]
        delta = direction * angle_step(event)
        theta1, theta2 = definition.theta1_degrees, definition.theta2_degrees
        if which == 1:
            theta1 += delta
        else:
            theta2 += delta
        self.set_angles(self.selected, theta1, theta2)

    def request_map(self, kind: str):
        if self.maps is None:
            return None
        return self.maps.request(
            kind,
            self.definitions[self.selected].parameters(),
            SETTINGS.map_resolution,
            SETTINGS.map_duration,
            SETTINGS.map_time_step,
        )

    def quit(self) -> None:
        self._close_editor(commit=False)
        if self.maps is not None:
            self.maps.shutdown()
        self.root.destroy()

    def _reset_models(self) -> None:
        self.pendulums = [
            ActivePendulum(
                definition=definition,
                model=DoublePendulum(definition.parameters(), definition.initial_state()),
                trail=deque(maxlen=SETTINGS.trail_points),
            )
            for definition in self.definitions
        ]
        for pendulum in self.pendulums:
            _, _, x2, y2 = pendulum.model.positions()
            pendulum.trail.append((x2, y2))
        self.accumulator = 0.0
        self.last_clock = time.perf_counter()

    def _refresh_mode(self) -> None:
        """Redessine tout ce qui dépend du mode (réglage, lecture, pause)."""

        self._draw_sidebar()
        self._draw_status()
        self._draw_commands()
        self._update_scene()
        self._refresh_map_overlay()

    def _after_angles_changed(self) -> None:
        self._draw_sidebar()
        self._update_scene()
        self._refresh_map_overlay()

    def _refresh_map_overlay(self) -> None:
        view = self.map_views.get(self.view)
        if view is not None and self.layout is not None:
            view.refresh()

    # ------------------------------------------------------------------
    # Boucle d'animation

    def _tick(self) -> None:
        now = time.perf_counter()
        elapsed = min(now - self.last_clock, 0.05)
        self.last_clock = now
        if not self.setup and not self.paused:
            self.accumulator += elapsed * SETTINGS.playback_speed
            steps = 0
            while self.accumulator >= SETTINGS.time_step and steps < 250:
                for pendulum in self.pendulums:
                    pendulum.model.step(SETTINGS.time_step)
                self.accumulator -= SETTINGS.time_step
                steps += 1
            if steps:
                for pendulum in self.pendulums:
                    _, _, x2, y2 = pendulum.model.positions()
                    pendulum.trail.append((x2, y2))
                self._update_scene()
        if self.maps is not None:
            view = self.map_views.get(self.view)
            for job in self.maps.poll():
                if view is not None and view.job is job:
                    view.refresh()
        self._animate_indicators(now)
        self.root.after(16, self._tick)

    def _animate_indicators(self, now: float) -> None:
        canvas = self.canvas
        if self._pulse_item is not None:
            index = int((now % PULSE_PERIOD) / PULSE_PERIOD * PULSE_FRAMES)
            if index != self._pulse_index:
                self._pulse_index = index
                canvas.itemconfigure(self._pulse_item, image=self._pulse_frame(index))
        if self._toast is not None and now > self._toast[2]:
            self._toast = None
            canvas.delete("toast")

    # ------------------------------------------------------------------
    # Construction de la scène

    def _on_configure(self, event: tk.Event) -> None:
        size = (event.width, event.height)
        if size == self._canvas_size:
            return
        self._canvas_size = size
        if not self._rebuild_pending:
            self._rebuild_pending = True
            self.root.after_idle(self._rebuild)

    def _rebuild(self) -> None:
        self._rebuild_pending = False
        width, height = self._canvas_size
        if width < 2 or height < 2:
            return
        self._close_editor(commit=True)
        self.canvas.delete("all")
        self._time_item = self._pulse_item = None
        self._overlay_drawn = False
        self.items = []
        self.layout = self._compute_layout(width, height)
        if self.view == PENDULUM_VIEW:
            self._draw_stage()
            self._create_pendulum_items()
        elif self.view in self.map_views:
            self.map_views[self.view].draw()
        else:
            self._draw_maps_unavailable()
        self._draw_header()
        self._draw_tabs()
        self._draw_sidebar()
        self._draw_status()
        self._draw_commands()
        self._draw_toast()
        self._update_scene()

    def _compute_layout(self, width: int, height: int) -> Layout:
        px = self.px
        margin = px(28)
        header_height = px(100)
        command_top = height - px(20) - px(46)
        sidebar_width = px(300) if width >= px(1100) else px(262)
        sidebar = (margin, header_height, margin + sidebar_width, command_top - px(20))
        tabs_top = header_height - px(12)
        stage = (sidebar[2] + px(28), tabs_top + px(34) + px(18), width - margin, command_top - px(8))
        origin = ((stage[0] + stage[2]) / 2, (stage[1] + stage[3]) / 2)
        reach = max(px(60), min(stage[2] - stage[0], stage[3] - stage[1]) / 2 - px(30))
        longest = max(item.length1 + item.length2 for item in PENDULUMS)
        return Layout(
            width=width,
            height=height,
            margin=margin,
            header_height=header_height,
            tabs_top=tabs_top,
            sidebar=sidebar,
            stage=stage,
            command_top=command_top,
            origin=origin,
            reach=reach,
            scale=reach / longest,
        )

    def _draw_stage(self) -> None:
        """Halo de fond, rapporteur d'angles et support du pivot."""

        canvas, layout, px = self.canvas, self.layout, self.px
        ox, oy = layout.origin
        reach = layout.reach

        glow_radius = reach * 1.6
        steps = 40
        for step in range(steps):
            fraction = step / (steps - 1)
            radius = glow_radius * (1 - fraction) + px(2)
            color = blend(theme.STAGE_GLOW, theme.BACKGROUND, fraction**1.7)
            canvas.create_oval(
                ox - radius, oy - radius, ox + radius, oy + radius, fill=color, outline=""
            )

        line = max(1, round(px(1)))
        lengths = {item.length1 for item in PENDULUMS}
        if len(lengths) == 1:
            inner = lengths.pop() * layout.scale
            canvas.create_oval(
                ox - inner, oy - inner, ox + inner, oy + inner, outline=theme.GRID, width=line
            )
        canvas.create_oval(
            ox - reach, oy - reach, ox + reach, oy + reach, outline=theme.GRID_STRONG, width=line
        )
        canvas.create_line(ox, oy, ox, oy + reach, fill=theme.GRID_STRONG, width=line)

        for degrees in range(0, 360, 5):
            angle = math.radians(degrees)
            major = degrees % 30 == 0
            inner_radius = reach + px(4)
            outer_radius = reach + (px(10) if major else px(6))
            dx, dy = math.sin(angle), math.cos(angle)
            canvas.create_line(
                ox + dx * inner_radius,
                oy + dy * inner_radius,
                ox + dx * outer_radius,
                oy + dy * outer_radius,
                fill=theme.TICK if major else theme.GRID_STRONG,
                width=line,
            )
        axis_font = self.fonts["axis"]
        for degrees, label in ((0, "0°"), (90, "90°"), (180, "180°"), (-90, "−90°")):
            angle = math.radians(degrees)
            distance = (
                reach
                + px(16)
                + abs(math.sin(angle)) * axis_font.measure(label) / 2
                + abs(math.cos(angle)) * axis_font.metrics("linespace") / 2
            )
            canvas.create_text(
                ox + math.sin(angle) * distance,
                oy + math.cos(angle) * distance,
                text=label,
                fill=theme.FAINT,
                font=self.fonts["axis"],
            )

        canvas.create_line(
            ox - px(24), oy, ox + px(24), oy, fill=theme.SUPPORT, width=px(3), capstyle="round"
        )

    def _draw_maps_unavailable(self) -> None:
        x1, y1, x2, y2 = self.layout.stage
        self.canvas.create_text(
            (x1 + x2) / 2,
            (y1 + y2) / 2,
            text=(
                "Les cartes nécessitent NumPy.\n"
                "Installez-le avec : .venv\\Scripts\\python.exe -m pip install -r requirements.txt"
            ),
            justify="center",
            fill=theme.MUTED,
            font=self.fonts["hint"],
        )

    def _mass_radius(self, mass: float) -> float:
        return self.px(max(8, min(17, 8 + 4 * math.sqrt(mass))))

    def _mass_image(self, color: str, radius: float) -> tk.PhotoImage:
        return self.sprites.photo(
            ("mass", color, round(radius, 2)),
            lambda: mass_png(color, radius, radius * 2.5),
        )

    def _create_pendulum_items(self) -> None:
        canvas, px = self.canvas, self.px
        trails: list[list[int]] = []
        for definition in self.definitions:
            segments = []
            # Du plus ancien au plus récent : le tronçon récent passe au-dessus.
            for age in reversed(range(TRAIL_SEGMENTS)):
                freshness = 1 - age / TRAIL_SEGMENTS
                color = blend(definition.color2, theme.BACKGROUND, 0.03 + 0.95 * freshness**2.2)
                segments.append(
                    canvas.create_line(
                        0, 0, 0, 0,
                        fill=color,
                        width=px(0.9 + 2.1 * freshness),
                        capstyle="round",
                        joinstyle="round",
                        state="hidden",
                    )
                )
            segments.reverse()
            trails.append(segments)

        self.items = []
        for definition, segments in zip(self.definitions, trails):
            shadows = tuple(
                canvas.create_line(0, 0, 0, 0, fill=theme.ROD_SHADOW, width=px(7), capstyle="round")
                for _ in range(2)
            )
            rods = tuple(
                canvas.create_line(
                    0, 0, 0, 0, fill=definition.rod_color, width=px(2.6), capstyle="round"
                )
                for _ in range(2)
            )
            radius1 = self._mass_radius(definition.mass1)
            radius2 = self._mass_radius(definition.mass2)
            masses = (
                canvas.create_image(0, 0, image=self._mass_image(definition.color1, radius1)),
                canvas.create_image(0, 0, image=self._mass_image(definition.color2, radius2)),
            )
            font = self.fonts["tag"]
            self.items.append(
                PendulumItems(
                    trail=segments,
                    trail_shown=[False] * TRAIL_SEGMENTS,
                    rod_shadows=shadows,
                    rods=rods,
                    masses=masses,
                    label=0,
                    label_back=0,
                    label_size=(
                        font.measure(definition.name) + px(16),
                        font.metrics("linespace") + px(4),
                    ),
                    mass_radii=(radius1, radius2),
                )
            )

        ox, oy = self.layout.origin
        hub = self.sprites.photo(
            ("hub", round(px(1), 3)),
            lambda: hub_png(theme.AMBER, theme.BACKGROUND, px(6.5), px(2.6)),
        )
        canvas.create_image(ox, oy, image=hub)

        for definition, items in zip(self.definitions, self.items):
            width, height = (round(size) for size in items.label_size)
            back = self.sprites.photo(
                ("capsule", width, height),
                lambda: capsule_png(width, height, theme.BACKGROUND, 0.62),
            )
            items.label_back = canvas.create_image(0, 0, image=back)
            items.label = canvas.create_text(
                0,
                0,
                text=definition.name,
                fill=blend(definition.color2, theme.TEXT, 0.8),
                font=self.fonts["tag"],
            )

    def _draw_header(self) -> None:
        canvas, layout, px, fonts = self.canvas, self.layout, self.px, self.fonts
        title = theme.TITLE if len(PENDULUMS) == 1 else theme.TITLE_PLURAL
        top = px(24)
        canvas.create_text(
            layout.margin,
            top,
            text=title,
            anchor="nw",
            fill=theme.TEXT,
            font=fonts["title"],
        )
        canvas.create_text(
            layout.margin + px(1),
            top + fonts["title"].metrics("linespace") + px(1),
            text=theme.SUBTITLE,
            anchor="nw",
            fill=theme.MUTED,
            font=fonts["subtitle"],
        )
        self._title_right = layout.margin + max(
            fonts["title"].measure(title), fonts["subtitle"].measure(theme.SUBTITLE)
        )

    def _header_center(self) -> float:
        return self.px(24) + self.fonts["title"].metrics("linespace") * 0.62

    def _draw_tabs(self) -> None:
        """Sélecteur de vue en haut de la scène : Pendules, Divergence, Périodicité."""

        canvas, layout, px, fonts = self.canvas, self.layout, self.px, self.fonts
        font, key_font = fonts["tab"], fonts["axis"]
        height = px(34)
        inner = px(4)
        widths = [
            px(14) + key_font.measure(str(number)) + px(8) + font.measure(label) + px(14)
            for number, (_, label) in enumerate(VIEWS, start=1)
        ]
        x = layout.stage[0]
        top = layout.tabs_top
        center_y = top + height / 2
        self._pill(x, center_y, sum(widths) + 2 * inner, height, "tabs")
        x += inner
        for number, ((view, label), width) in enumerate(zip(VIEWS, widths), start=1):
            active = view == self.view
            tags = ("tabs", f"tab:{view}")
            if active:
                draw_rounded_rect(
                    canvas,
                    self.sprites,
                    x,
                    top + inner,
                    x + width,
                    top + height - inner,
                    (height - 2 * inner) / 2,
                    theme.SURFACE_RAISED,
                    theme.BORDER_STRONG,
                    max(1, round(px(1))),
                    tags=tags,
                )
            else:
                canvas.create_rectangle(
                    x, top + inner, x + width, top + height - inner, fill=theme.SURFACE, outline="", tags=tags
                )
            canvas.create_text(
                x + px(14),
                center_y,
                text=str(number),
                anchor="w",
                fill=theme.MUTED if active else theme.FAINT,
                font=key_font,
                tags=tags,
            )
            canvas.create_text(
                x + px(14) + key_font.measure(str(number)) + px(8),
                center_y,
                text=label,
                anchor="w",
                fill=theme.TEXT if active else theme.MUTED,
                font=font,
                tags=tags,
            )
            x += width

    # ------------------------------------------------------------------
    # Panneau de configuration

    def _card_height(self, compact: bool) -> float:
        px, fonts = self.px, self.fonts
        title = fonts["card_title"].metrics("linespace")
        if compact:
            return px(12) * 2 + title + px(6) + 2 * self._compact_line_height()
        row = fonts["label"].metrics("linespace") + px(4) + self._field_height()
        return px(16) * 2 + title + px(24) + 2 * row + px(12)

    def _field_height(self) -> float:
        return self.fonts["value"].metrics("linespace") + self.px(6)

    def _compact_line_height(self) -> float:
        return self.fonts["compact"].metrics("linespace") + self.px(8)

    def _draw_sidebar(self) -> None:
        if self.layout is None:
            return
        canvas, layout, px, fonts = self.canvas, self.layout, self.px, self.fonts
        canvas.delete("sidebar")
        self._field_boxes = {}
        x1, y1, x2, y2 = layout.sidebar
        tags = "sidebar"
        canvas.create_text(
            x1 + px(2),
            y1,
            text=tracked("CONFIGURATION"),
            anchor="nw",
            fill=theme.MUTED,
            font=fonts["overline"],
            tags=tags,
        )
        count = len(self.definitions)
        canvas.create_text(
            x2 - px(2),
            y1,
            text=f"{count} pendule{'s' if count > 1 else ''}",
            anchor="ne",
            fill=theme.FAINT,
            font=fonts["label"],
            tags=tags,
        )
        if self.setup:
            footer_text = (
                "Glissez une masse ou utilisez les flèches (Maj : 0,1°, Ctrl : 10°). "
                "Longueurs et masses : simulation_config.py"
            )
        else:
            footer_text = "Longueurs, masses et couleurs : simulation_config.py"
        footer = canvas.create_text(
            x1 + px(2),
            y2,
            text=footer_text,
            anchor="sw",
            width=x2 - x1 - px(4),
            fill=theme.FAINT,
            font=fonts["axis"],
            tags=tags,
        )

        top = y1 + fonts["overline"].metrics("linespace") + px(12)
        bottom = canvas.bbox(footer)[1] - px(14)
        gap = px(12)
        available = bottom - top
        compact = count * self._card_height(False) + (count - 1) * gap > available
        card_height = self._card_height(compact)
        shown = count
        if count * card_height + (count - 1) * gap > available:
            more_height = fonts["label"].metrics("linespace") + px(4)
            shown = max(0, int((available - more_height + gap) // (card_height + gap)))

        # La carte sélectionnée reste visible même quand la liste est tronquée.
        indices = list(range(shown))
        if shown and self.selected >= shown:
            indices[-1] = self.selected
        y = top
        for index in indices:
            self._draw_card(index, x1, y, x2, card_height, compact)
            y += card_height + gap
        if shown < count:
            hidden = count - shown
            canvas.create_text(
                x1 + px(2),
                y,
                text=f"+ {hidden} autre{'s' if hidden > 1 else ''} pendule{'s' if hidden > 1 else ''} (Tab)",
                anchor="nw",
                fill=theme.FAINT,
                font=fonts["label"],
                tags=tags,
            )

    def _draw_field(
        self,
        index: int,
        which: int,
        box: tuple[float, float, float, float],
        text: str,
        font_name: str,
    ) -> None:
        """Valeur modifiable : un champ discret, cliquable ou réglable à la molette."""

        canvas, px = self.canvas, self.px
        tags = ("sidebar", f"field:{index}:{which}")
        x1, y1, x2, y2 = box
        draw_rounded_rect(
            canvas,
            self.sprites,
            x1,
            y1,
            x2,
            y2,
            px(6),
            theme.SURFACE_RAISED,
            theme.BORDER_STRONG,
            max(1, round(px(1))),
            tags=tags,
        )
        canvas.create_text(
            x1 + px(8),
            (y1 + y2) / 2,
            text=text,
            anchor="w",
            fill=theme.TEXT,
            font=self.fonts[font_name],
            tags=tags,
        )
        self._field_boxes[(index, which)] = (x1, y1, x2, y2, font_name)

    def _draw_card(
        self,
        index: int,
        x1: float,
        y1: float,
        x2: float,
        height: float,
        compact: bool,
    ) -> None:
        canvas, px, fonts = self.canvas, self.px, self.fonts
        definition = self.definitions[index]
        chosen = index == self.selected and len(self.definitions) > 1
        tags = ("sidebar", f"card:{index}")
        border = max(1, round(px(1)))
        draw_rounded_rect(
            canvas,
            self.sprites,
            x1,
            y1,
            x2,
            y1 + height,
            px(12),
            theme.SURFACE,
            blend(theme.ACCENT, theme.SURFACE, 0.7) if chosen else theme.BORDER,
            border,
            tags=tags,
        )
        pad = px(12) if compact else px(16)
        title_height = fonts["card_title"].metrics("linespace")
        center_y = y1 + pad + title_height / 2
        dot_radius = px(5)
        for position, color in enumerate((definition.color1, definition.color2)):
            image = self.sprites.photo(
                ("dot", color, round(dot_radius, 2)), lambda color=color: dot_png(color, dot_radius)
            )
            canvas.create_image(
                x1 + pad + dot_radius + position * px(14), center_y, image=image, tags=tags
            )
        canvas.create_text(
            x1 + pad + px(34),
            center_y,
            text=definition.name,
            anchor="w",
            fill=theme.TEXT,
            font=fonts["card_title"],
            tags=tags,
        )
        if chosen:
            canvas.create_text(
                x2 - pad,
                center_y,
                text="sélectionné",
                anchor="e",
                fill=theme.ACCENT,
                font=fonts["axis"],
                tags=tags,
            )

        angles = (
            (1, format_value(definition.theta1_degrees, "°")),
            (2, format_value(definition.theta2_degrees, "°")),
        )
        length1 = format_value(definition.length1, "m")
        length2 = format_value(definition.length2, "m")
        column_width = (x2 - x1 - 2 * pad) / 2

        if compact:
            line_height = self._compact_line_height()
            font = fonts["compact"]
            cells = (("θ₁", angles[0]), ("θ₂", angles[1]), ("L₁", (0, length1)), ("L₂", (0, length2)))
            for position, (label, (which, value)) in enumerate(cells):
                x = x1 + pad + (position % 2) * column_width
                y = y1 + pad + title_height + px(4) + (position // 2) * line_height
                center = y + line_height / 2
                canvas.create_text(
                    x, center, text=label, anchor="w", fill=theme.MUTED, font=font, tags=tags
                )
                if which and self.setup:
                    box = (x + px(18), center - line_height / 2 + px(2), x + column_width - px(10), center + line_height / 2 - px(2))
                    self._draw_field(index, which, box, value, "compact")
                else:
                    canvas.create_text(
                        x + px(26), center, text=value, anchor="w", fill=theme.TEXT, font=font, tags=tags
                    )
            return

        divider_y = y1 + pad + title_height + px(12)
        canvas.create_line(
            x1 + pad, divider_y, x2 - pad, divider_y, fill=theme.BORDER, width=border, tags=tags
        )
        label_height = fonts["label"].metrics("linespace")
        field_height = self._field_height()
        row_height = label_height + px(4) + field_height + px(12)
        cells = (
            ("θ₁ initial", angles[0]),
            ("θ₂ initial", angles[1]),
            ("Longueur L₁", (0, length1)),
            ("Longueur L₂", (0, length2)),
        )
        for position, (label, (which, value)) in enumerate(cells):
            x = x1 + pad + (position % 2) * column_width
            y = divider_y + px(12) + (position // 2) * row_height
            canvas.create_text(
                x, y, text=label, anchor="nw", fill=theme.MUTED, font=fonts["label"], tags=tags
            )
            field_top = y + label_height + px(4)
            if which and self.setup:
                box = (x - px(8), field_top, x + column_width - px(12), field_top + field_height)
                self._draw_field(index, which, box, value, "value")
            else:
                canvas.create_text(
                    x,
                    field_top + field_height / 2,
                    text=value,
                    anchor="w",
                    fill=theme.TEXT,
                    font=fonts["value"],
                    tags=tags,
                )

    # ------------------------------------------------------------------
    # Saisie d'un angle

    def open_editor(self, index: int, which: int) -> None:
        self._close_editor(commit=True)
        if not self.setup:
            self.enter_setup()
        if index != self.selected:
            self.select(index)
        box = self._field_boxes.get((index, which))
        if box is None:
            return
        x1, y1, x2, y2, font_name = box
        definition = self.definitions[index]
        value = definition.theta1_degrees if which == 1 else definition.theta2_degrees
        px = self.px
        line = max(1, round(px(1)))
        # Le champ prend une bordure d'accent ; le texte saisi reste à sa place.
        draw_rounded_rect(
            self.canvas,
            self.sprites,
            x1,
            y1,
            x2,
            y2,
            px(6),
            theme.SURFACE_RAISED,
            theme.ACCENT,
            line,
            tags="editor",
        )
        entry = tk.Entry(
            self.canvas,
            font=self.fonts[font_name],
            bg=theme.SURFACE_RAISED,
            fg=theme.TEXT,
            insertbackground=theme.TEXT,
            selectbackground=blend(theme.ACCENT, theme.SURFACE_RAISED, 0.45),
            selectforeground=theme.TEXT,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
        )
        entry.insert(0, format_value(value))
        # Sans la fenêtre dans ses bindtags : les raccourcis ne se déclenchent pas pendant la saisie.
        entry.bindtags((str(entry), "Entry", "all"))
        entry.bind("<Return>", lambda _event: self._close_editor(commit=True) or "break")
        entry.bind("<KP_Enter>", lambda _event: self._close_editor(commit=True) or "break")
        entry.bind("<Escape>", lambda _event: self._close_editor(commit=False) or "break")
        entry.bind("<Tab>", lambda _event: self._editor_next(index, which) or "break")
        entry.bind("<FocusOut>", lambda _event: self._close_editor(commit=True))
        self._editor = entry
        self._editor_target = (index, which)
        self._editor_window = self.canvas.create_window(
            x1 + px(8) - 1,
            (y1 + y2) / 2,
            anchor="w",
            width=x2 - x1 - px(14),
            height=y2 - y1 - 2 * line - 2,
            window=entry,
            tags="editor",
        )
        entry.focus_set()
        entry.select_range(0, "end")
        entry.icursor("end")

    def _editor_next(self, index: int, which: int) -> None:
        self._close_editor(commit=True)
        if which == 1:
            self.open_editor(index, 2)
        else:
            self.open_editor((index + 1) % len(self.definitions), 1)

    def _close_editor(self, commit: bool) -> None:
        entry = self._editor
        if entry is None:
            return
        self._editor = None
        text = entry.get()
        index, which = self._editor_target
        self.canvas.delete("editor")
        self._editor_window = None
        entry.destroy()
        self.root.focus_set()
        if not commit:
            return
        value = parse_angle(text)
        if value is None:
            self._show_toast(f"Angle invalide : « {text.strip()} »", theme.RED)
            return
        definition = self.definitions[index]
        theta1 = value if which == 1 else definition.theta1_degrees
        theta2 = value if which == 2 else definition.theta2_degrees
        self.set_angles(index, theta1, theta2)

    # ------------------------------------------------------------------
    # Souris

    def _tagged_item(self, x: float, y: float) -> tuple[str, list[str]] | None:
        """Élément interactif le plus haut sous le pointeur."""

        for item in reversed(self.canvas.find_overlapping(x, y, x, y)):
            for tag in self.canvas.gettags(item):
                kind, _, rest = tag.partition(":")
                if rest and kind in ("tab", "field", "card", "candidate"):
                    return kind, rest.split(":")
        return None

    def _mass_positions(self, index: int) -> tuple[float, float, float, float]:
        ox, oy = self.layout.origin
        scale = self.layout.scale
        x1, y1, x2, y2 = self.pendulums[index].model.positions()
        return ox + x1 * scale, oy + y1 * scale, ox + x2 * scale, oy + y2 * scale

    def _mass_at(self, x: float, y: float) -> tuple[int, int] | None:
        """Masse déplaçable sous le pointeur (le pendule sélectionné d'abord)."""

        if not self.setup or self.view != PENDULUM_VIEW or not self.items:
            return None
        count = len(self.definitions)
        order = [self.selected] + [i for i in reversed(range(count)) if i != self.selected]
        for index in order:
            sx1, sy1, sx2, sy2 = self._mass_positions(index)
            radius1, radius2 = self.items[index].mass_radii
            if math.hypot(x - sx2, y - sy2) <= radius2 + self.px(8):
                return index, 2
            if math.hypot(x - sx1, y - sy1) <= radius1 + self.px(8):
                return index, 1
        return None

    def _set_cursor(self, cursor: str) -> None:
        if cursor != self._cursor:
            self._cursor = cursor
            self.canvas.configure(cursor=cursor)

    def _on_motion(self, event: tk.Event) -> None:
        if self.layout is None:
            return
        hit = self._tagged_item(event.x, event.y)
        cursor = ""
        view = self.map_views.get(self.view)
        if view is not None:
            cursor = view.motion(event.x, event.y)
        if hit is not None:
            cursor = "xterm" if hit[0] == "field" else "hand2"
        elif self._mass_at(event.x, event.y) is not None:
            cursor = "fleur"
        self._set_cursor(cursor)

    def _on_leave(self, _event: tk.Event) -> None:
        view = self.map_views.get(self.view)
        if view is not None:
            view.leave()
        self._set_cursor("")

    def _on_press(self, event: tk.Event) -> None:
        if self.layout is None:
            return
        self._close_editor(commit=True)
        hit = self._tagged_item(event.x, event.y)
        if hit is not None:
            kind, arguments = hit
            if kind == "tab":
                self.set_view(arguments[0])
            elif kind == "field":
                self.open_editor(int(arguments[0]), int(arguments[1]))
            elif kind == "card":
                self.select(int(arguments[0]))
            elif kind == "candidate" and self.view in self.map_views:
                self.map_views[self.view].apply_candidate(int(arguments[0]))
            return
        target = self._mass_at(event.x, event.y)
        if target is not None:
            self._drag = target
            self.select(target[0])
            return
        view = self.map_views.get(self.view)
        if view is not None:
            view.press(event.x, event.y)

    def _on_drag(self, event: tk.Event) -> None:
        if self._drag is None:
            return
        index, which = self._drag
        definition = self.definitions[index]
        if which == 1:
            anchor_x, anchor_y = self.layout.origin
        else:
            anchor_x, anchor_y, _, _ = self._mass_positions(index)
        angle = math.degrees(math.atan2(event.x - anchor_x, event.y - anchor_y))
        step = FINE_STEP if event.state & SHIFT_MASK else DRAG_STEP
        angle = round(angle / step) * step
        theta1 = angle if which == 1 else definition.theta1_degrees
        theta2 = angle if which == 2 else definition.theta2_degrees
        if (normalize_angle(theta1), normalize_angle(theta2)) != (
            definition.theta1_degrees,
            definition.theta2_degrees,
        ):
            self.set_angles(index, theta1, theta2)

    def _on_release(self, _event: tk.Event) -> None:
        self._drag = None

    def _on_double_click(self, event: tk.Event) -> None:
        hit = self._tagged_item(event.x, event.y)
        view = self.map_views.get(self.view)
        on_plot = view is not None and view.contains(event.x, event.y)
        if on_plot or (hit is not None and hit[0] == "candidate"):
            self.launch()

    def _on_wheel(self, event: tk.Event) -> None:
        hit = self._tagged_item(event.x, event.y)
        if hit is None or hit[0] != "field" or not self.setup:
            return
        index, which = int(hit[1][0]), int(hit[1][1])
        definition = self.definitions[index]
        delta = (1 if event.delta > 0 else -1) * angle_step(event)
        theta1 = definition.theta1_degrees + (delta if which == 1 else 0)
        theta2 = definition.theta2_degrees + (delta if which == 2 else 0)
        self.set_angles(index, theta1, theta2)

    # ------------------------------------------------------------------
    # Indicateurs : état et notifications

    def _pulse_frame(self, index: int) -> tk.PhotoImage:
        px = self.px
        return self.sprites.photo(
            ("pulse", index, round(px(1), 3)),
            lambda: pulse_png(theme.GREEN, px(3.6), px(9), index / PULSE_FRAMES),
        )

    def _pill(
        self,
        x1: float,
        center_y: float,
        width: float,
        height: float,
        tags: str,
        fill: str = theme.SURFACE,
        border: str = theme.BORDER,
    ) -> None:
        draw_rounded_rect(
            self.canvas,
            self.sprites,
            x1,
            center_y - height / 2,
            x1 + width,
            center_y + height / 2,
            height / 2,
            fill,
            border,
            max(1, round(self.px(1))),
            tags=tags,
        )

    def _draw_status(self) -> None:
        if self.layout is None:
            return
        canvas, layout, px, fonts = self.canvas, self.layout, self.px, self.fonts
        canvas.delete("status")
        self._pulse_item = None
        self._pulse_index = -1

        center_y = self._header_center()
        height = px(36)
        pad = px(16)
        state_width = max(
            fonts["status"].measure(tracked(text)) for text in ("LECTURE", "PAUSE", "RÉGLAGE")
        )
        clock_text = format_clock(self.pendulums[0].model.time)
        self._clock_length = len(clock_text)
        clock_width = fonts["clock"].measure(clock_text)
        width = pad + px(12) + px(10) + state_width + px(28) + clock_width + pad
        x1 = layout.width - layout.margin - width
        self._status_left = x1
        self._pill(x1, center_y, width, height, "status")

        icon_x = x1 + pad + px(6)
        if self.setup:
            color, label = theme.ACCENT, "RÉGLAGE"
            radius = px(3.6)
            dot = self.sprites.photo(
                ("dot", color, round(radius, 2)), lambda: dot_png(color, radius)
            )
            canvas.create_image(icon_x, center_y, image=dot, tags="status")
        elif self.paused:
            color, label = theme.AMBER, "PAUSE"
            for offset in (-px(2.5), px(2.5)):
                canvas.create_rectangle(
                    round(icon_x + offset - px(1.2)),
                    round(center_y - px(5)),
                    round(icon_x + offset + px(1.2)),
                    round(center_y + px(5)),
                    fill=color,
                    outline="",
                    tags="status",
                )
        else:
            color, label = theme.GREEN, "LECTURE"
            self._pulse_item = canvas.create_image(
                icon_x, center_y, image=self._pulse_frame(0), tags="status"
            )
        canvas.create_text(
            icon_x + px(16),
            center_y,
            text=tracked(label),
            anchor="w",
            fill=color,
            font=fonts["status"],
            tags="status",
        )
        separator_x = icon_x + px(16) + state_width + px(14)
        canvas.create_line(
            separator_x,
            center_y - px(9),
            separator_x,
            center_y + px(9),
            fill=theme.BORDER_STRONG,
            width=max(1, round(px(1))),
            tags="status",
        )
        self._time_item = canvas.create_text(
            x1 + width - pad,
            center_y,
            text=clock_text,
            anchor="e",
            fill=theme.TEXT,
            font=fonts["clock"],
            tags="status",
        )

        self._draw_toast()

    def _show_toast(self, message: str, color: str) -> None:
        self._toast = (message, color, time.perf_counter() + TOAST_DURATION)
        self._draw_toast()

    def _draw_toast(self) -> None:
        """Notification brève, centrée dans l'espace libre de l'en-tête."""

        self.canvas.delete("toast")
        if self._toast is None or self.layout is None:
            return
        canvas, layout, px = self.canvas, self.layout, self.px
        message, color, _ = self._toast
        font = self.fonts["tag"]
        height = px(34)
        width = px(16) + px(8) + px(10) + font.measure(message) + px(18)
        center_x = (self._title_right + self._status_left) / 2
        if self._status_left - self._title_right < width + px(24):
            center_x = layout.width / 2
        x1 = center_x - width / 2
        center_y = self._header_center()
        self._pill(
            x1, center_y, width, height, "toast", theme.SURFACE_RAISED, theme.BORDER_STRONG
        )
        dot_radius = px(3.5)
        dot = self.sprites.photo(
            ("dot", color, round(dot_radius, 2)), lambda: dot_png(color, dot_radius)
        )
        canvas.create_image(x1 + px(16) + dot_radius, center_y, image=dot, tags="toast")
        canvas.create_text(
            x1 + px(16) + px(8) + px(10),
            center_y,
            text=message,
            anchor="w",
            fill=theme.TEXT,
            font=font,
            tags="toast",
        )

    # ------------------------------------------------------------------
    # Barre des commandes

    def _draw_keycap(
        self, x: float, center_y: float, key: str, tags: str, accent: str | None = None
    ) -> float:
        px, font = self.px, self.fonts["key"]
        width = max(px(26), font.measure(key) + px(16))
        height = px(24)
        top = center_y - height / 2 - px(1)
        face, border = theme.SURFACE_RAISED, theme.BORDER_STRONG
        if accent is not None:
            face = blend(accent, theme.SURFACE_RAISED, 0.2)
            border = blend(accent, theme.SURFACE_RAISED, 0.6)
        radius = px(6)
        # Liseré sombre décalé vers le bas : effet de touche en relief.
        draw_rounded_rect(
            self.canvas,
            self.sprites,
            x,
            top + px(2),
            x + width,
            top + height + px(2),
            radius,
            theme.KEY_EDGE,
            tags=tags,
        )
        draw_rounded_rect(
            self.canvas,
            self.sprites,
            x,
            top,
            x + width,
            top + height,
            radius,
            face,
            border,
            max(1, round(px(1))),
            tags=tags,
        )
        self.canvas.create_text(
            x + width / 2,
            top + height / 2,
            text=key,
            fill=theme.TEXT if accent is None else blend(accent, theme.TEXT, 0.5),
            font=font,
            tags=tags,
        )
        return x + width

    def _command_list(self) -> list[tuple[str, str, str | None]]:
        if self.setup:
            commands = [("ESPACE", "lancer", theme.ACCENT), ("← → ↑ ↓", "angles", None)]
            if len(self.definitions) > 1:
                commands.append(("TAB", "pendule suivant", None))
            return commands + [("R", "angles du code", None), ("ÉCHAP", "quitter", None)]
        return [
            ("ESPACE", "lecture" if self.paused else "pause", None),
            ("R", "réglages", None),
            ("T", "traînées", theme.GREEN if self.trails_visible else None),
            ("H", "masquer l'aide", None),
            ("F", "plein écran", theme.GREEN if self.fullscreen else None),
            ("ÉCHAP", "quitter", None),
        ]

    def _draw_commands(self) -> None:
        if self.layout is None:
            return
        canvas, layout, px, fonts = self.canvas, self.layout, self.px, self.fonts
        canvas.delete("commands")
        bar_height = px(46)
        center_y = layout.command_top + bar_height / 2

        if not self.commands_visible:
            right = self._draw_keycap(layout.margin, center_y, "H", "commands")
            canvas.create_text(
                right + px(10),
                center_y,
                text="afficher l'aide",
                anchor="w",
                fill=theme.FAINT,
                font=fonts["hint"],
                tags="commands",
            )
            return

        commands = self._command_list()
        key_font, hint_font = fonts["key"], fonts["hint"]
        pad, label_gap = px(14), px(9)
        available = layout.width - 2 * layout.margin

        def total_width(item_gap: float) -> float:
            widths = [
                max(px(26), key_font.measure(key) + px(16)) + label_gap + hint_font.measure(label)
                for key, label, _ in commands
            ]
            return 2 * pad + sum(widths) + item_gap * (len(widths) - 1)

        item_gap = px(24)
        if total_width(item_gap) > available:
            item_gap = px(12)
        width = min(total_width(item_gap), available)
        x1 = (layout.width - width) / 2
        self._pill(x1, center_y, width, bar_height, "commands")

        x = x1 + pad
        for key, label, accent in commands:
            x = self._draw_keycap(x, center_y, key, "commands", accent)
            canvas.create_text(
                x + label_gap,
                center_y,
                text=label,
                anchor="w",
                fill=theme.MUTED if accent is None else blend(accent, theme.MUTED, 0.55),
                font=hint_font,
                tags="commands",
            )
            x += label_gap + hint_font.measure(label) + item_gap

    # ------------------------------------------------------------------
    # Mise à jour image par image

    def _update_scene(self) -> None:
        if self.layout is None:
            return
        canvas = self.canvas
        if self._time_item is not None:
            clock_text = format_clock(self.pendulums[0].model.time)
            if len(clock_text) != self._clock_length:
                self._draw_status()
            else:
                canvas.itemconfigure(self._time_item, text=clock_text)
        if self.view != PENDULUM_VIEW or not self.items:
            return

        ox, oy = self.layout.origin
        labels: list[list[float]] = []
        for index, (pendulum, items) in enumerate(zip(self.pendulums, self.items)):
            self._update_trail(pendulum, items)
            sx1, sy1, sx2, sy2 = self._mass_positions(index)
            for shadow, rod, start, end in zip(
                items.rod_shadows,
                items.rods,
                ((ox, oy), (sx1, sy1)),
                ((sx1, sy1), (sx2, sy2)),
            ):
                canvas.coords(shadow, *start, *end)
                canvas.coords(rod, *start, *end)
            canvas.coords(items.masses[0], sx1, sy1)
            canvas.coords(items.masses[1], sx2, sy2)

            # L'étiquette s'écarte du pivot, dans le prolongement de la masse.
            dx, dy = sx2 - ox, sy2 - oy
            distance = math.hypot(dx, dy)
            ux, uy = (dx / distance, dy / distance) if distance > 1e-6 else (0.0, 1.0)
            text_width, text_height = items.label_size
            offset = (
                items.mass_radii[1]
                + self.px(4)
                + abs(ux) * text_width / 2
                + abs(uy) * text_height / 2
            )
            labels.append([sx2 + ux * offset, sy2 + uy * offset, text_width, text_height])

        # Décale verticalement chaque étiquette hors de celles déjà placées.
        for index, box in enumerate(labels):
            for _ in range(2 * index):
                for other in labels[:index]:
                    overlap_x = (box[2] + other[2]) / 2 - abs(box[0] - other[0])
                    overlap_y = (box[3] + other[3]) / 2 - abs(box[1] - other[1])
                    if overlap_x > 0 and overlap_y > 0:
                        box[1] += overlap_y if box[1] >= other[1] else -overlap_y
                        break
                else:
                    break
        for items, (x, y, _, _) in zip(self.items, labels):
            canvas.coords(items.label_back, x, y)
            canvas.coords(items.label, x, y)

        self._draw_setup_overlay()

    def _draw_setup_overlay(self) -> None:
        """En réglage : arcs et valeurs des angles du pendule sélectionné."""

        canvas, px = self.canvas, self.px
        if self._overlay_drawn:
            canvas.delete("overlay")
            self._overlay_drawn = False
        if not self.setup:
            return
        definition = self.definitions[self.selected]
        ox, oy = self.layout.origin
        sx1, sy1, _, _ = self._mass_positions(self.selected)
        width = max(1, round(px(2)))
        line = max(1, round(px(1)))
        guide = blend(theme.ACCENT, theme.BACKGROUND, 0.35)
        canvas.create_line(sx1, sy1, sx1, sy1 + px(56), fill=guide, width=line, tags="overlay")
        for (cx, cy), radius, angle, name in (
            ((ox, oy), px(52), definition.theta1_degrees, "θ₁"),
            ((sx1, sy1), px(40), definition.theta2_degrees, "θ₂"),
        ):
            if abs(angle) >= 0.05:
                canvas.create_arc(
                    cx - radius,
                    cy - radius,
                    cx + radius,
                    cy + radius,
                    start=270,
                    extent=angle if abs(angle) < 360 else 359.9,
                    style="arc",
                    outline=theme.ACCENT,
                    width=width,
                    tags="overlay",
                )
            text = f"{name} {format_value(angle, '°')}"
            font = self.fonts["tag"]
            label_width = round(font.measure(text) + px(14))
            label_height = round(font.metrics("linespace") + px(4))
            back = self.sprites.photo(
                ("capsule", label_width, label_height),
                lambda: capsule_png(label_width, label_height, theme.BACKGROUND, 0.72),
            )
            if abs(angle) < 35:
                # Angle étroit : l'étiquette se range du côté opposé à la tige.
                side = -1 if angle >= 0 else 1
                lx = cx + side * (label_width / 2 + px(8))
                ly = cy + radius * 0.9
            else:
                middle = math.radians(angle / 2)
                lx = cx + math.sin(middle) * (radius + px(24))
                ly = cy + math.cos(middle) * (radius + px(24))
            canvas.create_image(lx, ly, image=back, tags="overlay")
            canvas.create_text(lx, ly, text=text, fill=theme.ACCENT, font=font, tags="overlay")
        self._overlay_drawn = True

    def _update_trail(self, pendulum: ActivePendulum, items: PendulumItems) -> None:
        canvas, layout = self.canvas, self.layout
        ox, oy = layout.origin
        scale = layout.scale
        points = list(pendulum.trail) if self.trails_visible else []
        count = len(points)
        chunk = self._trail_chunk
        for age, segment in enumerate(items.trail):
            end = count - age * chunk
            start = max(0, end - chunk - 1)
            shown = end - start >= 2
            if shown:
                coordinates: list[float] = []
                for x, y in points[start:end]:
                    coordinates.append(ox + x * scale)
                    coordinates.append(oy + y * scale)
                canvas.coords(segment, coordinates)
            if shown != items.trail_shown[age]:
                items.trail_shown[age] = shown
                canvas.itemconfigure(segment, state="normal" if shown else "hidden")


def main() -> None:
    enable_high_dpi()
    root = tk.Tk()
    DoublePendulumApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
