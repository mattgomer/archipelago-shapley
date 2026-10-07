from __future__ import annotations

from .logic_world import LogicWorld


class OOTLogicWorld(LogicWorld):
    """Ocarina of Time-specific world wrapper."""

    def canonicalize_item(self, item):
        if item.name == "Bottle" or item.name.startswith("Bottle with "):
            return self.world.create_item("Bottle")
        return item

    def item_family_name(self, item) -> str:
        if item.name == "Bottle" or item.name.startswith("Bottle with "):
            return "Bottle"
        return item.name

    def is_shapley_family_member(self, item):
        if item is not None and item.player == self.player:
            if getattr(item, "type", None) == "DungeonReward":
                return True
        return super().is_shapley_family_member(item)

    def is_shapley_item(self, item):
        # OoT dungeon rewards have code=None, but they are genuine inventory
        # items. They must be sampled as Shapley players rather than being
        # auto-collected from whichever boss happens to hold them.
        if (
            item is not None
            and item.player == self.player
            and getattr(item, "type", None) == "DungeonReward"
        ):
            return item.advancement or item.useful

        return super().is_shapley_item(item)

    def acquisition_source_for_item(self, raw_item, source_location):
        """
        OoT dungeon rewards are structurally restricted to the generated
        boss-reward / Link's Pocket assignment. Preserve that source even if
        Archipelago's location metadata does not expose it as a generic locked
        pre-fill placement.
        """
        if (
            source_location is not None
            and getattr(raw_item, "type", None) == "DungeonReward"
        ):
            return source_location

        return super().acquisition_source_for_item(
            raw_item,
            source_location,
        )

    def is_automatic_event_location(self, loc):
        # Boss rewards / Link's Pocket rewards are item placements, not
        # automatic logical events for this analysis.
        if (
            loc.item is not None
            and getattr(loc.item, "type", None) == "DungeonReward"
        ):
            return False

        return super().is_automatic_event_location(loc)

    def show_location_logic(self, location_name: str, depth: int = 3):
        loc = self.multiworld.get_location(location_name, self.player)

        print("=" * 78)
        print(f"LOCATION:    {loc.name}")
        print(f"REGION:      {loc.parent_region.name}")
        print("DIRECT RULE:", getattr(loc, "rule_string", "<dynamic/no rule string>"))
        print("=" * 78)
        print()
        print("REGION ACCESS PATHS:")

        visited = set()

        def trace_region(region, remaining_depth: int, indent: str = ""):
            if remaining_depth < 0:
                return
            if region.name in visited:
                print(f"{indent}[{region.name}] (already shown)")
                return
            visited.add(region.name)
            print(f"{indent}REGION: {region.name}")
            if not region.entrances:
                print(f"{indent}  No incoming entrances")
                return
            for entrance in region.entrances:
                source = entrance.parent_region.name if entrance.parent_region else "<none>"
                rule = getattr(entrance, "rule_string", "<dynamic/no rule string>")
                print(f"{indent}  FROM: {source}")
                print(f"{indent}    via:  {entrance.name}")
                print(f"{indent}    rule: {rule}")
                if remaining_depth > 0 and entrance.parent_region is not None:
                    trace_region(entrance.parent_region, remaining_depth - 1, indent + "      ")

        trace_region(loc.parent_region, depth)
