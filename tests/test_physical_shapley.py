from __future__ import annotations

from collections import Counter
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "Archipelago"))

from ap_shapley.analyzer import ShapleyAnalyzer
from ap_shapley.logic_world import LogicWorld
from ap_shapley.result import AnalysisResult
from BaseClasses import Item, ItemClassification


class ToyItem:
    def __init__(self, name: str):
        self.name = name


class ToyLocation:
    def __init__(self, name: str, rule):
        self.name = name
        self._rule = rule

    def can_reach(self, state):
        return bool(self._rule(state))


class ToyLogicWorld:
    """Minimal generic LogicWorld protocol for physical Shapley tests."""

    def __init__(self, copies: dict[str, int], rules: list):
        self.shapley_items = [
            ToyItem(name)
            for name, count in copies.items()
            for _ in range(count)
        ]
        self.item_counts = Counter(item.name for item in self.shapley_items)
        self.check_locations = [
            ToyLocation(f"check_{index}", rule)
            for index, rule in enumerate(rules)
        ]

    def make_state(self):
        return Counter(), set()

    @staticmethod
    def reachable_checks(state, locations):
        return {location for location in locations if location.can_reach(state)}

    @staticmethod
    def offer_item(state, item, pending_items, remaining_events):
        state[item.name] += 1

    @staticmethod
    def is_complete(state):
        return False

    def describe(self):
        return {
            "game": "Toy",
            "player": 1,
            "scored_checks": len(self.check_locations),
            "automatic_logic_events": 0,
            "shapley_item_copies": len(self.shapley_items),
            "distinct_item_families": len(self.item_counts),
            "constrained_item_copies": 0,
            "constrained_item_families": 0,
        }


def analyze(copies, rules, pairs=20_000, seed=1234):
    return ShapleyAnalyzer(
        ToyLogicWorld(copies, rules),
        release_on_goal=False,
    ).run(shapley_pairs=pairs, sampling_seed=seed)


def family_totals(result):
    return result.results.set_index("item")["family_total"].to_dict()


