from __future__ import annotations

import importlib

from .logic_world import LogicWorld


class DSRLogicWorld(LogicWorld):
    """Dark Souls Remastered event classification from APWorld metadata."""

    def _item_data(self, item):
        items_module = importlib.import_module(item.__class__.__module__)
        return items_module.item_dictionary.get(item.name), items_module

    def is_automatic_event_item(self, item, source_location) -> bool:
        if item is not None and item.player == self.player:
            item_data, items_module = self._item_data(item)
            if (
                item_data is not None
                and item_data.category == items_module.DSRItemCategory.EVENT
            ):
                return True
        return super().is_automatic_event_item(item, source_location)
