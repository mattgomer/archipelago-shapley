from __future__ import annotations

import copy
import gzip
import html
from pathlib import Path
import pickle

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

try:
    from scipy.stats import t as student_t
except Exception:  # scipy is normally available in Colab; fall back cleanly.
    student_t = None


class WorldAnalysisResult:
    """Internal result for one fixed Archipelago logic world."""

    def __init__(
        self,
        logic_world,
        results,
        audit,
        shapley_pairs: int,
        sampling_seed: int,
        summary_data: dict,
        world_seed: int | None = None,
    ):
        self.logic_world = logic_world
        self.results = results
        self.audit = audit
        self.shapley_pairs = shapley_pairs
        self.sampling_seed = sampling_seed
        self.summary_data = dict(summary_data)
        self.world_seed = world_seed

    def summary(self):
        # Sanity/reachability checks were already computed during analysis.
        # Printing a summary should therefore be effectively instantaneous.
        return dict(self.summary_data)

    def strip_logic_world(self):
        """
        Drop the heavyweight Archipelago graph before crossing a process
        boundary. Numeric/DataFrame results remain fully usable.
        """
        self.logic_world = None
        return self


class AnalysisResult:
    """
    User-facing aggregate over one or more independently generated worlds.

    Item means are conditional on that item existing in the sampled world.
    For world_samples > 1, the displayed CI is based on the observed spread
    of the per-world Shapley estimates, so it naturally includes both real
    world-to-world variation and finite Shapley Monte Carlo noise.
    """

    def __init__(
        self,
        world_results: list[WorldAnalysisResult],
        seed: int,
        options: dict,
        player: int = 1,
        progress_stage_callback=None,
    ):
        if not world_results:
            raise ValueError("At least one world result is required.")

        self.world_results = world_results
        self.seed = seed
        self.options = dict(options)
        self.player = player
        self.world_samples = len(world_results)
        self.shapley_pairs = world_results[0].shapley_pairs

        # In serial mode this may be available immediately. In parallel mode
        # workers intentionally strip heavyweight AP graphs before returning.
        self.logic_world = world_results[0].logic_world
        self.game = world_results[0].summary_data["game"]
        self.release_on_goal = bool(
            world_results[0].summary_data.get("release_on_goal", False)
        )

        if progress_stage_callback is not None:
            progress_stage_callback("Aggregating item results", False)
        self.results = self._aggregate_item_results()
        if progress_stage_callback is not None:
            progress_stage_callback("Aggregating item results", True)

        if progress_stage_callback is not None:
            progress_stage_callback("Aggregating audits", False)
        self.audit = self._aggregate_audits()
        if progress_stage_callback is not None:
            progress_stage_callback("Aggregating audits", True)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str | Path) -> Path:
        """
        Save analysis data without serializing Archipelago's live logic graph.

        Files ending in ``.gz`` are gzip-compressed. Only load analysis files
        you trust, because this format uses Python pickle.
        """
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)

        saved = copy.copy(self)
        saved.logic_world = None
        saved.world_results = []
        for world_result in self.world_results:
            saved_world = copy.copy(world_result)
            saved_world.logic_world = None
            saved.world_results.append(saved_world)

        opener = gzip.open if destination.suffix == ".gz" else open
        with opener(destination, "wb") as stream:
            pickle.dump(saved, stream, protocol=pickle.HIGHEST_PROTOCOL)

        return destination.resolve()

    @classmethod
    def load(cls, path: str | Path) -> "AnalysisResult":
        """Load a trusted analysis file written by :meth:`save`."""
        source = Path(path)
        opener = gzip.open if source.suffix == ".gz" else open
        with opener(source, "rb") as stream:
            result = pickle.load(stream)

        if not isinstance(result, cls):
            raise TypeError(
                f"{source} contains {type(result).__name__}, not "
                f"{cls.__name__}."
            )

        # Do not silently present an artifact produced by the retired
        # family-block experiment as canonical physical-copy Shapley.
        legacy_method = getattr(result, "allocation_method", None)
        if legacy_method == "owen":
            raise ValueError(
                f"{source} contains an experimental Owen/family-block "
                "analysis. Regenerate it with the current physical-copy "
                "sampler."
            )
        return result

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    @staticmethod
    def _critical_95(n: int) -> float:
        if n <= 1:
            return 1.96
        if student_t is not None:
            return float(student_t.ppf(0.975, df=n - 1))
        return 1.96

    def _aggregate_item_results(self):
        """Vectorized conditional-on-existence aggregation across worlds."""
        frames = []
        for world_index, wr in enumerate(self.world_results):
            frame = wr.results.copy()
            frame["world_index"] = world_index
            frames.append(frame)

        stacked = pd.concat(frames, ignore_index=True)
        grouped = stacked.groupby("item", sort=False)

        agg = grouped.agg(
            worlds_present=("world_index", "nunique"),
            mean_copies=("copies", "mean"),
            shapley_per_copy=("shapley_per_copy", "mean"),
            observed_world_sd=("shapley_per_copy", "std"),
            family_total=("family_total", "mean"),
            first_sampling_se=("sampling_se", "first"),
            first_ci95=("ci95", "first"),
        )

        # Sum of inner sampling variances per item. min_count=1 preserves NaN
        # when no finite variance estimate exists (e.g. shapley_pairs=1).
        sampling_var_sum = grouped["sampling_se"].apply(
            lambda s: (s.dropna().astype(float) ** 2).sum(min_count=1)
        )
        sampling_var_mean = grouped["sampling_se"].apply(
            lambda s: (s.dropna().astype(float) ** 2).mean()
        )

        n = agg["worlds_present"].astype(float)
        multi = agg["worlds_present"] > 1

        agg["sampling_se_mean"] = np.sqrt(sampling_var_sum) / n

        observed_var = agg["observed_world_sd"] ** 2
        estimated_world_var = np.maximum(
            observed_var - sampling_var_mean.fillna(0.0),
            0.0,
        )
        agg["estimated_world_sd"] = np.sqrt(estimated_world_var)

        # User-facing total uncertainty. For >1 worlds, the ordinary standard
        # error across independent world estimates already includes both true
        # world variation and inner Monte Carlo noise. For one world, use the
        # inner Shapley SE/CI directly.
        agg["total_se"] = agg["first_sampling_se"]
        agg.loc[multi, "total_se"] = (
            agg.loc[multi, "observed_world_sd"]
            / np.sqrt(agg.loc[multi, "worlds_present"].astype(float))
        )

        critical = agg["worlds_present"].map(
            lambda count: self._critical_95(int(count))
        )
        agg["ci95"] = critical * agg["total_se"]
        agg.loc[~multi, "ci95"] = agg.loc[~multi, "first_ci95"]

        # For one world, topology spread is undefined rather than zero.
        agg.loc[~multi, "estimated_world_sd"] = np.nan
        agg.loc[~multi, "sampling_se_mean"] = agg.loc[
            ~multi, "first_sampling_se"
        ]

        result = agg.reset_index().drop(
            columns=["first_sampling_se", "first_ci95"]
        )

        return result.sort_values(
            "shapley_per_copy",
            ascending=False,
        ).reset_index(drop=True)

    def _aggregate_audits(self):
        """
        Aggregate audit matrices with NumPy indexing instead of scalar pandas
        .loc writes. Items are averaged only over worlds where they exist.
        Missing locations in an otherwise-present world contribute zero.
        """
        all_items = pd.Index(sorted({
            item
            for wr in self.world_results
            for item in wr.audit.index
        }))
        all_locations = pd.Index(sorted({
            loc
            for wr in self.world_results
            for loc in wr.audit.columns
        }))

        audit_sum = np.zeros(
            (len(all_items), len(all_locations)),
            dtype=float,
        )
        item_world_counts = np.zeros(len(all_items), dtype=np.int64)

        for wr in self.world_results:
            row_idx = all_items.get_indexer(wr.audit.index)
            col_idx = all_locations.get_indexer(wr.audit.columns)

            audit_sum[np.ix_(row_idx, col_idx)] += wr.audit.to_numpy(
                dtype=float,
                copy=False,
            )
            item_world_counts[row_idx] += 1

        nonzero = item_world_counts > 0
        audit_sum[nonzero, :] /= item_world_counts[nonzero, None]

        return pd.DataFrame(
            audit_sum,
            index=all_items,
            columns=all_locations,
        )

    # ------------------------------------------------------------------
    # Summary / validation
    # ------------------------------------------------------------------

    def summary(self):
        world_summaries = [wr.summary() for wr in self.world_results]
        conservation = [
            abs(ws["conservation_difference"])
            for ws in world_summaries
        ]
        scored_checks = [ws["scored_checks"] for ws in world_summaries]
        free_checks = [ws["free_checks"] for ws in world_summaries]
        item_dependent = [ws["item_dependent_checks"] for ws in world_summaries]
        shapley_copies = [
            ws.get("shapley_item_copies", 0)
            for ws in world_summaries
        ]
        shapley_families = [
            ws.get("distinct_item_families", 0)
            for ws in world_summaries
        ]
        reachable_with_all = [
            ws["reachable_with_all_items"]
            for ws in world_summaries
        ]
        still_unreachable = [
            ws["still_unreachable"]
            for ws in world_summaries
        ]
        goal_reachable = [
            bool(ws.get("goal_reachable_with_all_items", False))
            for ws in world_summaries
        ]
        constrained_copies = [
            ws.get("constrained_item_copies", 0)
            for ws in world_summaries
        ]
        constrained_families = [
            ws.get("constrained_item_families", 0)
            for ws in world_summaries
        ]
        pending_constrained = [
            ws.get("pending_constrained_items_with_all_offered", 0)
            for ws in world_summaries
        ]
        precollected = [
            ws.get("precollected_item_count", 0)
            for ws in world_summaries
        ]

        return {
            "game": self.game,
            "world_samples": self.world_samples,
            "shapley_pairs_per_world": self.shapley_pairs,
            "total_permutations": self.world_samples * self.shapley_pairs * 2,
            "seed": self.seed,
            "release_on_goal": self.release_on_goal,
            "scored_checks_min": min(scored_checks),
            "scored_checks_max": max(scored_checks),
            "free_checks_min": min(free_checks),
            "free_checks_max": max(free_checks),
            "item_dependent_checks_min": min(item_dependent),
            "item_dependent_checks_max": max(item_dependent),
            "shapley_item_copies_min": min(shapley_copies),
            "shapley_item_copies_max": max(shapley_copies),
            "distinct_item_families_min": min(shapley_families),
            "distinct_item_families_max": max(shapley_families),
            "precollected_item_count_min": min(precollected),
            "precollected_item_count_max": max(precollected),
            "constrained_item_copies_min": min(constrained_copies),
            "constrained_item_copies_max": max(constrained_copies),
            "constrained_item_families_min": min(constrained_families),
            "constrained_item_families_max": max(constrained_families),
            "pending_constrained_with_all_offered_max": max(pending_constrained),
            "reachable_with_all_items_min": min(reachable_with_all),
            "reachable_with_all_items_max": max(reachable_with_all),
            "still_unreachable_max": max(still_unreachable),
            "goal_reachable_with_all_items_all": all(goal_reachable),
            "max_abs_conservation_difference": max(conservation),
        }

    def print_summary(self):
        print("=== Archipelago Shapley analysis ===")
        for key, value in self.summary().items():
            print(f"{key:34s} {value}")

    # ------------------------------------------------------------------
    # Main ranking
    # ------------------------------------------------------------------

    def _resolve_metric(self, metric: str | None) -> str:
        if metric is None:
            return "shapley_per_copy"
        if metric not in {"shapley_per_copy", "family_total"}:
            raise ValueError(
                "metric must be 'shapley_per_copy' or 'family_total', "
                f"got {metric!r}"
            )
        return metric

    def top_items(self, n: int = 30, metric: str | None = None):
        metric = self._resolve_metric(metric)
        return (
            self.results.sort_values(metric, ascending=False)
            .head(n)
            .copy()
        )

    def display_items(
        self,
        metric: str | None = None,
        max_height: int = 480,
    ):
        """Display the complete ranked item table in a scrollable notebook panel."""
        metric = self._resolve_metric(metric)
        if max_height <= 0:
            raise ValueError("max_height must be a positive number of pixels.")

        ranked = self.results.sort_values(metric, ascending=False).copy()

        try:
            from IPython.display import HTML, display
        except ImportError as exc:
            raise RuntimeError(
                "display_items() requires IPython/Jupyter. Use the results "
                "attribute or top_items() to obtain a DataFrame directly."
            ) from exc

        table = ranked.to_html(
            index=False,
            border=0,
            classes="analysis-items-table",
        )
        display(HTML(
            f"""
            <div style="max-height: {int(max_height)}px; overflow-y: auto; "
                       "border: 1px solid #ddd;">
              <style>
                .analysis-items-table {{ border-collapse: collapse; width: 100%; }}
                .analysis-items-table th {{ position: sticky; top: 0; z-index: 1;
                                            background: var(--jp-layout-color1, white); }}
                .analysis-items-table th, .analysis-items-table td {{
                    padding: 0.35em 0.6em; border-bottom: 1px solid #ddd;
                    text-align: right;
                }}
                .analysis-items-table th:first-child,
                .analysis-items-table td:first-child {{ text-align: left; }}
              </style>
              {table}
            </div>
            """
        ))

        # Avoid Jupyter automatically rendering a second, non-scrollable copy.
        return None

    def plot(
        self,
        top_n: int = 30,
        break_even: float = 1.0,
        show_outliers: bool = False,
        metric: str | None = None,
        save_path: str | Path | None = None,
        show: bool = True,
    ):
        """
        Plot the top items as horizontal box-and-whisker distributions across
        generated worlds.

        Each box uses the actual per-world Shapley estimates for that item,
        conditional on the item existing in that world. This visualizes the
        spread a player should expect from world generation (plus the finite
        inner Shapley Monte Carlo noise remaining in each world estimate),
        rather than the confidence interval of the cross-world mean.

        Items are ranked by ``metric``. The default is per-copy value. Pass
        metric="family_total" explicitly to compare family totals instead.
        The mean is shown as a marker inside each distribution; the
        box/median/whiskers are the standard matplotlib boxplot summary (IQR
        with 1.5x-IQR whiskers).

        By default, outlier points are hidden and the x-axis is limited to the
        non-outlier whisker range. A mean beyond that display range is marked
        with a triangle at the edge; the data and ranking are not changed.
        Pass show_outliers=True to display the complete distribution range.
        """
        metric = self._resolve_metric(metric)
        if not show:
            # Batch/CI runs must not require a working Tk installation or a
            # desktop display merely to save a PNG.
            plt.switch_backend("Agg")
        ranked = self.top_items(top_n, metric=metric)
        ranked = ranked.sort_values(metric, ascending=True)

        item_names = ranked["item"].tolist()
        world_values = {item: [] for item in item_names}

        # Use the actual per-world item estimates. Missing items are omitted,
        # preserving the project's conditional-on-existence convention.
        wanted = set(item_names)
        for wr in self.world_results:
            frame = wr.results
            subset = frame[frame["item"].isin(wanted)]
            for row in subset.itertuples(index=False):
                world_values[row.item].append(float(getattr(row, metric)))

        distributions = [world_values[item] for item in item_names]

        fig_height = max(6.0, 0.34 * len(item_names) + 2.0)
        fig, ax = plt.subplots(figsize=(11, fig_height))

        ax.boxplot(
            distributions,
            vert=False,
            tick_labels=item_names,
            showmeans=True,
            showfliers=show_outliers,
        )

        ax.axvline(
            x=break_even,
            color="black",
            linestyle="--",
            linewidth=2,
            label=f"{break_even:g} check (break-even)",
        )
        metric_label = (
            "Family-total allocation"
            if metric == "family_total"
            else "Per-copy allocation"
        )
        ax.set_xlabel(
            f"{metric_label} across generated worlds "
            "(box = IQR, whiskers = 1.5×IQR, marker = mean)"
        )
        ax.set_ylabel("Item")
        ax.set_title(
            f"{self.game} Archipelago Item Importance Across Worlds\n"
            f"{self.world_samples} world sample(s), "
            f"{self.shapley_pairs * 2} permutations/world"
        )

        if not show_outliers:
            whisker_lows = []
            whisker_highs = []
            means = []
            for values in distributions:
                array = np.asarray(values, dtype=float)
                q1, q3 = np.percentile(array, [25, 75])
                iqr = q3 - q1
                inside = array[
                    (array >= q1 - 1.5 * iqr)
                    & (array <= q3 + 1.5 * iqr)
                ]
                whisker_lows.append(float(inside.min()))
                whisker_highs.append(float(inside.max()))
                means.append(float(array.mean()))

            display_low = min(min(whisker_lows), break_even)
            display_high = max(max(whisker_highs), break_even)
            span = max(display_high - display_low, 1.0)
            padding = span * 0.03
            x_low = max(0.0, display_low - padding)
            x_high = display_high + padding
            ax.set_xlim(x_low, x_high)

            clipped_label_used = False
            for y, mean in enumerate(means, start=1):
                if mean > x_high:
                    ax.plot(
                        x_high,
                        y,
                        marker=">",
                        color="C2",
                        clip_on=False,
                        label=(
                            "mean beyond displayed range"
                            if not clipped_label_used
                            else None
                        ),
                    )
                    clipped_label_used = True

            ax.set_xlabel(
                f"{metric_label} across generated worlds "
                "(outliers hidden; axis scaled to 1.5x-IQR whiskers)"
            )

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

    # ------------------------------------------------------------------
    # Item audit
    # ------------------------------------------------------------------

    def audit_item(self, item_name: str, top_n: int | None = 30):
        if item_name not in self.audit.index:
            raise ValueError(f"{item_name!r} is not a Shapley item.")

        result_row = self.results.loc[
            self.results["item"] == item_name
        ].iloc[0]

        contributions = (
            self.audit.loc[item_name]
            .loc[lambda x: x > 0]
            .sort_values(ascending=False)
        )

        df = contributions.reset_index()
        df.columns = ["location", "per_copy_credit"]

        mean_copies = float(result_row["mean_copies"])
        df["approx_family_credit"] = df["per_copy_credit"] * mean_copies
        df["approx_percent_of_check"] = df["approx_family_credit"] * 100

        print(f"=== {item_name} ===")
        print(f"Worlds present: {int(result_row['worlds_present'])}/{self.world_samples}")
        print(f"Mean copies when present: {mean_copies:.2f}")
        print(
            "Per-copy Shapley value: "
            f"{result_row['shapley_per_copy']:.3f} "
            f"± {result_row['ci95']:.3f}"
        )
        print(f"Mean family total: {result_row['family_total']:.3f}")
        print(
            "Sum of audited locations (per copy): "
            f"{contributions.sum():.3f}"
        )

        return df.head(top_n) if top_n is not None else df

    def plot_audit(
        self,
        item_name: str,
        top_n: int = 25,
        percent: bool = False,
    ):
        if item_name not in self.audit.index:
            raise ValueError(f"{item_name!r} is not a Shapley item.")

        contributions = (
            self.audit.loc[item_name]
            .loc[lambda x: x > 0]
            .sort_values(ascending=False)
        )

        total = contributions.sum()

        if percent:
            values = contributions / total * 100
            xlabel = "Percent of item's total Shapley value"
        else:
            values = contributions
            xlabel = "Contribution to per-copy Shapley value"

        plot_data = values.head(top_n).sort_values(ascending=True)

        plt.figure(figsize=(11, 9))
        plt.barh(plot_data.index, plot_data.values)
        plt.xlabel(xlabel)
        plt.ylabel("Location")
        plt.title(
            f"Where does {item_name}'s value come from?\n"
            f"Mean total Shapley value = {total:.3f}"
        )
        plt.tight_layout()
        plt.show()

    # ------------------------------------------------------------------
    # Location-centric audit
    # ------------------------------------------------------------------

    def audit_location(self, location_name: str, top_n: int | None = None):
        if location_name not in self.audit.columns:
            raise ValueError(
                f"{location_name!r} is not a scored location."
            )

        rows = []

        for item_name in self.audit.index:
            per_copy = float(self.audit.loc[item_name, location_name])
            if per_copy <= 0:
                continue

            result_row = self.results.loc[
                self.results["item"] == item_name
            ].iloc[0]
            mean_copies = float(result_row["mean_copies"])
            family_credit = per_copy * mean_copies

            rows.append({
                "item": item_name,
                "mean_copies": mean_copies,
                "per_copy_credit": per_copy,
                "approx_family_credit": family_credit,
                "approx_percent_of_check": family_credit * 100,
            })

        df = pd.DataFrame(rows)

        if df.empty:
            return df

        df = df.sort_values(
            "approx_family_credit",
            ascending=False,
        ).reset_index(drop=True)

        return df.head(top_n) if top_n is not None else df

    def display_location_audit(
        self,
        location_name: str,
        top_n: int | None = None,
        max_height: int = 480,
    ):
        """Display a location audit in a scrollable notebook panel.

        ``audit_location()`` remains the programmatic DataFrame API. This
        helper only changes notebook presentation and returns the underlying
        DataFrame so callers can continue using the values afterward.
        """
        if max_height <= 0:
            raise ValueError("max_height must be a positive number of pixels.")

        df = self.audit_location(location_name, top_n=top_n)

        try:
            from IPython.display import HTML, display
        except ImportError as exc:
            raise RuntimeError(
                "display_location_audit() requires IPython/Jupyter. "
                "Use audit_location() to obtain the DataFrame directly."
            ) from exc

        title = html.escape(location_name)
        table = df.to_html(index=False, border=0, classes="location-audit-table")
        display(HTML(
            f"""
            <div><strong>{title}</strong></div>
            <div style="max-height: {int(max_height)}px; overflow-y: auto; "
                       "border: 1px solid #ddd; margin-top: 0.4em;">
              <style>
                .location-audit-table {{ border-collapse: collapse; width: 100%; }}
                .location-audit-table th {{ position: sticky; top: 0; z-index: 1;
                                            background: var(--jp-layout-color1, white); }}
                .location-audit-table th, .location-audit-table td {{
                    padding: 0.35em 0.6em; border-bottom: 1px solid #ddd;
                    text-align: right;
                }}
                .location-audit-table th:first-child,
                .location-audit-table td:first-child {{ text-align: left; }}
              </style>
              {table}
            </div>
            """
        ))
        return df

    # ------------------------------------------------------------------
    # World-specific debugging
    # ------------------------------------------------------------------

    def show_location_logic(
        self,
        location_name: str,
        depth: int = 3,
        world_index: int = 0,
    ):
        if not 0 <= world_index < self.world_samples:
            raise IndexError(
                f"world_index must be between 0 and {self.world_samples - 1}."
            )

        world_result = self.world_results[world_index]
        logic_world = world_result.logic_world

        # Parallel workers intentionally return only lightweight numeric data.
        # Rebuild one requested world lazily for debugging, using the exact same
        # resolved options and world seed. This does not affect the analysis.
        if logic_world is None:
            from .builders import _build_oot

            logic_world = _build_oot(
                options=self.options,
                world_seed=world_result.world_seed,
                player=self.player,
            )
            world_result.logic_world = logic_world
            if world_index == 0:
                self.logic_world = logic_world

        method = getattr(logic_world, "show_location_logic", None)

        if method is None:
            raise NotImplementedError(
                "This game wrapper does not expose human-readable "
                "location rule strings."
            )

        if self.world_samples > 1:
            print(
                f"Showing logic for world sample {world_index + 1}/"
                f"{self.world_samples}"
            )

        return method(location_name, depth=depth)
