"""Execute all notebooks from fresh kernels; preserve outputs for GitHub viewing."""

import argparse
from pathlib import Path

import nbformat
from nbclient import NotebookClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, help="Write copies here instead of updating the notebooks.")
    parser.add_argument("--kernel", default="qiskit-portfolio", help="Jupyter kernel registered for the project environment.")
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    for source in sorted((root / "notebooks").glob("*.ipynb")):
        notebook = nbformat.read(source, as_version=4)
        for cell in notebook.cells:
            if cell.cell_type == "code":
                cell.outputs = []
                cell.execution_count = None
        NotebookClient(
            notebook, timeout=180, kernel_name=arguments.kernel,
            resources={"metadata": {"path": str(source.parent)}},
            record_timing=False,
        ).execute()
        nbformat.validate(notebook)
        target = source if arguments.output_dir is None else arguments.output_dir / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        nbformat.write(notebook, target)
        print(f"Executed {source.name}")


if __name__ == "__main__":
    main()
