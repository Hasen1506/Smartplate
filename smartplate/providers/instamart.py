"""Swiggy Instamart (groceries for "Cook it"): same Builders Club programme and sign-in as
Swiggy Food, so it is the natural next adapter. Stub only until it is built and approved."""
from __future__ import annotations

from .base import Provider


class Instamart(Provider):
    key = "swiggy_instamart"
    label = "Swiggy Instamart"
    attribution = "Powered by Swiggy"
    note = ("Not built yet. Part of the same Swiggy Builders Club MCP (separate server, same OAuth); "
            "needs Swiggy to enable it for Ziggy.")
