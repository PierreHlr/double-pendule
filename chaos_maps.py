"""Cartes de divergence et de périodicité sur le plan des angles initiaux.

Chaque cellule de la grille (θ₁, θ₂) est une simulation complète lancée depuis
le repos. Les calculs sont vectorisés avec NumPy (toutes les cellules d'un lot
avancent ensemble, avec le même schéma RK4 que ``double_pendulum.py``) puis
répartis entre plusieurs processus.

- **Divergence** : exposant de Lyapunov sur une durée finie. Un pendule jumeau
  part à 10⁻⁸ rad près ; leur écart est mesuré puis ramené à sa taille
  initiale à intervalle régulier (méthode de Benettin). Avec λ > 0, l'écart
  entre deux pendules presque identiques croît comme e^{λt}.
- **Périodicité** : plus petit écart relatif entre l'état initial et un état
  ultérieur (angles et vitesses), une fois que le pendule s'en est éloigné. Un
  mouvement exactement périodique repasse par son état initial : l'écart est nul.

Le système est symétrique par réflexion gauche-droite : (θ₁, θ₂) et (−θ₁, −θ₂)
donnent des mouvements miroirs. Seule la moitié de la grille est calculée.
"""

from __future__ import annotations

import hashlib
import math
import os
import queue
import threading
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from double_pendulum import PendulumParameters, wrap_degrees

DIVERGENCE = "divergence"
PERIODICITY = "periodicity"

TWIN_OFFSET = 1e-8
RENORMALIZE_INTERVAL = 0.1
EXIT_FRACTION = 0.5
CHUNK_CELLS = 1280
CANDIDATE_COUNT = 6
CANDIDATE_SPACING = 20.0
CACHE_VERSION = 1
CACHE_DIRECTORY = Path(__file__).resolve().parent / ".cache"


# --------------------------------------------------------------------------
# Intégration vectorisée


class VectorizedPendulum:
    """Même modèle que ``DoublePendulum``, appliqué à des tableaux d'états.

    Un état est un tableau de forme (4, n) : θ₁, ω₁, θ₂, ω₂ pour n pendules.
    """

    def __init__(self, parameters: PendulumParameters):
        parameters.validate()
        self.parameters = parameters
        self._buffers: tuple[np.ndarray, ...] | None = None

    def derivatives(self, state: np.ndarray, out: np.ndarray) -> np.ndarray:
        p = self.parameters
        t1, w1, t2, w2 = state
        s1, c1, s2, c2 = np.sin(t1), np.cos(t1), np.sin(t2), np.cos(t2)
        # sin et cos de θ₁ − θ₂ et de θ₁ − 2θ₂, sans nouvel appel trigonométrique.
        sin_delta = s1 * c2 - c1 * s2
        cos_delta = c1 * c2 + s1 * s2
        sin_shifted = sin_delta * c2 - cos_delta * s2
        common = 2 * (p.mass1 + p.mass2 * sin_delta * sin_delta)
        w1_squared = w1 * w1
        w2_squared = w2 * w2
        total_mass = p.mass1 + p.mass2
        out[0] = w1
        out[1] = (
            -p.gravity * (2 * p.mass1 + p.mass2) * s1
            - p.mass2 * p.gravity * sin_shifted
            - 2 * p.mass2 * sin_delta * (w2_squared * p.length2 + w1_squared * p.length1 * cos_delta)
        ) / (p.length1 * common) - p.damping * w1
        out[2] = w2
        out[3] = (
            2
            * sin_delta
            * (
                w1_squared * p.length1 * total_mass
                + p.gravity * total_mass * c1
                + w2_squared * p.length2 * p.mass2 * cos_delta
            )
        ) / (p.length2 * common) - p.damping * w2
        return out

    def step(self, state: np.ndarray, dt: float) -> None:
        """Avance ``state`` de ``dt`` secondes (Runge-Kutta 4, en place)."""

        if self._buffers is None or self._buffers[0].shape != state.shape:
            self._buffers = tuple(np.empty_like(state) for _ in range(5))
        k1, k2, k3, k4, trial = self._buffers
        self.derivatives(state, k1)
        np.multiply(k1, dt / 2, out=trial)
        trial += state
        self.derivatives(trial, k2)
        np.multiply(k2, dt / 2, out=trial)
        trial += state
        self.derivatives(trial, k3)
        np.multiply(k3, dt, out=trial)
        trial += state
        self.derivatives(trial, k4)
        k2 += k3
        k2 *= 2
        k2 += k1
        k2 += k4
        k2 *= dt / 6
        state += k2


