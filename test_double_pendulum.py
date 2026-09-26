import math
import unittest

from double_pendulum import DoublePendulum, PendulumParameters, PendulumState


class DoublePendulumTests(unittest.TestCase):
    def test_equilibrium_remains_stationary(self):
        model = DoublePendulum(PendulumParameters(), PendulumState(0, 0, 0, 0))
        for _ in range(100):
            model.step(0.01)
        self.assertEqual(model.state, PendulumState(0, 0, 0, 0))

    def test_energy_is_conserved_without_damping(self):
        model = DoublePendulum(
            PendulumParameters(damping=0),
            PendulumState(math.radians(90), 0, math.radians(30), 0),
        )
        initial_energy = model.energy()
        for _ in range(5_000):
            model.step(0.001)
        relative_error = abs((model.energy() - initial_energy) / initial_energy)
        self.assertLess(relative_error, 1e-7)

    def test_damping_reduces_energy(self):
        model = DoublePendulum(
            PendulumParameters(damping=0.1),
            PendulumState(math.radians(45), 0, math.radians(-25), 0),
        )
        initial_energy = model.energy()
        for _ in range(2_000):
            model.step(0.002)
        self.assertLess(model.energy(), initial_energy)

    def test_invalid_physical_parameter_is_rejected(self):
        with self.assertRaises(ValueError):
            DoublePendulum(PendulumParameters(length1=0), PendulumState(0, 0, 0, 0))


if __name__ == "__main__":
    unittest.main()
