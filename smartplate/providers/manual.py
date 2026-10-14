"""What the user types in: favourite places/dishes and prices from any app, and a delivery
address, with no platform account at all (grill #25, #46). Always available."""
from __future__ import annotations

from .base import Provider


class Manual(Provider):
    key = "manual"
    label = "Typed in by you"
    wired = True
    capabilities = frozenset({"manual"})
    note = "Works with no platform connected. Ordering is a hand-off: Ziggy opens the app's own search."
