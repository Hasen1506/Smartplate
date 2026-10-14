"""The provider interface every food platform adapter implements."""
from __future__ import annotations


class ProviderUnavailable(Exception):
    """This platform is not wired into Ziggy (terms, no public API, or not built yet)."""


class Provider:
    """One food platform. Capabilities name what the adapter really does; a method whose
    capability is missing raises ProviderUnavailable with the plain reason."""

    key = "base"
    label = "Provider"
    wired = False
    # read: addresses, search, menus · history: past orders · cart: fill the user's cart ·
    # order: place an order the user explicitly approved · manual: user-typed entries only
    capabilities: frozenset[str] = frozenset()
    attribution: str | None = None          # text the brand rules require next to its data
    note = ""                               # why it is (not) wired; terms in one line

    def describe(self) -> dict:
        return {"key": self.key, "label": self.label, "wired": self.wired,
                "capabilities": sorted(self.capabilities), "attribution": self.attribution, "note": self.note}

    def _unavailable(self, what: str):
        raise ProviderUnavailable(f"{self.label}: {what} isn't available in Ziggy. {self.note}".strip())

    # ---- the seam ------------------------------------------------------------------- #
    def addresses(self, user_id: int) -> list[dict]:
        self._unavailable("saved addresses")

    def search_restaurants(self, user_id: int, query: str, offset: int = 0) -> dict:
        self._unavailable("restaurant search")

    def menu(self, user_id: int, restaurant_id: str, restaurant_name: str) -> dict:
        self._unavailable("menus")

    def order_history(self, user_id: int) -> list[dict]:
        self._unavailable("order history")

    def fill_cart(self, user_id: int, restaurant_id: str, restaurant_name: str, item_id: str, item_name: str,
                  expected_fingerprint: str | None, quantity: int = 1) -> dict:
        self._unavailable("filling a cart")

    def place_order(self, user_id: int, expected_fingerprint: str | None) -> dict:
        self._unavailable("placing an order")
