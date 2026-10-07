from __future__ import annotations

import random

import numpy as np
import pandas as pd

from .result import WorldAnalysisResult


class ShapleyAnalyzer:
    """Game-agnostic Monte Carlo Shapley estimator for one LogicWorld."""

    def __init__(
        self,
        logic_world,
        release_on_goal: bool = True,
    ):
        self.logic_world = logic_world
        self.release_on_goal = bool(release_on_goal)

    def _prepare_base_state(self):
        """
        Build the zero-Shapley-item state once for this world.

        Every permutation starts from exactly this state, so recomputing its
        automatic events and free checks for every permutation is wasted work.
        CollectionState.copy() gives each permutation an independent mutable
        state while preserving the already-computed reachability cache.
        """
        lw = self.logic_world
        base_state, base_remaining_events = lw.make_state()
        all_checks = set(lw.check_locations)
        free_checks = lw.reachable_checks(base_state, lw.check_locations)

        if self.release_on_goal and lw.is_complete(base_state):
            free_checks = all_checks

        dependent_checks = all_checks - free_checks
        return base_state, base_remaining_events, free_checks, dependent_checks

    def _evaluate_permutation(
        self,
        order,
        base_state,
        base_remaining_events,
        dependent_checks,
        family_to_index,
        location_to_index,
        n_locations,
    ):
        """
        Evaluate one item ordering.

        Return one compact integer vector: for each scored location, the index
        of the item family that made it reachable, or -1 if the location is
        free/unreachable.  This avoids constructing nested Counters/dicts for
        every permutation.
        """
        lw = self.logic_world
        state = base_state.copy()
        remaining_events = base_remaining_events.copy()
        remaining_checks = dependent_checks.copy()
        pending_items = []

        winners = np.full(n_locations, -1, dtype=np.int32)

        for item in order:
            # The permutation orders item *offers*. Ordinary items activate
            # immediately; structurally constrained items may stay pending
            # until their allowed source becomes reachable. Any acquisition
            # cascade caused by this offer belongs to this player's Shapley
            # marginal contribution.
            lw.offer_item(
                state,
                item,
                pending_items,
                remaining_events,
            )

            if self.release_on_goal and lw.is_complete(state):
                newly_reachable = remaining_checks
            else:
                newly_reachable = {
                    loc for loc in remaining_checks
                    if loc.can_reach(state)
                }

            if newly_reachable:
                family_index = family_to_index[item.name]
                for loc in newly_reachable:
                    winners[location_to_index[loc]] = family_index
                remaining_checks -= newly_reachable

                if not remaining_checks:
                    break

        return winners

    def _cached_sanity(
        self,
        base_state,
        base_remaining_events,
        free_checks,
    ):
        """Compute the full-inventory sanity check once and cache the result."""
        lw = self.logic_world

        full_state = base_state.copy()
        full_events = base_remaining_events.copy()
        full_pending = []
        for item in lw.shapley_items:
            lw.offer_item(
                full_state,
                item,
                full_pending,
                full_events,
            )
        normal_full_checks = lw.reachable_checks(full_state, lw.check_locations)

        all_checks = set(lw.check_locations)
        goal_reachable = lw.is_complete(full_state)

        if self.release_on_goal and goal_reachable:
            effective_full_checks = all_checks
        else:
            effective_full_checks = normal_full_checks

        return {
            "total_checks": len(all_checks),
            "free_checks": len(free_checks),
            "normally_reachable_with_all_items": len(normal_full_checks),
            "goal_reachable_with_all_items": goal_reachable,
            "pending_constrained_items_with_all_offered": len(full_pending),
            "reachable_with_all_items": len(effective_full_checks),
            "item_dependent_checks": len(effective_full_checks - free_checks),
            "still_unreachable": len(all_checks - effective_full_checks),
        }

    def run(
        self,
        shapley_pairs: int = 64,
        sampling_seed: int = 2026,
        progress_callback=None,
        world_seed: int | None = None,
    ):
        """
        Estimate per-copy Shapley values for one fixed logic world.

        Every sampled permutation is paired with its reverse, so
        shapley_pairs=64 evaluates 128 total permutations. Each initial order
        is uniform over all physical item-copy permutations.
        """
        if shapley_pairs < 1:
            raise ValueError("shapley_pairs must be >= 1")

        lw = self.logic_world
        rng = random.Random(sampling_seed)

        family_names = sorted(lw.item_counts)
        family_to_index = {
            name: i for i, name in enumerate(family_names)
        }
        family_copies = np.asarray(
            [lw.item_counts[name] for name in family_names],
            dtype=float,
        )
        n_families = len(family_names)

        locations = sorted(lw.check_locations, key=lambda loc: loc.name)
        location_names = [loc.name for loc in locations]
        location_to_index = {
            loc: i for i, loc in enumerate(locations)
        }
        n_locations = len(locations)
        location_indices = np.arange(n_locations, dtype=np.int32)

        (
            base_state,
            base_remaining_events,
            free_checks,
            dependent_checks,
        ) = self._prepare_base_state()

        # Sufficient statistics for one observation per antithetic pair.
        # We do not need to retain every raw pair sample just to compute means
        # and standard errors.
        pair_sum = np.zeros(n_families, dtype=float)
        pair_sum_sq = np.zeros(n_families, dtype=float)

        # Dense audit accumulation is small for AP-sized worlds and is much
        # cheaper than nested string-keyed dictionaries/Counters.
        audit_sum = np.zeros((n_families, n_locations), dtype=float)

        for _ in range(shapley_pairs):
            order = lw.shapley_items.copy()
            rng.shuffle(order)

            winners_a = self._evaluate_permutation(
                order=order,
                base_state=base_state,
                base_remaining_events=base_remaining_events,
                dependent_checks=dependent_checks,
                family_to_index=family_to_index,
                location_to_index=location_to_index,
                n_locations=n_locations,
            )
            winners_b = self._evaluate_permutation(
                order=list(reversed(order)),
                base_state=base_state,
                base_remaining_events=base_remaining_events,
                dependent_checks=dependent_checks,
                family_to_index=family_to_index,
                location_to_index=location_to_index,
                n_locations=n_locations,
            )

            valid_a = winners_a >= 0
            valid_b = winners_b >= 0

            family_credit_a = np.bincount(
                winners_a[valid_a],
                minlength=n_families,
            ).astype(float, copy=False)
            family_credit_b = np.bincount(
                winners_b[valid_b],
                minlength=n_families,
            ).astype(float, copy=False)

            pair_value = (
                family_credit_a + family_credit_b
            ) / (2.0 * family_copies)

            pair_sum += pair_value
            pair_sum_sq += pair_value * pair_value

            # Each location appears at most once per permutation, so these
            # advanced-index additions do not contain duplicate (row, col)
            # pairs within one update.
            if np.any(valid_a):
                fam = winners_a[valid_a]
                loc = location_indices[valid_a]
                audit_sum[fam, loc] += 0.5 / family_copies[fam]

            if np.any(valid_b):
                fam = winners_b[valid_b]
                loc = location_indices[valid_b]
                audit_sum[fam, loc] += 0.5 / family_copies[fam]

            if progress_callback is not None:
                progress_callback(1)

        means = pair_sum / shapley_pairs

        if shapley_pairs > 1:
            # Numerically stable enough at this scale; clamp tiny negative
            # roundoff before taking the square root.
            sample_var = (
                pair_sum_sq - shapley_pairs * means * means
            ) / (shapley_pairs - 1)
            sample_var = np.maximum(sample_var, 0.0)
            sampling_se = np.sqrt(sample_var / shapley_pairs)
            ci95 = 1.96 * sampling_se
        else:
            sampling_se = np.full(n_families, np.nan, dtype=float)
            ci95 = np.full(n_families, np.nan, dtype=float)

        results = pd.DataFrame({
            "item": family_names,
            "copies": family_copies.astype(int),
            "shapley_per_copy": means,
            "sampling_se": sampling_se,
            "ci95": ci95,
            "family_total": means * family_copies,
        }).sort_values(
            "shapley_per_copy",
            ascending=False,
        ).reset_index(drop=True)

        audit = pd.DataFrame(
            audit_sum / shapley_pairs,
            index=family_names,
            columns=location_names,
        )

        sanity = self._cached_sanity(
            base_state=base_state,
            base_remaining_events=base_remaining_events,
            free_checks=free_checks,
        )
        describe = lw.describe()
        allocated_points = float(results["family_total"].sum())
        expected_points = int(sanity["item_dependent_checks"])

        summary_data = {
            **describe,
            **sanity,
            "shapley_pairs": shapley_pairs,
            "total_permutations": shapley_pairs * 2,
            "sampling_seed": sampling_seed,
            "release_on_goal": self.release_on_goal,
            "allocated_shapley_points": allocated_points,
            "expected_shapley_points": expected_points,
            "conservation_difference": (
                allocated_points - expected_points
            ),
        }

        return WorldAnalysisResult(
            logic_world=lw,
            results=results,
            audit=audit,
            shapley_pairs=shapley_pairs,
            sampling_seed=sampling_seed,
            summary_data=summary_data,
            world_seed=world_seed,
        )
