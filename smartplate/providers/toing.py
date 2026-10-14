"""Toing (Swiggy's low-cost food app). Stub: no public API and not one of the Builders Club
MCP servers (Food, Instamart, Dineout, Scenes), so prices can only be ones the user typed."""
from __future__ import annotations

from .base import Provider


class Toing(Provider):
    key = "toing"
    label = "Toing"
    note = ("No public API or MCP server. A Toing price shown in Ziggy must be one you typed in; "
            "Ziggy never fetches or scrapes it.")
