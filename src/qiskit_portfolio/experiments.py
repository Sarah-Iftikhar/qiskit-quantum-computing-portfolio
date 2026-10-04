"""Quantum experiments with independent mathematical reference checks.

All examples use ideal local simulation. Hamiltonian coefficients and times use
dimensionless units with hbar = 1; there is no physical-device calibration here.
"""

from dataclasses import dataclass

import numpy as np
from scipy.linalg import expm
from scipy.optimize import minimize

from qiskit import QuantumCircuit, transpile
from qiskit.circuit import Parameter
from qiskit.circuit.library import PauliEvolutionGate
from qiskit.primitives import StatevectorSampler
from qiskit.quantum_info import Operator, SparsePauliOp, Statevector, state_fidelity
from qiskit.synthesis import LieTrotter, MatrixExponential

SEED = 42
BASIS_GATES = ["rz", "sx", "x", "cx"]


def bell_circuit() -> QuantumCircuit:
    """Prepare (|00> + |11>)/sqrt(2), without measurements."""
    circuit = QuantumCircuit(2, name="Bell")
    circuit.h(0)
    circuit.cx(0, 1)
    return circuit


def ghz_circuit() -> QuantumCircuit:
    """Prepare (|000> + |111>)/sqrt(2), without measurements."""
    circuit = QuantumCircuit(3, name="GHZ")
    circuit.h(0)
    circuit.cx(0, 1)
    circuit.cx(0, 2)
    return circuit


def sample_counts(circuit: QuantumCircuit, shots: int = 1000, seed: int = SEED) -> dict[str, int]:
    """Sample an unmeasured, bound circuit; keep the input unchanged.

    measure_all() creates the classical register 'meas', which determines
    the result access path. Shot fluctuations are sampling noise, not device noise.
    """
    measured = circuit.copy()
    measured.measure_all()
    result = StatevectorSampler(seed=seed).run([measured], shots=shots).result()
    return dict(sorted(result[0].data.meas.get_counts().items()))


def hamiltonian() -> SparsePauliOp:
    """Return H = -Z_0 - Z_1 + 0.5 X_0 X_1.

    Qiskit writes Pauli strings in q1 q0 order: IZ acts on q0; ZI on q1.
    """
    return SparsePauliOp.from_list([("IZ", -1.0), ("ZI", -1.0), ("XX", 0.5)])


def ansatz() -> tuple[QuantumCircuit, Parameter]:
    """Trial state cos(theta/2)|00> + sin(theta/2)|11>."""
    theta = Parameter("theta")
    circuit = QuantumCircuit(2, name="VQE ansatz")
    circuit.ry(theta, 0)
    circuit.cx(0, 1)
    return circuit, theta


def analytic_energy(theta):
    """Closed-form expectation of H in the trial state; accepts arrays."""
    return -2.0 * np.cos(theta) + 0.5 * np.sin(theta)


@dataclass
class VQEResult:
    theta: float
    energy: float
    exact_energy: float
    fidelity: float
    history: list[tuple[float, float]]
    circuit: QuantumCircuit
    state: Statevector
    evaluations: int


def run_vqe(initial_theta: float = 0.5) -> VQEResult:
    """Optimize an exact statevector expectation with classical COBYLA.

    This demonstrates the variational loop. The cost is noiseless and does not
    include finite-shot Pauli measurements. Every objective evaluation is saved,
    so the history is not a list of accepted optimizer iterations.
    """
    operator = hamiltonian()
    trial, parameter = ansatz()
    history = []

    def objective(values):
        bound = trial.assign_parameters({parameter: float(values[0])})
        state = Statevector.from_instruction(bound)
        energy = float(state.expectation_value(operator).real)
        history.append((float(values[0]), energy))
        return energy

    optimized = minimize(
        objective,
        x0=[initial_theta],
        method="COBYLA",
        options={"tol": 1e-8, "maxiter": 300},
    )
    if not optimized.success:
        raise RuntimeError(f"VQE optimization did not converge: {optimized.message}")

    # A full 4x4 diagonalization provides a reference beyond the ansatz subspace.
    eigenvalues, eigenvectors = np.linalg.eigh(operator.to_matrix())
    best_theta = float(optimized.x[0])
    bound = trial.assign_parameters({parameter: best_theta})
    state = Statevector.from_instruction(bound)
    exact_state = Statevector(eigenvectors[:, 0])
    return VQEResult(
        theta=best_theta,
        energy=float(optimized.fun),
        exact_energy=float(eigenvalues[0]),
        fidelity=float(state_fidelity(state, exact_state)),
        history=history,
        circuit=bound,
        state=state,
        evaluations=int(optimized.nfev),
    )


def evolution_circuit(initial: QuantumCircuit, time: float, reps: int | None = None) -> QuantumCircuit:
    """Append and explicitly synthesize evolution under the portfolio Hamiltonian.

    reps=None uses a dense matrix exponential (only feasible for small systems).
    A positive reps uses a first-order Lie-Trotter product formula. We synthesize
    explicitly before Statevector evaluation: an unsynthesized evolution gate
    can expose its exact matrix, hiding the intended product-formula error.
    """
    if reps is not None and reps < 1:
        raise ValueError("Trotter repetitions must be positive.")
    synthesis = MatrixExponential() if reps is None else LieTrotter(reps=reps)
    gate = PauliEvolutionGate(hamiltonian(), time=time, synthesis=synthesis)
    steps = synthesis.synthesize(gate)
    return initial.compose(steps)


def evolution_comparison(initial: QuantumCircuit, time: float = 1.0, repetitions=(1, 4, 16, 64)) -> list[dict]:
    """Compare synthesized circuits with scipy.linalg.expm and their gate cost."""
    initial_state = Statevector.from_instruction(initial)
    exact_state = Statevector(expm(-1j * time * hamiltonian().to_matrix()) @ initial_state.data)
    rows = []
    for reps in (None, *repetitions):
        synthesized = evolution_circuit(initial, time, reps)
        compiled = transpile(
            synthesized, basis_gates=BASIS_GATES,
            optimization_level=1, seed_transpiler=SEED,
        )
        fidelity = float(state_fidelity(exact_state, Statevector.from_instruction(compiled)))
        rows.append({
            "method": "matrix exponential" if reps is None else "Lie-Trotter",
            "repetitions": reps,
            "time": float(time),
            "fidelity": fidelity,
            "infidelity": max(0.0, 1.0 - fidelity),
            "depth": int(compiled.depth()),
            "cx_count": int(compiled.count_ops().get("cx", 0)),
        })
    return rows


def transpilation_comparison(circuit: QuantumCircuit) -> list[dict]:
    """Compare levels 0-3 for a fixed basis without a device coupling map.

    Operator.equiv checks the complete unitary up to global phase, not just
    the output for one initial state. Basis translation is not a full backend
    execution or a noise-aware hardware benchmark.
    """
    original_operator = Operator(circuit)
    rows = []
    for level in range(4):
        compiled = transpile(
            circuit, basis_gates=BASIS_GATES,
            optimization_level=level, seed_transpiler=SEED,
        )
        equivalent = bool(original_operator.equiv(Operator(compiled)))
        if not equivalent:
            raise AssertionError(f"Level {level} changed the synthesized unitary.")
        rows.append({
            "optimization_level": level,
            "depth": int(compiled.depth()),
            "gate_count": int(compiled.size()),
            "cx_count": int(compiled.count_ops().get("cx", 0)),
            "count_ops": dict(compiled.count_ops()),
            "unitary_equivalent": equivalent,
        })
    return rows
