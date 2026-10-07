from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "Archipelago"))

from ap_shapley.combined import combine_analysis_tables, plot_combined_analyses


class CombinedAnalysisTests(unittest.TestCase):
    def _analysis(self, game, item, values):
        worlds = [
            SimpleNamespace(results=pd.DataFrame({
                "item": [item],
                "shapley_per_copy": [value],
                "family_total": [value],
            }))
            for value in values
        ]
        return SimpleNamespace(
            game=game,
            results=pd.DataFrame({
                "item": [item],
                "shapley_per_copy": [sum(values) / len(values)],
                "family_total": [sum(values) / len(values)],
            }),
            world_results=worlds,
        )

    def test_combines_and_plots_independent_game_results(self):
        results = {
            "a.yaml": self._analysis("Game A", "Key", [1.0, 2.0]),
            "b.yaml": self._analysis("Game B", "Key", [3.0, 4.0]),
        }
        table = combine_analysis_tables(results)
        self.assertEqual(len(table), 2)
        self.assertEqual(set(table["game"]), {"Game A", "Game B"})
        self.assertEqual(
            set(table["game_item"]),
            {"Key — Game A", "Key — Game B"},
        )
        figure = plot_combined_analyses(results, show=False)
        self.assertEqual(len(figure.axes), 1)


if __name__ == "__main__":
    unittest.main()
