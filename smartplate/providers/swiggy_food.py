"""Swiggy Food through the Builders Club MCP: the one wired platform. Thin delegation to
integrations/swiggy_live.py (which owns the MCP calls, caching and safety checks)."""
from __future__ import annotations

from .. import config
from .base import Provider


class SwiggyFood(Provider):
    key = "swiggy_food"
    label = "Swiggy Food"
    wired = True
    attribution = "Powered by Swiggy"
    note = ("Swiggy Builders Club MCP. Swiggy is the data fiduciary: its data serves the user's "
            "current task only; no aggregation that hides Swiggy's brand, no competitive benchmarking.")

    @property
    def capabilities(self) -> frozenset[str]:          # follows the server's Swiggy mode
        mode = config.swiggy_mode()
        caps = {"demo": set(), "live_read": {"read", "history"}, "live_cart": {"read", "history", "cart"},
                "live_order": {"read", "history", "cart", "order"}}[mode]
        return frozenset(caps)

    def addresses(self, user_id):
        from ..integrations import swiggy_live
        return swiggy_live.addresses(user_id)

    def search_restaurants(self, user_id, query, offset=0):
        from ..integrations import swiggy_live
        return swiggy_live.search_live_restaurants(user_id, query, offset)

    def menu(self, user_id, restaurant_id, restaurant_name):
        from ..integrations import swiggy_live
        return swiggy_live.live_menu(user_id, restaurant_id, restaurant_name)

    def order_history(self, user_id):
        from ..integrations import swiggy_history
        return swiggy_history.fetch_orders(user_id)

    def fill_cart(self, user_id, restaurant_id, restaurant_name, item_id, item_name, expected_fingerprint,
                  quantity=1):
        if "cart" not in self.capabilities:
            self._unavailable("filling a cart")
        from ..integrations import swiggy_live
        return swiggy_live.fill_live_cart(user_id, restaurant_id, restaurant_name, item_id, item_name,
                                         expected_fingerprint, quantity)

    def place_order(self, user_id, expected_fingerprint):
        if "order" not in self.capabilities:
            self._unavailable("placing an order")
        from ..integrations import swiggy_live
        return swiggy_live.place_live_order(user_id, expected_fingerprint)
