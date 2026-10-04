"""Regenerate the checked-in figures and machine-readable experiment results."""

import argparse
import csv
import json
import platform
from importlib.metadata import version
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .experiments import (
    SEED, analytic_energy, bell_circuit, evolution_comparison,
    evolution_circuit, ghz_circuit, run_vqe, sample_counts,
    transpilation_comparison,
)


def generate_outputs(output_dir: Path) -> dict:
    """Write four plots, a result summary, and the raw benchmark tables."""
    figures = output_dir / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 11,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.titleweight": "bold", "savefig.dpi": 180,
    })

    bell_counts = sample_counts(bell_circuit(), 1000, SEED)
    ghz_counts = sample_counts(ghz_circuit(), 2000, 43)
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.4), constrained_layout=True)
    for ax, counts, title in zip(axes, [bell_counts, ghz_counts], ["Bell state · 1,000 shots", "GHZ state · 2,000 shots"]):
        shots = sum(counts.values())
        labels = list(counts)
        values = [counts[label] / shots for label in labels]
        bars = ax.bar(labels, values, color=["#1565C0", "#00897B"], width=0.55)
        ax.axhline(0.5, color="#777777", ls="--", lw=1, label="Exact probability = 0.5")
        ax.bar_label(bars, labels=[str(counts[label]) for label in labels], padding=4)
        ax.set(title=title, xlabel="Measured bitstring", ylabel="Sampled frequency", ylim=(0, 0.64))
        ax.legend(loc="lower center", fontsize=9)
    fig.savefig(figures / "entanglement_sampling.png")
    plt.close(fig)

    vqe = run_vqe()
    history = np.asarray(vqe.history)
    angles = np.linspace(-np.pi, np.pi, 401)
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6), constrained_layout=True)
    axes[0].plot(angles, analytic_energy(angles), color="#1565C0", label=r"$E(\theta)=-2\cos\theta+0.5\sin\theta$")
    axes[0].scatter([vqe.theta], [vqe.energy], color="#E65100", zorder=3, label="COBYLA optimum")
    axes[0].set(title="One-parameter energy landscape", xlabel=r"$\theta$ (radians)", ylabel="Energy (dimensionless)")
    axes[0].legend(fontsize=9)
    axes[1].plot(np.arange(1, len(history) + 1), history[:, 1], marker=".", color="#00897B", label="Objective evaluations")
    axes[1].axhline(vqe.exact_energy, color="#E65100", ls="--", label="Exact ground energy")
    axes[1].set(title="VQE optimization trace", xlabel="Objective evaluation", ylabel="Energy (dimensionless)")
    axes[1].legend(fontsize=9)
    fig.savefig(figures / "vqe_validation.png")
    plt.close(fig)

    evolution = evolution_comparison(vqe.circuit)
    approximate = evolution[1:]
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.5), constrained_layout=True)
    reps = [row["repetitions"] for row in approximate]
    axes[0].loglog(reps, [row["infidelity"] for row in approximate], "o-", color="#1565C0")
    axes[0].set(title="Evolution error at t = 1", xlabel="Lie-Trotter repetitions", ylabel=r"Infidelity $1-|\langle\psi_{exact}|\psi\rangle|^2$")
    axes[0].grid(alpha=0.2, which="both")
    axes[1].plot(reps, [row["cx_count"] for row in approximate], "o-", color="#00897B")
    axes[1].set_xscale("log")
    axes[1].set(title="Cost of improving accuracy", xlabel="Lie-Trotter repetitions", ylabel="CX gate count after level 1")
    axes[1].grid(alpha=0.2)
    fig.savefig(figures / "evolution_accuracy_cost.png")
    plt.close(fig)

    # Freeze synthesis first; then isolate optimization of that fixed approximation.
    circuit = evolution_circuit(vqe.circuit, time=1.0, reps=4)
    compilation = transpilation_comparison(circuit)
    levels = [row["optimization_level"] for row in compilation]
    fig, ax = plt.subplots(figsize=(6.5, 3.6), constrained_layout=True)
    ax.bar(np.array(levels) - 0.18, [r["depth"] for r in compilation], width=0.36, color="#1565C0", label="Circuit depth")
    ax.bar(np.array(levels) + 0.18, [r["cx_count"] for r in compilation], width=0.36, color="#00897B", label="CX count")
    ax.set(title="Transpilation of a fixed four-step evolution", xlabel="Optimization level", ylabel="Count", xticks=levels)
    ax.legend()
    fig.savefig(figures / "transpilation_comparison.png")
    plt.close(fig)

    summary = {
        "environment": {"python": platform.python_version(), **{p: version(p) for p in ("qiskit", "numpy", "scipy", "matplotlib")}},
        "configuration": {"bell_shots": 1000, "bell_seed": SEED, "ghz_shots": 2000, "ghz_seed": 43, "transpiler_seed": SEED, "initial_theta": 0.5, "hbar": 1, "units": "dimensionless"},
        "bell_counts": bell_counts,
        "ghz_counts": ghz_counts,
        "vqe": {"theta": vqe.theta, "energy": vqe.energy, "exact_energy": vqe.exact_energy, "absolute_error": abs(vqe.energy - vqe.exact_energy), "ground_state_fidelity": vqe.fidelity, "objective_evaluations": vqe.evaluations},
        "evolution": evolution,
        "transpilation": compilation,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    for name, fields, rows in [
        ("vqe_history.csv", ["theta", "energy"], [dict(zip(["theta", "energy"], pair)) for pair in vqe.history]),
        ("evolution_comparison.csv", list(evolution[0]), evolution),
        ("transpilation_comparison.csv", ["optimization_level", "depth", "gate_count", "cx_count", "unitary_equivalent"], compilation),
    ]:
        with (output_dir / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    arguments = parser.parse_args()
    summary = generate_outputs(arguments.output_dir)
    print(f"VQE energy: {summary['vqe']['energy']:.10f}")
    print(f"Exact energy: {summary['vqe']['exact_energy']:.10f}")
    print(f"Absolute error: {summary['vqe']['absolute_error']:.3e}")
    print(f"Results written to {arguments.output_dir}")


if __name__ == "__main__":
    main()
