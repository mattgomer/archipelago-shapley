import unittest

from ap_shapley.builders import _build_logic_world


class SA2BWorldTests(unittest.TestCase):
    def test_default_emblems_and_completion_event(self):
        logic_world = _build_logic_world(
            "Sonic Adventure 2 Battle",
            {},
            world_seed=2026,
        )
        counts = logic_world.item_family_classification_counts("Emblem")

        self.assertGreater(counts["total"], 0)
        self.assertGreater(counts["progression"], 0)
        self.assertGreater(counts["filler"], 0)
        self.assertEqual(counts["shapley"], counts["total"])
        self.assertNotIn(
            "What Maria Wanted",
            {item.name for item in logic_world.shapley_items},
        )
        self.assertEqual(
            [(loc.name, loc.item.name) for loc in logic_world.event_locations],
            [("Finalhazard", "What Maria Wanted")],
        )
        self.assertEqual(logic_world.describe()["constrained_item_copies"], 0)


if __name__ == "__main__":
    unittest.main()
