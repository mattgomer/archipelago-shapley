import unittest

from ap_shapley.builders import _build_logic_world


BADGES = {
    "Boulder Badge",
    "Cascade Badge",
    "Thunder Badge",
    "Rainbow Badge",
    "Soul Badge",
    "Marsh Badge",
    "Volcano Badge",
    "Earth Badge",
}


class PokemonBadgePlacementTests(unittest.TestCase):
    def _badge_rows(self, badgesanity: bool):
        logic_world = _build_logic_world(
            game_name="Pokemon Red and Blue",
            options={
                "badgesanity": badgesanity,
                "door_shuffle": "off",
            },
            world_seed=2026,
        )
        return logic_world, logic_world.acquisition_source_diagnostics(BADGES)

    def test_badgesanity_badges_are_unrestricted(self):
        logic_world, rows = self._badge_rows(True)
        self.assertEqual(len(rows), 8)
        self.assertEqual(sum(row["source_locked"] for row in rows), 0)
        self.assertEqual(
            sum(
                logic_world.is_acquisition_constrained(item)
                for item in logic_world.shapley_items
                if item.name in BADGES
            ),
            0,
        )

    def test_non_badgesanity_badges_are_locked_to_gyms(self):
        logic_world, rows = self._badge_rows(False)
        self.assertEqual(len(rows), 8)
        self.assertEqual(sum(row["source_locked"] for row in rows), 8)
        self.assertEqual(len({row["source_location"] for row in rows}), 8)
        self.assertTrue(all("Gym" in row["source_location"] for row in rows))


if __name__ == "__main__":
    unittest.main()