def velocity_scale(parameters: PendulumParameters) -> float:
    """Pulsation propre : rend angles et vitesses angulaires comparables."""

    return math.sqrt(parameters.gravity / ((parameters.length1 + parameters.length2) / 2))


def wrap(angle: np.ndarray) -> np.ndarray:
    """Ramène un angle dans [−π, π[."""

    return (angle + np.pi) % (2 * np.pi) - np.pi


def _rest_state(theta1: np.ndarray, theta2: np.ndarray) -> np.ndarray:
    state = np.zeros((4, theta1.size))
    state[0] = theta1
    state[2] = theta2
    return state


def lyapunov_exponents(
    parameters: PendulumParameters,
    theta1: np.ndarray,
    theta2: np.ndarray,
    duration: float,
    dt: float,
) -> np.ndarray:
    """Exposant de Lyapunov (s⁻¹) de chaque pendule lâché depuis (θ₁, θ₂)."""

    theta1 = np.asarray(theta1, dtype=float).ravel()
    theta2 = np.asarray(theta2, dtype=float).ravel()
    count = theta1.size
    reference = _rest_state(theta1, theta2)
    twin = reference.copy()
    twin[0] += TWIN_OFFSET / math.sqrt(2)
    twin[2] += TWIN_OFFSET / math.sqrt(2)
    # Pendules de référence et jumeaux avancent dans un seul tableau.
    state = np.concatenate((reference, twin), axis=1)
    weights = np.array([1.0, 1 / velocity_scale(parameters), 1.0, 1 / velocity_scale(parameters)])
    weights = weights[:, None]

    model = VectorizedPendulum(parameters)
    steps = max(1, round(duration / dt))
    interval = max(1, round(RENORMALIZE_INTERVAL / dt))
    growth = np.zeros(count)
    for step in range(1, steps + 1):
        model.step(state, dt)
        if step % interval == 0 or step == steps:
            difference = state[:, count:] - state[:, :count]
            distance = np.sqrt(((difference * weights) ** 2).sum(axis=0))
            distance = np.maximum(distance, 1e-300)
            growth += np.log(distance / TWIN_OFFSET)
            state[:, count:] = state[:, :count] + difference * (TWIN_OFFSET / distance)
    return growth / (steps * dt)


