import math
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

import chaos_maps
from double_pendulum import DoublePendulum, PendulumParameters, PendulumState, wrap_degrees
from main import normalize_angle, parse_angle
from map_view import PERIODIC_BEST, PERIODIC_WORST, heatmap_ppm, normalize, sequential_ramp


class VectorizedModelTests(unittest.TestCase):
    def test_matches_scalar_model(self):
        parameters = PendulumParameters(mass2=1.5, length2=0.8, damping=0.02)
        states = np.random.default_rng(3).uniform(-3, 3, (4, 6))
        vectorized = chaos_maps.VectorizedPendulum(parameters)
        models = [DoublePendulum(parameters, PendulumState(*column)) for column in states.T]
        for _ in range(200):
            vectorized.step(states, 0.01)
            for model in models:
                model.step(0.01)
        expected = np.array(
            [[m.state.theta1, m.state.omega1, m.state.theta2, m.state.omega2] for m in models]
        ).T
        np.testing.assert_allclose(states, expected, atol=1e-10)


class MetricTests(unittest.TestCase):
    parameters = PendulumParameters(damping=0.0)

    def test_lyapunov_separates_regular_and_chaotic_motion(self):
        exponents = chaos_maps.lyapunov_exponents(
            self.parameters, np.radians([5, 170]), np.radians([5, 170]), 15, 0.01
        )
        self.assertLess(exponents[0], 0.1)
        self.assertGreater(exponents[1], 0.5)

    def test_mirror_symmetry(self):
        exponents = chaos_maps.lyapunov_exponents(
            self.parameters, np.radians([120, -120]), np.radians([-10, 10]), 10, 0.01
        )
        self.assertAlmostEqual(exponents[0], exponents[1], delta=0.05)

    def test_normal_mode_is_periodic(self):
        # Pendules identiques : aux petits angles, θ₂ = √2·θ₁ est un mode propre
        # de période 2π / (√(g/L)·√(2 − √2)).
        theta1 = math.radians(3)
        distance, moment = chaos_maps.recurrence(
            self.parameters,
            np.array([theta1, theta1]),
            np.array([math.sqrt(2) * theta1, theta1]),
            4,
            0.01,
        )
        period = 2 * math.pi / (math.sqrt(9.81) * math.sqrt(2 - math.sqrt(2)))
        self.assertLess(distance[0], 0.01)
        self.assertAlmostEqual(moment[0], period, delta=0.02)
        self.assertGreater(distance[1], 0.05)

    def test_no_return_is_infinite(self):
        # Presque à la verticale ascendante, le pendule reste près du départ.
        distance, moment = chaos_maps.recurrence(
            self.parameters, np.radians([179.999]), np.radians([179.999]), 1, 0.01
        )
        self.assertTrue(np.isinf(distance[0]))
        self.assertTrue(np.isnan(moment[0]))


class CandidateTests(unittest.TestCase):
    def test_mirror_duplicates_are_merged(self):
        axis = chaos_maps.grid_axis(36)
        distance = np.ones((36, 36))
        distance[27, 27] = 0.01  # (≈ 95°, ≈ 95°)
        distance[8, 8] = 0.01  # son miroir
        distance[27, 9] = 0.02
        candidates = chaos_maps.find_candidates(distance, axis)
        self.assertEqual(candidates[0], (95.0, 95.0))
        self.assertNotIn((-95.0, -95.0), candidates)

    def test_refinement_finds_the_normal_mode(self):
        parameters = PendulumParameters(damping=0.0)
        start = (4.0, 4.0 * math.sqrt(2) + 0.8)
        [candidate] = chaos_maps.refine_candidates(parameters, [start], 3, 0.01, step=0.5)
        self.assertLess(candidate.distance, 0.01)


class MapServiceTests(unittest.TestCase):
    def test_small_map_end_to_end(self):
        with tempfile.TemporaryDirectory() as directory:
            original = chaos_maps.CACHE_DIRECTORY
            chaos_maps.CACHE_DIRECTORY = Path(directory)
            service = chaos_maps.MapService()
            try:
                job = service.request(chaos_maps.PERIODICITY, PendulumParameters(), 16, 2.0, 0.02)
                deadline = time.monotonic() + 120
                while not (job.done or job.error) and time.monotonic() < deadline:
                    service.poll()
                    time.sleep(0.05)
                self.assertIsNone(job.error)
                self.assertTrue(job.done)
                self.assertFalse(np.isnan(job.values).any())
                # Symétrie miroir : la cellule (i, j) vaut la cellule opposée.
                np.testing.assert_array_equal(job.values, job.values[::-1, ::-1])
                self.assertTrue(job.cache_path().exists())
                cached = chaos_maps.MapService().request(
                    chaos_maps.PERIODICITY, PendulumParameters(), 16, 2.0, 0.02
                )
                self.assertTrue(cached.done)
                np.testing.assert_array_equal(cached.values, job.values)
            finally:
                service.shutdown()
                chaos_maps.CACHE_DIRECTORY = original


class DisplayTests(unittest.TestCase):
    def test_ramp_gets_lighter(self):
        ramp = sequential_ramp(hue=255, chroma=0.15).astype(float)
        luminance = ramp @ np.array([0.2126, 0.7152, 0.0722])
        self.assertTrue(np.all(np.diff(luminance) >= -1))
        self.assertLess(luminance[0], 40)
        self.assertGreater(luminance[-1], 220)

    def test_periodicity_scale(self):
        values = np.array([np.inf, PERIODIC_WORST, PERIODIC_BEST, 0.0, np.nan])
        level = normalize(chaos_maps.PERIODICITY, values, 1.0)
        np.testing.assert_allclose(level[:4], [0, 0, 1, 1])
        self.assertTrue(np.isnan(level[4]))

    def test_heatmap_image(self):
        data = heatmap_ppm(chaos_maps.DIVERGENCE, np.random.rand(8, 8), 1.0, 20)
        self.assertTrue(data.startswith(b"P6 20 20 255\n"))
        self.assertEqual(len(data), len(b"P6 20 20 255\n") + 20 * 20 * 3)


class AngleInputTests(unittest.TestCase):
    def test_parse_angle(self):
        self.assertEqual(parse_angle("120,5"), 120.5)
        self.assertEqual(parse_angle(" −10° "), -10)
        self.assertIsNone(parse_angle("abc"))
        self.assertIsNone(parse_angle("inf"))

    def test_normalize_angle(self):
        self.assertEqual(normalize_angle(121.1), 121.1)
        self.assertEqual(normalize_angle(190), -170)
        self.assertEqual(normalize_angle(-180), 180)
        self.assertEqual(wrap_degrees(540), 180)
        self.assertEqual(str(normalize_angle(-0.001)), "0.0")


if __name__ == "__main__":
    unittest.main()
