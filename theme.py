"""Palette et conventions d'affichage de l'application.

Reprend la direction artistique du portfolio (pierrehlr.github.io) : papier
crème, encre, quatre couleurs mates, titres à chasse fixe en majuscules,
filets d'encre et angles droits, sans dégradé, ombre ni halo.
"""

# Papier et encre
BACKGROUND = "#ebe6dc"  # papier crème
PAPER = "#f3efe7"  # cartes et cadres, un ton plus clair
PAPER_DARK = "#e2dccf"  # champs modifiables, cellules pas encore calculées
INK = "#1b1b1b"  # texte, filets, cadres, tiges
MUTED = "#4d4a45"
HELPER = "#6b665e"
LINE_SOFT = "#cfc8ba"  # séparateurs discrets, graduations fines

# Les quatre couleurs de la bande du portfolio
SAGE = "#849586"
TERRACOTTA = "#d6743f"
BRICK = "#ab2317"
SLATE = "#44738c"
BAND = (SAGE, TERRACOTTA, BRICK, SLATE)

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
