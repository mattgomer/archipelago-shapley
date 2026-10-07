"""Analyze one player YAML locally and save its AnalysisResult."""

from __future__ import annotations

import argparse
import multiprocessing as mp
from pathlib import Path
import re
import sys


PROJECT_ROOT = Path(__file__).resolve().parent
ARCHIPELAGO_ROOT = PROJECT_ROOT / "Archipelago"
APWORLD_DIRECTORY = PROJECT_ROOT / "external_apworlds"
OUTPUT_DIRECTORY = PROJECT_ROOT / "output"


def _configure_import_path() -> None:
    if not (ARCHIPELAGO_ROOT / "BaseClasses.py").is_file():
        raise RuntimeError(
            f"Archipelago 0.6.7 was not found at {ARCHIPELAGO_ROOT}."
        )
    path = str(ARCHIPELAGO_ROOT)
    if path not in sys.path:
        sys.path.insert(0, path)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze one Archipelago player YAML and save the result."
    )
    parser.add_argument("yaml", type=Path, help="Path to one player YAML")
    parser.add_argument("--world-samples", type=int, default=2)
    parser.add_argument("--shapley-pairs", type=int, default=4)
    parser.add_argument("--n-jobs", type=int, default=2)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--apworld",
        action="append",
        type=Path,
        default=[],
        help="Additional exact external .apworld path (repeatable)",
    )
    return parser.parse_args()


def main() -> None:
    args = _arguments()
    _configure_import_path()

    from ap_shapley import analyze_yaml

    bundled_apworlds = [
        path
        for path in (
            APWORLD_DIRECTORY / "dsr.apworld",
            APWORLD_DIRECTORY / "mm_recomp.apworld",
        )
        if path.is_file()
    ]
    analysis = analyze_yaml(
        args.yaml,
        world_samples=args.world_samples,
        shapley_pairs=args.shapley_pairs,
        seed=args.seed,
        n_jobs=args.n_jobs,
        progress=True,
        release_on_goal=True,
        apworld_paths=[*bundled_apworlds, *args.apworld],
    )

    if args.output is None:
        stem = re.sub(r"[^A-Za-z0-9]+", "_", args.yaml.stem).strip("_")
        destination = OUTPUT_DIRECTORY / f"{stem}_analysis.pkl.gz"
    else:
        destination = args.output

    analysis.print_summary()
    print(analysis.top_items(30).to_string(index=False))
    saved_path = analysis.save(destination)
    print(f"\nSaved analysis to: {saved_path}")


if __name__ == "__main__":
    mp.freeze_support()
    main()
