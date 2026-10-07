from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "Archipelago"))

from ap_shapley.builders import _build_logic_world


class GenericLifecycleTests(unittest.TestCase):
    def test_start_inventory_from_pool_is_base_state_not_player(self):
        logic_world = _build_logic_world(
            "Paint",
            {"start_inventory_from_pool": {"Pick Color": 1}},
            world_seed=2026,
        )

        precollected = logic_world.multiworld.precollected_items[1]
        self.assertIn("Pick Color", [item.name for item in precollected])
        self.assertNotIn("Pick Color", [item.name for item in logic_world.shapley_items])

        base_state, _ = logic_world.make_state()
        self.assertTrue(base_state.has("Pick Color", 1))


if __name__ == "__main__":
    unittest.main()
