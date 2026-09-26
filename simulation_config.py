"""Configuration modifiable de la simulation.

Ajoutez, retirez ou modifiez les entrées de ``PENDULUMS`` pour choisir les
doubles pendules affichés. Les angles et vitesses initiales sont en degrés.
Les angles indiqués ici sont ceux proposés au démarrage : ils se modifient
ensuite dans l'application, avant de lancer la simulation. Longueurs, masses,
gravité, amortissement et couleurs ne se changent qu'ici.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from double_pendulum import PendulumParameters, PendulumState


@dataclass(frozen=True)
class PendulumDefinition:
    name: str
    mass1: float = 1.0
    mass2: float = 1.0
    length1: float = 1.0
    length2: float = 1.0
    gravity: float = 9.81
    damping: float = 0.01
    theta1_degrees: float = 120.0
    theta2_degrees: float = -10.0
    omega1_degrees: float = 0.0
    omega2_degrees: float = 0.0
    color1: str = "#58c4dd"
    color2: str = "#ff6b6b"
    rod_color: str = "#d8dee9"

    def parameters(self) -> PendulumParameters:
        return PendulumParameters(
            mass1=self.mass1,
            mass2=self.mass2,
            length1=self.length1,
            length2=self.length2,
            gravity=self.gravity,
            damping=self.damping,
        )

    def initial_state(self) -> PendulumState:
        return PendulumState(
            theta1=math.radians(self.theta1_degrees),
            omega1=math.radians(self.omega1_degrees),
            theta2=math.radians(self.theta2_degrees),
            omega2=math.radians(self.omega2_degrees),
        )


@dataclass(frozen=True)
class SimulationSettings:
    time_step: float = 0.005
    playback_speed: float = 1.0
    trail_points: int = 220
    # Cartes de divergence et de périodicité : grille (paire) de couples
    # (θ₁, θ₂), durée simulée pour chaque couple et pas de calcul.
    map_resolution: int = 160
    map_duration: float = 15.0
    map_time_step: float = 0.01


SETTINGS = SimulationSettings()


# Les deux exemples ne diffèrent que de 0,5° sur θ₁ : leur divergence rend
# le comportement chaotique visible. Supprimez la seconde entrée pour ne garder
# qu'un seul double pendule, ou dupliquez-en une pour en ajouter d'autres.
PENDULUMS = (
    PendulumDefinition(
        name="Pendule A",
        length1=1.0,
        length2=1.0,
        theta1_degrees=120.0,
        theta2_degrees=-10.0,
        color1="#58c4dd",
        color2="#ff6b6b",
    ),
    PendulumDefinition(
        name="Pendule B",
        length1=1.0,
        length2=1.0,
        theta1_degrees=120.5,
        theta2_degrees=-10.0,
        color1="#83c167",
        color2="#f9c74f",
        rod_color="#b8c1d1",
    ),
)


def validate_configuration() -> None:
    if not PENDULUMS:
        raise ValueError("PENDULUMS doit contenir au moins un double pendule.")
    if SETTINGS.time_step <= 0 or SETTINGS.playback_speed <= 0:
        raise ValueError("Le pas de temps et la vitesse doivent être positifs.")
    if SETTINGS.trail_points < 2:
        raise ValueError("La traînée doit compter au moins deux points.")
    if SETTINGS.map_resolution < 16 or SETTINGS.map_resolution % 2:
        raise ValueError("La résolution des cartes doit être un nombre pair d'au moins 16.")
    if SETTINGS.map_duration <= 0 or SETTINGS.map_time_step <= 0:
        raise ValueError("La durée et le pas des cartes doivent être positifs.")
    names: set[str] = set()
    for definition in PENDULUMS:
        if not definition.name.strip():
            raise ValueError("Chaque pendule doit avoir un nom.")
        if definition.name in names:
            raise ValueError(f"Nom de pendule dupliqué : {definition.name}")
        names.add(definition.name)
        definition.parameters().validate()


validate_configuration()
