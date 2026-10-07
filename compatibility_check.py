from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent
ARCHIPELAGO_ROOT = PROJECT_ROOT / "Archipelago"


def _configure_import_path() -> None:
    if not (ARCHIPELAGO_ROOT / "BaseClasses.py").is_file():
        raise RuntimeError(f"Archipelago checkout not found at {ARCHIPELAGO_ROOT}")
    path = str(ARCHIPELAGO_ROOT)
    if path not in sys.path:
        sys.path.insert(0, path)


def main() -> None:
    _configure_import_path()
    parser = argparse.ArgumentParser(
        description="Run tiny independent Archipelago Shapley compatibility checks."
    )
    parser.add_argument("yamls", nargs="+", help="YAML files and/or folders")
    parser.add_argument(
        "--apworld",
        action="append",
        default=[],
        help="Exact external .apworld path (repeatable)",
    )
    parser.add_argument("--world-samples", type=int, default=2)
    parser.add_argument("--shapley-pairs", type=int, default=2)
    parser.add_argument("--n-jobs", type=int, default=2)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()

    from ap_shapley import compatibility_check_yamls

    rows = compatibility_check_yamls(
        [Path(path) for path in args.yamls],
        world_samples=args.world_samples,
        shapley_pairs=args.shapley_pairs,
        n_jobs=args.n_jobs,
        seed=args.seed,
        apworld_paths=args.apworld,
    )
    for row in rows:
        game = row.get("game", Path(row["yaml_path"]).name)
        resolved = " / ".join(filter(None, [row.get("module"), row.get("world_class")]))
        reason = f" — {row['reason']}" if row.get("reason") else ""
        print(f"{game:28s} {row['status']:9s} {resolved}{reason}")
        if row.get("status") == "PASS":
            print(
                "  checks={scored_checks_min}-{scored_checks_max} "
                "copies={shapley_item_copies_min}-{shapley_item_copies_max} "
                "families={distinct_item_families_min}-{distinct_item_families_max} "
                "precollected={precollected_item_count_min}-{precollected_item_count_max} "
                "constrained={constrained_item_copies_min}-{constrained_item_copies_max} "
                "pending={pending_constrained_with_all_offered_max} "
                "unreachable={still_unreachable_max} "
                "conservation={max_abs_conservation_difference:.3g} "
                "goal={goal_reachable_with_all_items_all} "
                "runtime={runtime_seconds:.2f}s".format(**row)
            )


if __name__ == "__main__":
    main()
