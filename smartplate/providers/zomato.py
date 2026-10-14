"""Zomato. Stub: Zomato's official MCP exists but its README says third-party apps may not
be built on it (personal use only), so Ziggy does not connect to it."""
from __future__ import annotations

from .base import Provider


class Zomato(Provider):
    key = "zomato"
    label = "Zomato"
    note = ("Zomato's MCP is for personal use only; third-party apps aren't allowed on it. "
            "Add Zomato orders and prices by typing them in.")
