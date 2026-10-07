from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "Archipelago"))

from ap_shapley.builders import (
    _adapter_for_game,
    _load_adapter_world_type,
    _setup_single_multiworld,
)
from ap_shapley.yaml_config import read_player_yaml, roll_yaml_options


class DSREventClassificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        yaml_path = PROJECT_ROOT / "batch_yamls" / "dsr.yaml"
        apworld_path = PROJECT_ROOT / "external_apworlds" / "dsr.apworld"
        if not yaml_path.is_file() or not apworld_path.is_file():
            raise unittest.SkipTest(
                "DSR integration test requires the private YAML and exact local APWorld"
            )
        weights = read_player_yaml(yaml_path)
        adapter = _adapter_for_game("Dark Souls Remastered", [apworld_path])
        world_type = _load_adapter_world_type("Dark Souls Remastered", adapter)
        options = roll_yaml_options(weights, world_type, roll_seed=2026)
        multiworld = _setup_single_multiworld(world_type, options, seed=2026)
        cls.logic_world = adapter["logic_world_class"](multiworld)

    def test_bells_are_automatic_events_not_players(self):
        names = {
            "Bell Gargoyles Defeated",
            "Bell of Awakening #1",
            "Bell of Awakening #2",
        }
        rows = self.logic_world.item_classification_diagnostics(names)
        self.assertEqual({row["item"] for row in rows}, names)
        for row in rows:
            self.assertEqual(row["item_category"], "EVENT")
            self.assertTrue(row["locked"])
            self.assertFalse(row["shapley_player"])
            self.assertTrue(row["automatic_event"])

    def test_all_instantiated_dsr_event_items_are_excluded(self):
        event_rows = [
            row for row in self.logic_world.item_classification_diagnostics()
            if row["item_category"] == "EVENT"
        ]
        self.assertEqual(len(event_rows), 32)
        self.assertTrue(all(row["automatic_event"] for row in event_rows))
        self.assertTrue(all(not row["shapley_player"] for row in event_rows))

    def test_locked_physical_progression_remains_constrained(self):
        description = self.logic_world.describe()
        self.assertEqual(description["constrained_item_copies"], 4)
        constrained = {
            row["item"]
            for row in self.logic_world.item_classification_diagnostics()
            if row["source_constrained_physical"]
        }
        self.assertEqual(
            constrained,
            {
                "Dungeon Cell Key",
                "Estus Flask",
                "Undead Asylum F2 East Key",
                "Big Pilgrim's Key",
            },
        )
