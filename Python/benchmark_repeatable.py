"""Repeatable benchmark runner for EV simulation scripts.

Runs one or more commands multiple times and reports median runtime.
This helps reduce noise from one-off measurements.
"""

from __future__ import annotations

import argparse
import statistics
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass
class BenchmarkResult:
    name: str
    samples_s: list[float]

    @property
    def median_s(self) -> float:
        return statistics.median(self.samples_s)

    @property
    def mean_s(self) -> float:
        return statistics.fmean(self.samples_s)

    @property
    def min_s(self) -> float:
        return min(self.samples_s)

    @property
    def max_s(self) -> float:
        return max(self.samples_s)

    @property
    def stdev_s(self) -> float:
        return statistics.stdev(self.samples_s) if len(self.samples_s) > 1 else 0.0


def _workspace_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_python_exe(root: Path) -> Path:
    return root / "venv" / "bin" / "python"


def _run_once(command: Sequence[str], cwd: Path, quiet: bool) -> float:
    start = time.perf_counter()
    completed = subprocess.run(
        list(command),
        cwd=str(cwd),
        stdout=subprocess.DEVNULL if quiet else None,
        stderr=subprocess.DEVNULL if quiet else None,
        check=False,
    )
    elapsed = time.perf_counter() - start
    if completed.returncode != 0:
        raise RuntimeError(
            f"Command failed with exit code {completed.returncode}: {' '.join(command)}"
        )
    return elapsed


def _benchmark_command(
    name: str,
    command: Sequence[str],
    cwd: Path,
    iterations: int,
    warmup: int,
    quiet: bool,
) -> BenchmarkResult:
    if warmup > 0:
        for _ in range(warmup):
            _run_once(command, cwd=cwd, quiet=quiet)

    samples: list[float] = []
    for idx in range(1, iterations + 1):
        elapsed = _run_once(command, cwd=cwd, quiet=quiet)
        samples.append(elapsed)
        print(f"  {name} iter {idx:>2}: {elapsed:.3f}s")

    return BenchmarkResult(name=name, samples_s=samples)


def _print_summary(results: list[BenchmarkResult]) -> None:
    print("\nSummary (seconds)")
    print("name                           median    mean     min      max      stdev")
    print("----------------------------  --------  -------  -------  -------  -------")
    for result in results:
        print(
            f"{result.name:<28}  "
            f"{result.median_s:>8.3f}  "
            f"{result.mean_s:>7.3f}  "
            f"{result.min_s:>7.3f}  "
            f"{result.max_s:>7.3f}  "
            f"{result.stdev_s:>7.3f}"
        )


def build_parser() -> argparse.ArgumentParser:
    root = _workspace_root()
    default_python = _default_python_exe(root)
    parser = argparse.ArgumentParser(description="Repeatable benchmark runner")
    parser.add_argument("--iterations", type=int, default=7, help="Measured runs per command")
    parser.add_argument("--warmup", type=int, default=1, help="Warmup runs per command")
    parser.add_argument(
        "--python-exe",
        type=str,
        default=str(default_python),
        help="Python executable used for script commands",
    )
    parser.add_argument(
        "--charger",
        type=str,
        default="Bharat AC-001 (3.3kW)",
        help="Charger preset passed to both script benchmarks",
    )
    parser.add_argument(
        "--no-quiet",
        action="store_true",
        help="Show command output while benchmarking",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.iterations < 3:
        print("Use at least 3 iterations for stable median measurements.")
        return 2
    if args.warmup < 0:
        print("Warmup must be >= 0.")
        return 2

    root = _workspace_root()
    python_exe = Path(args.python_exe)
    if not python_exe.exists():
        print(f"Python executable not found: {python_exe}")
        return 2

    command_matrix: list[tuple[str, list[str]]] = [
        (
            "ev_charging_sim",
            [
                str(python_exe),
                "Python/ev_charging_sim.py",
                "--charger",
                args.charger,
            ],
        ),
        (
            "pcc_harmonic_analysis",
            [
                str(python_exe),
                "Python/pcc_harmonic_analysis.py",
                "--charger",
                args.charger,
            ],
        ),
    ]

    print(f"Workspace: {root}")
    print(f"Python:    {python_exe}")
    print(f"Iterations:{args.iterations} (warmup: {args.warmup})")
    print(f"Charger:   {args.charger}")

    results: list[BenchmarkResult] = []
    for name, command in command_matrix:
        print(f"\nBenchmarking {name}: {' '.join(command)}")
        result = _benchmark_command(
            name=name,
            command=command,
            cwd=root,
            iterations=args.iterations,
            warmup=args.warmup,
            quiet=not args.no_quiet,
        )
        results.append(result)

    _print_summary(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
