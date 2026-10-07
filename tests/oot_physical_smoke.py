"""Manual native-Windows spawn smoke test for physical-copy Shapley."""

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
        PROJECT_ROOT / "local_yamls" / "oot.yaml",
        world_samples=2,
        shapley_pairs=2,
        seed=2026,
        n_jobs=2,
        progress=False,
        release_on_goal=True,
    )
    summary = result.summary()
    assert summary["pending_constrained_with_all_offered_max"] == 0
    assert summary["max_abs_conservation_difference"] < 1e-10

    audit = result.audit_item("Zeldas Lullaby", top_n=5)
    assert not audit.empty
    result.plot(top_n=10, show_outliers=True)

    print(
        "physical OK",
        f"checks={summary['scored_checks_min']}",
        f"conservation={summary['max_abs_conservation_difference']}",
        f"audit_rows={len(audit)}",
    )


if __name__ == "__main__":
    mp.freeze_support()
    main()
