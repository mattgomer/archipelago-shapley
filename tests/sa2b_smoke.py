"""Manual native-Windows spawn smoke test for Sonic Adventure 2 Battle."""

from __future__ import annotations

import multiprocessing as mp
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "Archipelago"))


def main():
    import matplotlib

    matplotlib.use("Agg")

    from ap_shapley import analyze_yaml

    result = analyze_yaml(
        PROJECT_ROOT / "sa2b.yaml",
        world_samples=2,
        shapley_pairs=4,
        seed=2026,
        n_jobs=2,
        progress=True,
        release_on_goal=True,
    )
    summary = result.summary()
    assert summary["scored_checks_min"] > 0
    assert summary["pending_constrained_with_all_offered_max"] == 0
    assert summary["still_unreachable_max"] == 0
    assert summary["goal_reachable_with_all_items_all"] is True
    assert summary["max_abs_conservation_difference"] < 1e-10

    for index, world_result in enumerate(result.world_results):
        world_summary = world_result.summary()
        emblem_counts = world_summary["emblem_counts"]
        assert emblem_counts["total"] > 0
        assert emblem_counts["shapley"] == emblem_counts["total"]
        print(
            f"world={index} emblems={emblem_counts} "
            f"max_required={world_summary['maximum_required_emblems']} "
            f"cannons_core={world_summary['cannons_core_emblems']} "
            f"gate_costs={world_summary['gate_costs']}"
        )

    assert (
        result.world_results[0].summary()["level_gate_costs"]
        != result.world_results[1].summary()["level_gate_costs"]
    )

    gate_audit = result.audit_location("Gate 1 Boss")
    emblem_row = gate_audit.loc[gate_audit["item"] == "Emblem"].iloc[0]
    assert abs(float(emblem_row["approx_family_credit"]) - 1.0) < 1e-10

    emblem_audit = result.audit_item("Emblem", top_n=10)
    booster_audit = result.audit_item("Tails - Booster", top_n=10)
    result.plot(top_n=10, show_outliers=True)
    result.print_summary()
    print("\nGate 1 Boss allocation:")
    print(gate_audit.to_string(index=False))
    print("\nTop Emblem audit rows:")
    print(emblem_audit.to_string(index=False))
    print("\nTop Tails - Booster audit rows:")
    print(booster_audit.to_string(index=False))


if __name__ == "__main__":
    mp.freeze_support()
    main()
