"""Food-platform adapters behind one seam (grill #46, PLAN.md W7).

Ziggy must survive with zero platform API: a typed-in or forwarded-receipt ledger always
works, and each platform is an accelerator plugged in here. Today only Swiggy Food is
implemented (Swiggy Builders Club MCP). Toing, Zomato and Instamart are stubs that say
plainly why they are not wired, with the terms behind that (docs/research/platform-2026-10.md).

Nothing here talks to a platform by itself; `swiggy_food` delegates to the existing
integrations/swiggy_live.py functions so behaviour is unchanged.
"""
from __future__ import annotations

from .base import Provider, ProviderUnavailable
from .instamart import Instamart
from .manual import Manual
from .swiggy_food import SwiggyFood
from .toing import Toing
from .zomato import Zomato

REGISTRY: dict[str, Provider] = {p.key: p for p in (SwiggyFood(), Manual(), Instamart(), Toing(), Zomato())}


def get(key: str) -> Provider:
    try:
        return REGISTRY[key]
    except KeyError:
        raise ProviderUnavailable(f"Unknown provider {key!r}") from None


def overview() -> list[dict]:
    """Every provider, whether it is wired, what it can do, and the note behind that."""
    return [p.describe() for p in REGISTRY.values()]


__all__ = ["Provider", "ProviderUnavailable", "REGISTRY", "get", "overview"]
