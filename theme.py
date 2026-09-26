"""Palette et conventions d'affichage de l'application."""

BACKGROUND = "#070b18"
STAGE_GLOW = "#111b36"
SURFACE = "#0c1326"
SURFACE_RAISED = "#141d36"
KEY_EDGE = "#04070f"
BORDER = "#1b2542"
BORDER_STRONG = "#2a3659"

GRID = "#121b33"
GRID_STRONG = "#1c2744"
TICK = "#2e3a5c"
SUPPORT = "#3d4a6d"
ROD_SHADOW = "#1c2540"

TEXT = "#eef1f8"
MUTED = "#8c96b0"
FAINT = "#56617f"

ACCENT = "#8ab4ff"
GREEN = "#83c167"
AMBER = "#f9c74f"
RED = "#ff6b6b"

TITLE = "Double pendule"
TITLE_PLURAL = "Doubles pendules"
SUBTITLE = "Une trajectoire déterministe, un mouvement chaotique"


def format_value(value: float, unit: str = "") -> str:
    """Nombre à la française : virgule décimale et vrai signe moins."""

    text = f"{value + 0.0:g}".replace("-", "−").replace(".", ",")  # + 0.0 : pas de « −0 »
    if not unit or unit == "°":
        return text + unit
    return f"{text} {unit}"


def format_clock(seconds: float) -> str:
    return f"t = {seconds:.2f} s".replace(".", ",")
