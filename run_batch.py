"""Analyze player YAMLs, save results, and build a cross-game comparison."""

from __future__ import annotations

import argparse
import multiprocessing as mp
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent
ARCHIPELAGO_ROOT = PROJECT_ROOT / "Archipelago"
YAML_DIRECTORY = PROJECT_ROOT / "batch_yamls"
APWORLD_DIRECTORY = PROJECT_ROOT / "external_apworlds"
OUTPUT_DIRECTORY = PROJECT_ROOT / "results"


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
        description=(
            "Analyze every YAML in batch_yamls independently and "
            "save one AnalysisResult and ranking plot per YAML."
        )
    )
    parser.add_argument("--world-samples", type=int, default=20)
    parser.add_argument("--shapley-pairs", type=int, default=16)
    parser.add_argument("--n-jobs", type=int, default=16)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--plot-top-n", type=int, default=30)
    parser.add_argument(
        "--combined-top-n",
        type=int,
        default=50,
        help="Items in the cross-game plot; use 0 for every item",
    )
    parser.add_argument(
        "--combine-only",
        action="store_true",
        help="Reuse existing result .pkl files without rerunning analyses",
    )
    parser.add_argument(
        "--yaml",
        action="append",
        type=Path,
        help=(
            "Analyze only this YAML, then combine it with existing saved "
            "results. Repeat for multiple YAMLs."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = _arguments()
    if args.combine_only and args.yaml:
        raise ValueError("--combine-only and --yaml cannot be used together")
    _configure_import_path()

    from ap_shapley import (
        AnalysisResult,
        analyze_yamls,
        combine_analysis_tables,
        plot_combined_analyses,
    )
    from ap_shapley.builders import _result_stem

    analyzed_results = {}
    if args.combine_only:
        result_paths = sorted(OUTPUT_DIRECTORY.glob("*.pkl"))
        if not result_paths:
            raise FileNotFoundError(
                f"No saved .pkl analyses found in {OUTPUT_DIRECTORY}"
            )
        combined_results = {}
        for path in result_paths:
            analysis = AnalysisResult.load(path)
            source = getattr(analysis, "yaml_path", str(path.resolve()))
            combined_results[source] = analysis
    else:
        apworld_paths = [
            APWORLD_DIRECTORY / "dsr.apworld",
            APWORLD_DIRECTORY / "mm_recomp.apworld",
        ]
        missing = [path for path in apworld_paths if not path.is_file()]
        if missing:
            raise FileNotFoundError(
                "Missing required external APWorld(s): "
                + ", ".join(str(path) for path in missing)
            )
        if not YAML_DIRECTORY.is_dir():
            raise FileNotFoundError(
                f"Player YAML directory does not exist: {YAML_DIRECTORY}"
            )

        yaml_inputs = args.yaml if args.yaml else YAML_DIRECTORY
        analyzed_results = analyze_yamls(
            yaml_inputs,
            world_samples=args.world_samples,
            shapley_pairs=args.shapley_pairs,
            seed=args.seed,
            n_jobs=args.n_jobs,
            progress=True,
            release_on_goal=True,
            apworld_paths=apworld_paths,
            output_dir=OUTPUT_DIRECTORY,
            save_plots=True,
            plot_top_n=args.plot_top_n,
        )

        combined_results = dict(analyzed_results)
        if args.yaml:
            # A targeted rerun should replace only those analyses, then use
            # every other existing saved result for the joint comparison.
            freshly_saved = {
                f"{_result_stem(Path(source), analysis.game)}.pkl"
                for source, analysis in analyzed_results.items()
            }
            for path in sorted(OUTPUT_DIRECTORY.glob("*.pkl")):
                if path.name in freshly_saved:
                    continue
                analysis = AnalysisResult.load(path)
                source = getattr(analysis, "yaml_path", str(path.resolve()))
                combined_results.setdefault(source, analysis)

    combined = combine_analysis_tables(combined_results)
    combined_path = OUTPUT_DIRECTORY / "combined_item_results.csv"
    combined.to_csv(combined_path, index=False)
    combined_plot_path = OUTPUT_DIRECTORY / "combined_item_plot.png"
    plot_combined_analyses(
        combined_results,
        top_n=(None if args.combined_top_n == 0 else args.combined_top_n),
        metric="shapley_per_copy",
        show_outliers=False,
        save_path=combined_plot_path,
        show=False,
    )

    action = "Loaded" if args.combine_only else "Analyzed"
    shown_results = combined_results if args.combine_only else analyzed_results
    print(f"\n{action} {len(shown_results)} analyses in {OUTPUT_DIRECTORY.resolve()}:")
    for yaml_path, analysis in shown_results.items():
        summary = analysis.summary()
        print(
            f"- {Path(yaml_path).name}: {analysis.game} | "
            f"checks={summary['scored_checks_min']}-"
            f"{summary['scored_checks_max']} | "
            f"conservation="
            f"{summary['max_abs_conservation_difference']:.3g}"
        )
    print(f"- Combined analyses: {len(combined_results)}")
    print(f"- Combined table: {combined_path.resolve()}")
    print(f"- Combined plot:  {combined_plot_path.resolve()}")


if __name__ == "__main__":
    mp.freeze_support()
    main()
