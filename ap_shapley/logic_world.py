from __future__ import annotations

from collections import Counter
import importlib
from typing import Iterable

from BaseClasses import CollectionState


class LogicWorld:
    """Thin, game-agnostic wrapper around one Archipelago player's logic world."""

    def __init__(self, multiworld, player: int = 1):
        self.multiworld = multiworld
        self.player = player
        self.world = multiworld.worlds[player]

        self.check_locations = self._find_check_locations()
        self.event_locations = self._find_event_locations()
        self._automatic_event_item_ids = {
            id(loc.item) for loc in self.event_locations
            if loc.item is not None
        }

        # Preserve the source location of already-placed items before any
        # canonicalization. At the pre-fill stage, locked placements generally
        # represent structural acquisition constraints rather than ordinary
        # random item placement.
        raw_source_locations = {
            id(loc.item): loc
            for loc in self.multiworld.get_locations(self.player)
            if loc.item is not None
        }

        raw_items = self._find_candidate_items()
        self.shapley_items = []
        self._acquisition_sources = {}

        for raw_item in raw_items:
            item = self.canonicalize_item(raw_item)
            self.shapley_items.append(item)

            source_location = raw_source_locations.get(id(raw_item))
            source = self.acquisition_source_for_item(
                raw_item,
                source_location,
            )
            if source is not None:
                self._acquisition_sources[id(item)] = source

        self.item_counts = Counter(item.name for item in self.shapley_items)

    def _find_check_locations(self):
        return [
            loc for loc in self.multiworld.get_locations(self.player)
            if isinstance(loc.address, int)
        ]

    def is_shapley_item(self, item):
        """
        Return True if this item should be a Shapley player.

        Games can override this when real inventory items use unusual
        Archipelago metadata (for example OoT dungeon rewards have code=None).
        """
        return (
            self.is_shapley_family_member(item)
            and (item.advancement or item.useful)
        )

    def is_shapley_family_member(self, item):
        """Return whether an item can be a physical member of a Shapley family."""
        return (
            item is not None
            and item.player == self.player
            and item.code is not None
            and id(item) not in getattr(self, "_automatic_event_item_ids", set())
        )

    def item_family_name(self, item) -> str:
        """Canonical family identity shared by selection and reporting."""
        return item.name

    def is_automatic_event_item(self, item, source_location) -> bool:
        """Return whether a placed item is internal state, not inventory.

        The generic AP convention uses an address-less location and code-less
        progression item. Some external APWorlds give event items normal IDs;
        adapters can override this using authoritative game metadata.
        """
        return (
            source_location is not None
            and source_location.address is None
            and item is not None
            and item.player == self.player
            and item.code is None
            and item.advancement
        )

    def is_automatic_event_location(self, loc):
        """
        Return True if reaching this location should automatically collect
        its item as an internal logical event.
        """
        return self.is_automatic_event_item(loc.item, loc)

    def _find_event_locations(self):
        return [
            loc for loc in self.multiworld.get_locations(self.player)
            if self.is_automatic_event_location(loc)
        ]

    def _find_candidate_items(self):
        physical_items = [
            item for item in self.multiworld.get_items()
            if self.is_shapley_family_member(item)
        ]
        relevant_families = {
            self.item_family_name(item)
            for item in physical_items
            if self.is_shapley_item(item)
        }
        return [
            item for item in physical_items
            if self.item_family_name(item) in relevant_families
        ]

    def canonicalize_item(self, item):
        return item

    def item_family_classification_counts(self, item_name: str):
        """Count physical copies by AP classification within one family."""
        from BaseClasses import ItemClassification

        counts = {
            "total": 0,
            "progression": 0,
            "useful": 0,
            "filler": 0,
            "trap": 0,
            "shapley": 0,
        }
        for item in self.multiworld.get_items():
            if (
                item is None
                or item.player != self.player
                or self.item_family_name(item) != item_name
            ):
                continue
            counts["total"] += 1
            classification = item.classification
            if classification & ItemClassification.progression:
                counts["progression"] += 1
            if classification & ItemClassification.useful:
                counts["useful"] += 1
            if classification & ItemClassification.trap:
                counts["trap"] += 1
            if classification == ItemClassification.filler:
                counts["filler"] += 1
        counts["shapley"] = int(self.item_counts.get(item_name, 0))
        return counts

    # ------------------------------------------------------------------
    # Acquisition constraints
    # ------------------------------------------------------------------

    def acquisition_source_for_item(self, raw_item, source_location):
        """
        Return a location that must be reachable before an offered Shapley
        item can actually enter inventory, or None for an unconstrained item.

        Generic default:
        preserve a source only when Archipelago has already locked the item to
        that location during world construction/pre-fill. Ordinary shuffled
        pool items remain placement-independent.

        Game adapters can override this for special placement systems that do
        not expose themselves through Location.locked.
        """
        if (
            source_location is not None
            and getattr(source_location, "locked", False)
        ):
            return source_location
        return None

    def acquisition_source(self, item):
        return self._acquisition_sources.get(id(item))

    def acquisition_source_diagnostics(self, item_names=None):
        """Describe generated sources for Shapley items.

        This is intentionally game-agnostic and is useful when validating
        whether an AP generation phase produced structural locked placements.
        """
        wanted = set(item_names) if item_names is not None else None
        rows = []
        for item in self.shapley_items:
            if wanted is not None and item.name not in wanted:
                continue
            source = self.acquisition_source(item)
            rows.append({
                "item": item.name,
                "source_location": (
                    source.name if source is not None else None
                ),
                "source_locked": (
                    bool(source.locked) if source is not None else False
                ),
            })
        return rows

    def item_category_name(self, item):
        """Best-effort category label for diagnostics only."""
        category = getattr(item, "category", None)
        if category is None:
            try:
                module = importlib.import_module(item.__class__.__module__)
                item_table = getattr(module, "item_dictionary", {})
                category = getattr(item_table.get(item.name), "category", None)
            except (ImportError, AttributeError):
                category = None
        return getattr(category, "name", str(category) if category is not None else None)

    @staticmethod
    def location_category_name(location):
        category = getattr(location, "category", None)
        return getattr(category, "name", str(category) if category is not None else None)

    def item_classification_diagnostics(self, item_names=None):
        """Describe physical, constrained, and automatic placed items."""
        wanted = set(item_names) if item_names is not None else None
        shapley_ids = {id(item) for item in self.shapley_items}
        rows = []
        for location in self.multiworld.get_locations(self.player):
            item = location.item
            if item is None or (wanted is not None and item.name not in wanted):
                continue
            rows.append({
                "item": item.name,
                "item_category": self.item_category_name(item),
                "source_location": location.name,
                "location_category": self.location_category_name(location),
                "location_address": location.address,
                "locked": bool(location.locked),
                "shapley_player": id(item) in shapley_ids,
                "automatic_event": location in self.event_locations,
                "source_constrained_physical": (
                    id(item) in shapley_ids
                    and self.is_acquisition_constrained(item)
                ),
            })
        return rows

    def is_acquisition_constrained(self, item) -> bool:
        return id(item) in self._acquisition_sources

    def can_activate_item(self, state, item) -> bool:
        source = self.acquisition_source(item)
        return source is None or source.can_reach(state)

    def close_pending_items(
        self,
        state,
        pending_items: list,
        remaining_events: set,
    ):
        """
        Compute the monotone acquisition closure of all currently offered
        constrained items.

        Items remain pending until their structural source becomes reachable.
        Activating one item can unlock logic events or additional pending
        items, so iterate to a fixed point.
        """
        changed = True
        while changed:
            changed = False

            for item in list(pending_items):
                if self.can_activate_item(state, item):
                    state.collect(item, prevent_sweep=True)
                    pending_items.remove(item)
                    changed = True

            if changed:
                self.sweep_logic_events(state, remaining_events)

    def offer_item(
        self,
        state,
        item,
        pending_items: list,
        remaining_events: set,
    ):
        """
        Add one Shapley player to the offered coalition and update the
        acquisition closure.

        Unconstrained items activate immediately. Constrained items remain
        pending until their source location can be reached.
        """
        if self.is_acquisition_constrained(item):
            pending_items.append(item)
        else:
            state.collect(item, prevent_sweep=True)
            self.sweep_logic_events(state, remaining_events)

        self.close_pending_items(
            state,
            pending_items,
            remaining_events,
        )

    def sweep_logic_events(self, state, remaining_events: set):
        changed = True
        while changed:
            changed = False
            for loc in list(remaining_events):
                if loc.can_reach(state):
                    state.collect(loc.item, prevent_sweep=True, location=loc)
                    remaining_events.remove(loc)
                    changed = True

    def make_state(self):
        state = CollectionState(self.multiworld)
        remaining_events = set(self.event_locations)
        self.sweep_logic_events(state, remaining_events)
        return state, remaining_events

    @staticmethod
    def reachable_checks(state, locations: Iterable):
        return {loc for loc in locations if loc.can_reach(state)}

    def is_complete(self, state) -> bool:
        condition = self.multiworld.completion_condition[self.player]
        return bool(condition(state))

    def sanity_check(self):
        empty_state, _ = self.make_state()
        free_checks = self.reachable_checks(empty_state, self.check_locations)

        full_state, full_events = self.make_state()
        for item in self.shapley_items:
            full_state.collect(item, prevent_sweep=True)
        self.sweep_logic_events(full_state, full_events)
        full_checks = self.reachable_checks(full_state, self.check_locations)

        all_checks = set(self.check_locations)
        return {
            "total_checks": len(all_checks),
            "free_checks": len(free_checks),
            "reachable_with_all_items": len(full_checks),
            "item_dependent_checks": len(full_checks - free_checks),
            "still_unreachable": len(all_checks - full_checks),
        }

    def describe(self):
        precollected = self.multiworld.precollected_items.get(self.player, [])
        return {
            "game": self.world.game,
            "player": self.player,
            "scored_checks": len(self.check_locations),
            "automatic_logic_events": len(self.event_locations),
            "precollected_item_count": len(precollected),
            "shapley_item_copies": len(self.shapley_items),
            "distinct_item_families": len(self.item_counts),
            "constrained_item_copies": len(self._acquisition_sources),
            "constrained_item_families": len({
                item.name
                for item in self.shapley_items
                if self.is_acquisition_constrained(item)
            }),
        }
