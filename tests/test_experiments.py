"""Check quantum behavior against independent analytical or matrix references."""

import unittest

import numpy as np
from scipy.linalg import expm
from qiskit import QuantumCircuit
from qiskit.primitives import StatevectorEstimator
from qiskit.quantum_info import Operator, SparsePauliOp, Statevector, partial_trace, state_fidelity

from qiskit_portfolio.experiments import (
    BASIS_GATES, analytic_energy, ansatz, bell_circuit, evolution_circuit,
    ghz_circuit, hamiltonian, run_vqe, sample_counts, transpilation_comparison,
)


class QuantumExperimentsTest(unittest.TestCase):
    def test_bell_coherence_and_entanglement(self):
        state = Statevector.from_instruction(bell_circuit())
        # Probabilities alone would not distinguish an incoherent 00/11 mixture.
        self.assertAlmostEqual(float(state.expectation_value(SparsePauliOp("XX")).real), 1.0)
        np.testing.assert_allclose(partial_trace(state, [1]).data, np.eye(2) / 2, atol=1e-12)
        np.testing.assert_allclose(state.data, [1 / np.sqrt(2), 0, 0, 1 / np.sqrt(2)], atol=1e-12)

    def test_ghz_state(self):
        expected = np.zeros(8)
        expected[[0, 7]] = 1 / np.sqrt(2)
        np.testing.assert_allclose(Statevector.from_instruction(ghz_circuit()).data, expected, atol=1e-12)

    def test_sampling_is_seeded_and_keeps_input_unmeasured(self):
        circuit = bell_circuit()
        first = sample_counts(circuit, shots=1000, seed=42)
        self.assertEqual(first, sample_counts(circuit, shots=1000, seed=42))
        self.assertEqual(sum(first.values()), 1000)
        self.assertEqual(set(first), {"00", "11"})
        self.assertLess(abs(first["00"] / 1000 - 0.5), 0.1)
        self.assertEqual(circuit.num_clbits, 0)

    def test_parameterized_rotation_and_estimator(self):
        from qiskit.circuit import Parameter
        angle = Parameter("angle")
        circuit = QuantumCircuit(1)
        circuit.ry(angle, 0)
        estimator = StatevectorEstimator()
        for value in [0.0, np.pi / 4, np.pi / 2, np.pi]:
            bound = circuit.assign_parameters({angle: value})
            state = Statevector.from_instruction(bound)
            self.assertAlmostEqual(float(state.probabilities()[1]), np.sin(value / 2) ** 2)
            measured = estimator.run([(bound, SparsePauliOp("Z"))]).result()[0].data.evs
            self.assertAlmostEqual(float(measured), np.cos(value))

    def test_pauli_bit_ordering(self):
        circuit = QuantumCircuit(2)
        circuit.x(0)  # |q1 q0> = |01>
        state = Statevector.from_instruction(circuit)
        self.assertAlmostEqual(float(state.expectation_value(SparsePauliOp("IZ")).real), -1.0)
        self.assertAlmostEqual(float(state.expectation_value(SparsePauliOp("ZI")).real), 1.0)

    def test_variational_energy_matches_closed_form(self):
        trial, angle = ansatz()
        for value in np.linspace(-np.pi, np.pi, 11):
            state = Statevector.from_instruction(trial.assign_parameters({angle: value}))
            self.assertAlmostEqual(float(state.expectation_value(hamiltonian()).real), float(analytic_energy(value)))

    def test_vqe_reaches_full_hamiltonian_ground_state(self):
        result = run_vqe()
        self.assertAlmostEqual(result.energy, -np.sqrt(4.25), places=8)
        self.assertLess(abs(result.energy - result.exact_energy), 1e-8)
        self.assertGreater(result.fidelity, 1 - 1e-8)

    def test_exact_evolution_and_trotter_convergence(self):
        initial = QuantumCircuit(2)
        initial.h(0)
        initial.ry(0.7, 1)
        time = 0.8
        reference = Operator(expm(-1j * time * hamiltonian().to_matrix()))
        actual = Operator(evolution_circuit(QuantumCircuit(2), time))
        self.assertTrue(reference.equiv(actual))
        state = Statevector.from_instruction(initial)
        target = Statevector(reference.data @ state.data)
        errors = []
        for reps in [1, 4, 16]:
            evolved = Statevector.from_instruction(evolution_circuit(initial, time, reps))
            errors.append(1 - state_fidelity(target, evolved))
        self.assertGreater(errors[0], 1e-4)  # Detect hidden exact-matrix evaluation.
        self.assertGreater(errors[0], errors[1])
        self.assertGreater(errors[1], errors[2])

    def test_transpilation_preserves_complete_unitary(self):
        circuit = evolution_circuit(bell_circuit(), time=0.7, reps=4)
        rows = transpilation_comparison(circuit)
        self.assertEqual([r["optimization_level"] for r in rows], [0, 1, 2, 3])
        for row in rows:
            self.assertTrue(row["unitary_equivalent"])
            self.assertTrue(set(row["count_ops"]).issubset(BASIS_GATES))


if __name__ == "__main__":
    unittest.main()