class PhysicalShapleyTests(unittest.TestCase):
    def assert_conserved(self, result):
        summary = result.summary()
        self.assertAlmostEqual(summary["conservation_difference"], 0.0)
        self.assertAlmostEqual(
            result.results["family_total"].sum(),
            summary["item_dependent_checks"],
        )

    def test_duplicate_substitute_replication_effect(self):
        rule = lambda s: s["A"] >= 1 and s["B"] >= 1
        result = analyze({"A": 1, "B": 2}, [rule])
        totals = family_totals(result)

        self.assertAlmostEqual(totals["A"], 2 / 3, delta=0.01)
        self.assertAlmostEqual(totals["B"], 1 / 3, delta=0.01)
        self.assertAlmostEqual(
            result.results.set_index("item").loc["B", "shapley_per_copy"],
            totals["B"] / 2,
        )
        self.assert_conserved(result)

    def test_progressive_copies_interleave_with_other_items(self):
        # stage2 OR (stage1 AND Y). Y is pivotal precisely when it appears
        # between the two physical Progressive copies, which family blocks
        # would make impossible.
        rule = lambda s: (
            s["Progressive"] >= 2
            or (s["Progressive"] >= 1 and s["Y"] >= 1)
        )
        result = analyze({"Progressive": 2, "Y": 1}, [rule])
        totals = family_totals(result)

        self.assertAlmostEqual(totals["Y"], 1 / 3, delta=0.01)
        self.assertGreater(totals["Y"], 0.0)
        self.assertAlmostEqual(totals["Progressive"], 2 / 3, delta=0.01)
        self.assert_conserved(result)

    def test_count_threshold_interleaves_with_other_items(self):
        # Count 3 OR (Count 2 AND Y). Y receives credit when two counted
        # copies precede it and the third follows it.
        rule = lambda s: (
            s["Counted"] >= 3
            or (s["Counted"] >= 2 and s["Y"] >= 1)
        )
        result = analyze({"Counted": 3, "Y": 1}, [rule])
        totals = family_totals(result)

        self.assertAlmostEqual(totals["Y"], 1 / 4, delta=0.01)
        self.assertAlmostEqual(totals["Counted"], 3 / 4, delta=0.01)
        self.assert_conserved(result)

    def test_automatic_event_cascade_credits_physical_key(self):
        class EventToyLogicWorld(ToyLogicWorld):
            def __init__(self):
                super().__init__(
                    {"Physical Key A": 1},
                    [lambda state: state["Event Token E"] >= 1] * 5,
                )
                self.event = ToyLocation(
                    "Event Location E",
                    lambda state: state["Physical Key A"] >= 1,
                )

            def make_state(self):
                return Counter(), {self.event}

            def offer_item(self, state, item, pending_items, remaining_events):
                state[item.name] += 1
                if self.event in remaining_events and self.event.can_reach(state):
                    state["Event Token E"] += 1
                    remaining_events.remove(self.event)

            def describe(self):
                description = super().describe()
                description["automatic_logic_events"] = 1
                return description

        world = EventToyLogicWorld()
        self.assertEqual([item.name for item in world.shapley_items], ["Physical Key A"])
        result = ShapleyAnalyzer(world, release_on_goal=False).run(
            shapley_pairs=1,
            sampling_seed=1,
        )
        self.assertEqual(family_totals(result), {"Physical Key A": 5.0})
        self.assert_conserved(result)

    def test_source_bound_physical_item_remains_pending_player(self):
        class SourceBoundToyLogicWorld(ToyLogicWorld):
            def __init__(self):
                super().__init__(
                    {"Physical Key A": 1, "Physical Item B": 1},
                    [lambda state: state["Physical Item B"] >= 1],
                )
                self.source = ToyLocation(
                    "Location L",
                    lambda state: state["Physical Key A"] >= 1,
                )

            def offer_item(self, state, item, pending_items, remaining_events):
                if item.name == "Physical Item B" and not self.source.can_reach(state):
                    pending_items.append(item)
                else:
                    state[item.name] += 1
                for pending in list(pending_items):
                    if self.source.can_reach(state):
                        state[pending.name] += 1
                        pending_items.remove(pending)

            def describe(self):
                description = super().describe()
                description["constrained_item_copies"] = 1
                description["constrained_item_families"] = 1
                return description

        world = SourceBoundToyLogicWorld()
        self.assertIn("Physical Item B", [item.name for item in world.shapley_items])
        state, events = world.make_state()
        pending = []
        item_b = next(item for item in world.shapley_items if item.name == "Physical Item B")
        world.offer_item(state, item_b, pending, events)
        self.assertEqual([item.name for item in pending], ["Physical Item B"])

        result = ShapleyAnalyzer(world, release_on_goal=False).run(
            shapley_pairs=1,
            sampling_seed=1,
        )
        self.assertAlmostEqual(sum(family_totals(result).values()), 1.0)
        self.assert_conserved(result)

    def test_relevant_family_includes_filler_classified_copies(self):
        class FamilyItem(Item):
            game = "Toy"

        items = [
            FamilyItem("F", ItemClassification.progression, 1, 1)
            for _ in range(60)
        ] + [
            FamilyItem("F", ItemClassification.filler, 1, 1)
            for _ in range(40)
        ] + [FamilyItem("Unrelated filler", ItemClassification.filler, 2, 1)]

        class ItemMultiworld:
            @staticmethod
            def get_items():
                return items

        selector = LogicWorld.__new__(LogicWorld)
        selector.player = 1
        selector.multiworld = ItemMultiworld()
        selected = selector._find_candidate_items()

        self.assertEqual(len(selected), 100)
        self.assertNotIn("Unrelated filler", {item.name for item in selected})

        pure = analyze(
            {"F": len(selected)},
            [lambda state: state["F"] >= 10],
            pairs=100,
        )
        self.assertAlmostEqual(family_totals(pure)["F"], 1.0)
        self.assert_conserved(pure)

        complemented = analyze(
            {"F": len(selected), "A": 1},
            [lambda state: state["A"] >= 1 and state["F"] >= 10],
            pairs=10_000,
        )
        totals = family_totals(complemented)
        self.assertAlmostEqual(totals["F"], 10 / 101, delta=0.01)
        self.assertAlmostEqual(totals["A"], 91 / 101, delta=0.01)
        self.assert_conserved(complemented)

    def test_conservation_across_multiple_checks(self):
        rules = [
            lambda s: s["A"] >= 1,
            lambda s: s["B"] >= 2,
            lambda s: s["A"] >= 1 and s["B"] >= 1,
        ]
        result = analyze({"A": 1, "B": 3, "C": 2}, rules, pairs=2_000)
        self.assert_conserved(result)
        self.assertEqual(result.summary()["item_dependent_checks"], 3)

    def test_tables_and_plots_default_to_per_copy_metric(self):
        world_result = analyze(
            {"A": 1, "B": 2},
            [lambda s: s["A"] >= 1 and s["B"] >= 1],
            pairs=2_000,
        )
        aggregate = AnalysisResult([world_result], seed=1234, options={})

        self.assertEqual(aggregate._resolve_metric(None), "shapley_per_copy")
        displayed = aggregate.top_items(2)
        expected = aggregate.results.sort_values(
            "shapley_per_copy", ascending=False
        ).reset_index(drop=True)
        self.assertEqual(displayed["item"].tolist(), expected["item"].tolist())


if __name__ == "__main__":
    unittest.main()
