"""Print DSR physical/event/source-bound classification for one generated world."""

from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "Archipelago"))


def main() -> None:
    from ap_shapley.builders import (
        _adapter_for_game,
        _load_adapter_world_type,
        _setup_single_multiworld,
    )
    from ap_shapley.yaml_config import read_player_yaml, roll_yaml_options

    yaml_path = PROJECT_ROOT / "batch_yamls" / "dsr.yaml"
    apworld_path = PROJECT_ROOT / "external_apworlds" / "dsr.apworld"
    weights = read_player_yaml(yaml_path)
    adapter = _adapter_for_game("Dark Souls Remastered", [apworld_path])
    world_type = _load_adapter_world_type("Dark Souls Remastered", adapter)
    options = roll_yaml_options(weights, world_type, roll_seed=2026)
    multiworld = _setup_single_multiworld(world_type, options, seed=2026)
    logic_world = adapter["logic_world_class"](multiworld)

    description = logic_world.describe()
    print("DSR classification summary")
    for key in (
        "shapley_item_copies",
        "distinct_item_families",
        "automatic_logic_events",
        "constrained_item_copies",
        "constrained_item_families",
    ):
        print(f"{key:30s} {description[key]}")

    print("\nBell diagnostics")
    names = {
        "Bell Gargoyles Defeated",
        "Bell of Awakening #1",
        "Bell of Awakening #2",
    }
    for row in logic_world.item_classification_diagnostics(names):
        print(row)


if __name__ == "__main__":
    main()