def recurrence(
    parameters: PendulumParameters,
    theta1: np.ndarray,
    theta2: np.ndarray,
    duration: float,
    dt: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Meilleur retour vers l'état initial : (écart relatif, instant en s).

    L'écart est la distance dans l'espace des phases (angles modulo 2π,
    vitesses divisées par la pulsation propre) rapportée à la distance entre
    l'état initial et l'équilibre. ``inf`` : aucun retour mesuré.
    """

    theta1 = np.asarray(theta1, dtype=float).ravel()
    theta2 = np.asarray(theta2, dtype=float).ravel()
    state = _rest_state(theta1, theta2)
    reference = np.maximum(wrap(theta1) ** 2 + wrap(theta2) ** 2, 1e-12)
    inverse_omega = 1 / velocity_scale(parameters) ** 2
    exit_threshold = EXIT_FRACTION**2

    model = VectorizedPendulum(parameters)
    steps = max(1, round(duration / dt))
    best = np.full(theta1.size, np.inf)
    moment = np.full(theta1.size, np.nan)
    left = np.zeros(theta1.size, dtype=bool)
    before_previous: np.ndarray | None = None
    previous: np.ndarray | None = None
    for step in range(1, steps + 1):
        model.step(state, dt)
        current = (
            wrap(state[0] - theta1) ** 2
            + wrap(state[2] - theta2) ** 2
            + (state[1] ** 2 + state[3] ** 2) * inverse_omega
        ) / reference
        if before_previous is not None:
            # Minimum local au pas précédent : une parabole passant par les
            # trois derniers points situe le retour entre deux pas de temps.
            curvature = before_previous - 2 * previous + current
            candidate = left & (previous < before_previous) & (previous <= current) & (curvature > 0)
            if candidate.any():
                index = np.flatnonzero(candidate)
                a, b, c = before_previous[index], previous[index], current[index]
                bend = curvature[index]
                value = np.maximum(b - (c - a) ** 2 / (8 * bend), 0.0)
                better = value < best[index]
                index = index[better]
                best[index] = value[better]
                moment[index] = (step - 1 + (a - c)[better] / (2 * bend[better])) * dt
        if previous is not None:
            left |= previous > exit_threshold
        before_previous, previous = previous, current

    return np.sqrt(best), moment


# --------------------------------------------------------------------------
# Grille, candidats et affinage


def grid_axis(resolution: int) -> np.ndarray:
    """Centres des cellules, en degrés, de −180° à 180°."""

    return -180 + (np.arange(resolution) + 0.5) * 360 / resolution


def _torus_distance(first: tuple[float, float], second: tuple[float, float]) -> float:
    return math.hypot(
        wrap_degrees(first[0] - second[0]), wrap_degrees(first[1] - second[1])
    )


@dataclass(frozen=True)
class Candidate:
    """Angles initiaux (degrés) menant à un mouvement presque périodique."""

    theta1: float
    theta2: float
    distance: float
    period: float


def find_candidates(distance: np.ndarray, axis: np.ndarray) -> list[tuple[float, float]]:
    """Minima locaux les plus profonds, espacés, sans doublon miroir."""

    finite = np.where(np.isfinite(distance), distance, np.inf)
    neighbours = np.full_like(finite, np.inf)
    for shift_row in (-1, 0, 1):
        for shift_column in (-1, 0, 1):
            if shift_row or shift_column:
                shifted = np.roll(np.roll(finite, shift_row, axis=0), shift_column, axis=1)
                neighbours = np.minimum(neighbours, shifted)
    rows, columns = np.nonzero(np.isfinite(finite) & (finite <= neighbours))
    order = np.argsort(finite[rows, columns])

    chosen: list[tuple[float, float]] = []
    for index in order:
        point = (float(axis[columns[index]]), float(axis[rows[index]]))
        mirror = (-point[0], -point[1])
        if any(
            _torus_distance(point, other) < CANDIDATE_SPACING
            or _torus_distance(mirror, other) < CANDIDATE_SPACING
            for other in chosen
        ):
            continue
        chosen.append(point if point[0] >= 0 else mirror)
        if len(chosen) == CANDIDATE_COUNT:
            break
    return chosen


def refine_candidates(
    parameters: PendulumParameters,
    starts: list[tuple[float, float]],
    duration: float,
    dt: float,
    step: float,
    iterations: int = 9,
) -> list[Candidate]:
    """Affine chaque candidat par recherche locale (motif 3 × 3 qui rétrécit)."""

    if not starts:
        return []
    centers = np.array(starts, dtype=float)
    steps = np.full(len(starts), step)
    offsets = np.array([(a, b) for a in (-1, 0, 1) for b in (-1, 0, 1)], dtype=float)
    middle = 4
    for _ in range(iterations):
        points = centers[:, None, :] + offsets[None, :, :] * steps[:, None, None]
        distance, _ = recurrence(
            parameters,
            np.radians(points[..., 0]).ravel(),
            np.radians(points[..., 1]).ravel(),
            duration,
            dt,
        )
        distance = np.where(np.isfinite(distance), distance, np.inf).reshape(len(starts), 9)
        best = np.argmin(distance, axis=1)
        centers = points[np.arange(len(starts)), best]
        steps = np.where(best == middle, steps / 2, steps)

    distance, moment = recurrence(
        parameters, np.radians(centers[:, 0]), np.radians(centers[:, 1]), duration, dt
    )
    results: list[Candidate] = []
    for (theta1, theta2), gap, period in zip(centers, distance, moment):
        if not np.isfinite(gap):
            continue
        point = (round(wrap_degrees(float(theta1)), 2), round(wrap_degrees(float(theta2)), 2))
        if any(_torus_distance(point, (c.theta1, c.theta2)) < 1 for c in results):
            continue
        results.append(Candidate(point[0], point[1], float(gap), float(period)))
    return sorted(results, key=lambda candidate: candidate.distance)


def compute_chunk(
    kind: str,
    parameters: PendulumParameters,
    theta1_degrees: np.ndarray,
    theta2_degrees: np.ndarray,
    duration: float,
    dt: float,
) -> tuple[np.ndarray, np.ndarray | None]:
    """Tâche exécutée dans un processus de calcul."""

    theta1 = np.radians(theta1_degrees)
    theta2 = np.radians(theta2_degrees)
    if kind == DIVERGENCE:
        return lyapunov_exponents(parameters, theta1, theta2, duration, dt), None
    return recurrence(parameters, theta1, theta2, duration, dt)


# --------------------------------------------------------------------------
# Calcul asynchrone et cache


@dataclass(eq=False)
class MapJob:
    kind: str
    parameters: PendulumParameters
    resolution: int
    duration: float
    dt: float
    values: np.ndarray = field(init=False)
    periods: np.ndarray | None = field(init=False, default=None)
    candidates: list[Candidate] | None = None
    completed: int = 0
    total: int = 0
    refining: bool = False
    error: str | None = None

    def __post_init__(self) -> None:
        self.values = np.full((self.resolution, self.resolution), np.nan)
        if self.kind == PERIODICITY:
            self.periods = np.full((self.resolution, self.resolution), np.nan)

    @property
    def key(self) -> tuple:
        p = self.parameters
        return (
            self.kind,
            p.mass1,
            p.mass2,
            p.length1,
            p.length2,
            p.gravity,
            p.damping,
            self.resolution,
            self.duration,
            self.dt,
        )

    @property
    def axis(self) -> np.ndarray:
        return grid_axis(self.resolution)

    @property
    def progress(self) -> float:
        return self.completed / self.total if self.total else 0.0

    @property
    def done(self) -> bool:
        return self.error is None and self.total > 0 and self.completed == self.total and not self.refining

    def cache_path(self) -> Path:
        digest = hashlib.sha1(repr((CACHE_VERSION, *self.key)).encode()).hexdigest()[:16]
        return CACHE_DIRECTORY / f"{self.kind}-{digest}.npz"


class MapService:
    """Lance les calculs en arrière-plan et rapporte leur avancement.

    ``poll`` doit être appelé régulièrement depuis la boucle Tk : il intègre
    les résultats reçus et renvoie les cartes modifiées.
    """

    def __init__(self) -> None:
        self._jobs: dict[tuple, MapJob] = {}
        self._events: queue.Queue = queue.Queue()
        self._executor: ProcessPoolExecutor | None = None
        self._lock = threading.Lock()

    def request(
        self,
        kind: str,
        parameters: PendulumParameters,
        resolution: int,
        duration: float,
        dt: float,
    ) -> MapJob:
        job = MapJob(kind, parameters, resolution, duration, dt)
        existing = self._jobs.get(job.key)
        if existing is not None:
            return existing
        self._jobs[job.key] = job
        if not self._load(job):
            # Démarrer les processus prend plusieurs centaines de millisecondes
            # sous Windows : on le fait hors de la boucle de l'interface.
            job.total = max(1, math.ceil(job.resolution // 2 / self._rows_per_chunk(job)))
            threading.Thread(target=self._submit, args=(job,), daemon=True).start()
        return job

    def poll(self) -> list[MapJob]:
        updated: list[MapJob] = []
        while True:
            try:
                job, rows, future = self._events.get_nowait()
            except queue.Empty:
                return updated
            if future is None:
                updated.append(job)
                continue
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 - affiché dans la carte
                job.error = f"{type(exc).__name__} : {exc}"
                job.refining = False
            else:
                if rows is None:
                    job.candidates = result
                    job.refining = False
                    self._save(job)
                else:
                    self._store_rows(job, rows, result)
            if job not in updated:
                updated.append(job)

    def shutdown(self) -> None:
        """Annule les lots en attente ; ceux en cours se terminent seuls."""

        with self._lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def _pool(self) -> ProcessPoolExecutor:
        with self._lock:
            if self._executor is None:
                workers = max(1, min(8, (os.cpu_count() or 2) // 2))
                self._executor = ProcessPoolExecutor(max_workers=workers)
            return self._executor

    @staticmethod
    def _rows_per_chunk(job: MapJob) -> int:
        return max(1, round(CHUNK_CELLS / job.resolution))

    def _submit(self, job: MapJob) -> None:
        axis = job.axis
        half = job.resolution // 2
        rows_per_chunk = self._rows_per_chunk(job)
        bands = [(start, min(half, start + rows_per_chunk)) for start in range(0, half, rows_per_chunk)]
        try:
            pool = self._pool()
            for start, stop in bands:
                theta1, theta2 = np.meshgrid(axis, axis[start:stop])
                future = pool.submit(
                    compute_chunk,
                    job.kind,
                    job.parameters,
                    theta1.ravel(),
                    theta2.ravel(),
                    job.duration,
                    job.dt,
                )
                future.add_done_callback(
                    lambda done, job=job, rows=(start, stop): self._events.put((job, rows, done))
                )
        except (OSError, RuntimeError) as exc:  # Service arrêté ou processus impossible à lancer.
            job.error = str(exc) or type(exc).__name__
            self._events.put((job, None, None))

    def _store_rows(
        self, job: MapJob, rows: tuple[int, int], result: tuple[np.ndarray, np.ndarray | None]
    ) -> None:
        start, stop = rows
        size = job.resolution
        values, periods = result
        job.values[start:stop] = values.reshape(stop - start, size)
        if periods is not None:
            job.periods[start:stop] = periods.reshape(stop - start, size)
        # Symétrie miroir : la cellule (i, j) vaut la cellule opposée.
        for row in range(start, stop):
            job.values[size - 1 - row] = job.values[row][::-1]
            if job.periods is not None:
                job.periods[size - 1 - row] = job.periods[row][::-1]
        job.completed += 1
        if job.completed < job.total:
            return
        if job.kind == PERIODICITY:
            job.refining = True
            future: Future = self._pool().submit(
                refine_candidates,
                job.parameters,
                find_candidates(job.values, job.axis),
                job.duration,
                job.dt,
                360 / job.resolution / 2,
            )
            future.add_done_callback(lambda done, job=job: self._events.put((job, None, done)))
        else:
            self._save(job)

    def _save(self, job: MapJob) -> None:
        candidates = np.array(
            [(c.theta1, c.theta2, c.distance, c.period) for c in job.candidates or ()],
            dtype=float,
        ).reshape(-1, 4)
        try:
            CACHE_DIRECTORY.mkdir(exist_ok=True)
            np.savez_compressed(
                job.cache_path(),
                values=job.values,
                periods=job.periods if job.periods is not None else np.empty(0),
                candidates=candidates,
            )
        except OSError:
            pass

    def _load(self, job: MapJob) -> bool:
        path = job.cache_path()
        if not path.exists():
            return False
        try:
            with np.load(path) as data:
                values = data["values"]
                periods = data["periods"]
                candidates = data["candidates"]
        except (OSError, KeyError, ValueError):
            return False
        if values.shape != job.values.shape:
            return False
        job.values = values
        if job.periods is not None and periods.shape == job.periods.shape:
            job.periods = periods
        if job.kind == PERIODICITY:
            job.candidates = [Candidate(*map(float, row)) for row in candidates]
        job.completed = job.total = 1
        return True
