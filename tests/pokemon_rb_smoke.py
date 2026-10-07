"""Manual native-Windows spawn smoke test for Pokemon Red and Blue."""

from __future__ import annotations

import multiprocessing as mp
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "Archipelago"))


def main():
    from ap_shapley import analyze_yaml
    from ap_shapley.builders import _build_logic_world

    badge_names = {
        "Boulder Badge", "Cascade Badge", "Thunder Badge", "Rainbow Badge",
        "Soul Badge", "Marsh Badge", "Volcano Badge", "Earth Badge",
    }
    results = {}

    for label, yaml_name, badgesanity in (
        ("badgesanity=false", "pokemon_rb_default.yaml", False),
        ("badgesanity=true", "pokemon_rb_badgesanity.yaml", True),
    ):
        logic_world = _build_logic_world(
            "Pokemon Red and Blue",
            {"badgesanity": badgesanity, "door_shuffle": "off"},
            world_seed=2026,
        )
        badge_sources = logic_world.acquisition_source_diagnostics(badge_names)
        constrained_badges = sum(row["source_locked"] for row in badge_sources)
        assert constrained_badges == (0 if badgesanity else 8)

        print(f"\n=== {label}: badge sources ===")
        for row in sorted(badge_sources, key=lambda value: value["item"]):
            print(
                f"{row['item']:16s}  "
                f"{str(row['source_location']):38s}  "
                f"locked={row['source_locked']}"
            )

        result = analyze_yaml(
            PROJECT_ROOT / yaml_name,
            world_samples=2,
            shapley_pairs=4,
            seed=2026,
            n_jobs=2,
            progress=True,
            release_on_goal=True,
        )
        summary = result.summary()
        assert summary["pending_constrained_with_all_offered_max"] == 0
        assert summary["still_unreachable_max"] == 0
        assert summary["goal_reachable_with_all_items_all"] is True
        assert (
            summary["reachable_with_all_items_min"]
            == summary["scored_checks_min"]
        )
        assert summary["max_abs_conservation_difference"] < 1e-10

        result.print_summary()
        results[badgesanity] = result

    false_badges = results[False].results.set_index("item").loc[
        sorted(badge_names), "shapley_per_copy"
    ]
    true_badges = results[True].results.set_index("item").loc[
        sorted(badge_names), "shapley_per_copy"
    ]
    difference = float((false_badges - true_badges).abs().sum())
    assert difference > 0
    print(f"\nBadge absolute Shapley difference sum: {difference:.6f}")


if __name__ == "__main__":
    mp.freeze_support()
    main()
