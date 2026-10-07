from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def combine_analysis_tables(results: dict) -> pd.DataFrame:
    """Concatenate independent result tables with explicit game/YAML identity."""
    frames = []
    for yaml_path, analysis in results.items():
        frame = analysis.results.copy()
        frame.insert(0, "source_yaml", str(yaml_path))
        frame.insert(1, "yaml_name", Path(yaml_path).name)
        frame.insert(2, "game", analysis.game)
        frame.insert(
            3,
            "game_item",
            frame["item"].astype(str) + " — " + analysis.game,
        )
        frames.append(frame)
    if not frames:
        raise ValueError("At least one AnalysisResult is required.")
    return pd.concat(frames, ignore_index=True).sort_values(
        "shapley_per_copy", ascending=False
    ).reset_index(drop=True)


def plot_combined_analyses(
    results: dict,
    *,
    top_n: int | None = 50,
    metric: str = "shapley_per_copy",
    show_outliers: bool = False,
    break_even: float = 1.0,
    save_path: str | Path | None = None,
    show: bool = True,
):
    """Plot independent per-world values together for cross-game comparison."""
    if metric not in {"shapley_per_copy", "family_total"}:
        raise ValueError("metric must be 'shapley_per_copy' or 'family_total'")
    if not show:
        plt.switch_backend("Agg")

    combined = combine_analysis_tables(results)
    ranked = combined if top_n is None else combined.head(top_n)
    ranked = ranked.sort_values(metric, ascending=True)

    distributions = []
    labels = []
    for row in ranked.itertuples(index=False):
        analysis = results[row.source_yaml]
        values = []
        for world_result in analysis.world_results:
            matches = world_result.results.loc[
                world_result.results["item"] == row.item,
                metric,
            ]
            if not matches.empty:
                values.append(float(matches.iloc[0]))
        distributions.append(values)
        labels.append(row.game_item)

    fig_height = max(7.0, 0.31 * len(labels) + 2.0)
    fig, ax = plt.subplots(figsize=(13, fig_height))
    ax.boxplot(
        distributions,
        orientation="horizontal",
        tick_labels=labels,
        showmeans=True,
        showfliers=show_outliers,
    )
    ax.axvline(
        break_even,
        color="black",
        linestyle="--",
        linewidth=2,
        label=f"{break_even:g} check (break-even)",
    )

    metric_label = (
        "Per-copy allocation"
        if metric == "shapley_per_copy"
        else "Family-total allocation"
    )
    ax.set_xlabel(
        f"{metric_label} across independently generated game worlds"
    )
    ax.set_ylabel("Item — game")
    ax.set_title("Cross-game Archipelago item usefulness")

    if not show_outliers:
        lows, highs, means = [], [], []
        for values in distributions:
            array = np.asarray(values, dtype=float)
            q1, q3 = np.percentile(array, [25, 75])
            iqr = q3 - q1
            inside = array[
                (array >= q1 - 1.5 * iqr)
                & (array <= q3 + 1.5 * iqr)
            ]
            lows.append(float(inside.min()))
            highs.append(float(inside.max()))
            means.append(float(array.mean()))
        display_low = min(min(lows), break_even)
        display_high = max(max(highs), break_even)
        span = max(display_high - display_low, 1.0)
        x_low = max(0.0, display_low - span * 0.03)
        x_high = display_high + span * 0.03
        ax.set_xlim(x_low, x_high)
        used_label = False
        for y, mean in enumerate(means, start=1):
            if mean > x_high:
                ax.plot(
                    x_high,
                    y,
                    marker=">",
                    color="C2",
                    clip_on=False,
                    label=("mean beyond displayed range" if not used_label else None),
                )
                used_label = True

    ax.legend()
    fig.tight_layout()
    if save_path is not None:
        destination = Path(save_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(destination, bbox_inches="tight")
    if show:
        plt.show()
    else:
        plt.close(fig)
    return fig
