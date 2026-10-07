from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "Archipelago"))

from ap_shapley.builders import _adapter_for_game, _load_adapter_world_type
from ap_shapley.logic_world import LogicWorld
from ap_shapley.dsr_world import DSRLogicWorld


class GameRegistryTests(unittest.TestCase):
    def test_pokemon_rb_uses_generic_logic_world(self):
        adapter = _adapter_for_game("Pokemon Red and Blue")
        self.assertEqual(adapter["module"], "pokemon_rb")
        self.assertEqual(
            _load_adapter_world_type("Pokemon Red and Blue", adapter).__name__,
            "PokemonRedBlueWorld",
        )
        self.assertIs(adapter["logic_world_class"], LogicWorld)

    def test_sa2b_uses_generic_logic_world(self):
        adapter = _adapter_for_game("Sonic Adventure 2 Battle")
        self.assertEqual(adapter["module"], "sa2b")
        self.assertEqual(
            _load_adapter_world_type("Sonic Adventure 2 Battle", adapter).__name__,
            "SA2BWorld",
        )
        self.assertIs(adapter["logic_world_class"], LogicWorld)

    def test_new_builtins_are_discovered_without_adapters(self):
        expected = {
            "Paint": ("paint", "PaintWorld"),
            "Subnautica": ("subnautica", "SubnauticaWorld"),
            "Starcraft 2": ("sc2", "SC2World"),
            "Celeste (Open World)": ("celeste_open_world", "CelesteOpenWorld"),
        }
        for game, (module, class_name) in expected.items():
            with self.subTest(game=game):
                adapter = _adapter_for_game(game)
                self.assertEqual(adapter["module"], module)
                self.assertIs(adapter["logic_world_class"], LogicWorld)
                self.assertEqual(
                    _load_adapter_world_type(game, adapter).__name__, class_name
                )

    def test_manifestless_external_does_not_shadow_builtin(self):
        with patch(
            "ap_shapley.builders._read_apworld_manifest",
            return_value={},
        ):
            adapter = _adapter_for_game(
                "Paint",
                [r"C:\explicit\legacy.apworld"],
            )
        self.assertEqual(adapter["source"], "builtin")
        self.assertEqual(adapter["module"], "paint")

    def test_dsr_uses_narrow_event_metadata_adapter(self):
        with patch(
            "ap_shapley.builders._read_apworld_manifest",
            return_value={"game": "Dark Souls Remastered"},
        ):
            adapter = _adapter_for_game(
                "Dark Souls Remastered",
                [r"C:\explicit\dsr.apworld"],
            )
        self.assertIs(adapter["logic_world_class"], DSRLogicWorld)


if __name__ == "__main__":
    unittest.main()
