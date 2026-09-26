"""Modèle physique d'un double pendule plan.

Les angles sont mesurés depuis la verticale descendante. Les tiges sont
supposées sans masse et les deux masses ponctuelles.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, sin


def wrap_degrees(angle: float) -> float:
    """Ramène un angle dans ]−180°, 180°]."""

    if -180 < angle <= 180:
        return float(angle)  # Évite le bruit d'arrondi de l'aller-retour modulo.
    wrapped = (angle + 180) % 360 - 180
    return 180.0 if wrapped == -180 else float(wrapped)


@dataclass(frozen=True)
class PendulumParameters:
    """Paramètres physiques de la simulation (unités SI)."""

    mass1: float = 1.0
    mass2: float = 1.0
    length1: float = 1.0
    length2: float = 1.0
    gravity: float = 9.81
    damping: float = 0.0

    def validate(self) -> None:
        positive = {
            "Masse 1": self.mass1,
            "Masse 2": self.mass2,
            "Longueur 1": self.length1,
            "Longueur 2": self.length2,
            "Gravité": self.gravity,
        }
        for label, value in positive.items():
            if value <= 0:
                raise ValueError(f"{label} doit être strictement positif.")
        if self.damping < 0:
            raise ValueError("L'amortissement ne peut pas être négatif.")


@dataclass
class PendulumState:
    """Etat dynamique : angles et vitesses angulaires en radians."""

    theta1: float
    omega1: float
    theta2: float
    omega2: float


class DoublePendulum:
    """Intégrateur RK4 pour les équations couplées du double pendule."""

    def __init__(self, parameters: PendulumParameters, state: PendulumState):
        parameters.validate()
        self.parameters = parameters
        self.state = state
        self.time = 0.0

    def derivatives(self, state: PendulumState) -> tuple[float, float, float, float]:
        p = self.parameters
        t1, w1, t2, w2 = (
            state.theta1,
            state.omega1,
            state.theta2,
            state.omega2,
        )
        delta = t1 - t2
        common = 2 * p.mass1 + p.mass2 - p.mass2 * cos(2 * delta)

        alpha1 = (
            -p.gravity * (2 * p.mass1 + p.mass2) * sin(t1)
            - p.mass2 * p.gravity * sin(t1 - 2 * t2)
            - 2
            * p.mass2
            * sin(delta)
            * (w2 * w2 * p.length2 + w1 * w1 * p.length1 * cos(delta))
        ) / (p.length1 * common)

        alpha2 = (
            2
            * sin(delta)
            * (
                w1 * w1 * p.length1 * (p.mass1 + p.mass2)
                + p.gravity * (p.mass1 + p.mass2) * cos(t1)
                + w2 * w2 * p.length2 * p.mass2 * cos(delta)
            )
        ) / (p.length2 * common)

        alpha1 -= p.damping * w1
        alpha2 -= p.damping * w2
        return w1, alpha1, w2, alpha2

    @staticmethod
    def _offset(state: PendulumState, derivative: tuple[float, ...], scale: float) -> PendulumState:
        return PendulumState(
            state.theta1 + derivative[0] * scale,
            state.omega1 + derivative[1] * scale,
            state.theta2 + derivative[2] * scale,
            state.omega2 + derivative[3] * scale,
        )

    def step(self, dt: float) -> PendulumState:
        """Avance la simulation de ``dt`` secondes avec Runge-Kutta 4."""

        if dt <= 0:
            raise ValueError("Le pas de temps doit être strictement positif.")
        current = self.state
        k1 = self.derivatives(current)
        k2 = self.derivatives(self._offset(current, k1, dt / 2))
        k3 = self.derivatives(self._offset(current, k2, dt / 2))
        k4 = self.derivatives(self._offset(current, k3, dt))
        values = [
            getattr(current, name)
            + dt * (k1[i] + 2 * k2[i] + 2 * k3[i] + k4[i]) / 6
            for i, name in enumerate(("theta1", "omega1", "theta2", "omega2"))
        ]
        self.state = PendulumState(*values)
        self.time += dt
        return self.state

    def positions(self) -> tuple[float, float, float, float]:
        """Retourne les positions cartésiennes x1, y1, x2, y2."""

        p, s = self.parameters, self.state
        x1 = p.length1 * sin(s.theta1)
        y1 = p.length1 * cos(s.theta1)
        x2 = x1 + p.length2 * sin(s.theta2)
        y2 = y1 + p.length2 * cos(s.theta2)
        return x1, y1, x2, y2

    def energy(self) -> float:
        """Energie mécanique totale, avec zéro au point de suspension."""

        p, s = self.parameters, self.state
        v1_sq = (p.length1 * s.omega1) ** 2
        v2_sq = (
            v1_sq
            + (p.length2 * s.omega2) ** 2
            + 2 * p.length1 * p.length2 * s.omega1 * s.omega2 * cos(s.theta1 - s.theta2)
        )
        kinetic = 0.5 * p.mass1 * v1_sq + 0.5 * p.mass2 * v2_sq
        potential = -(
            (p.mass1 + p.mass2) * p.gravity * p.length1 * cos(s.theta1)
            + p.mass2 * p.gravity * p.length2 * cos(s.theta2)
        )
        return kinetic + potential
