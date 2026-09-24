"""Read-only Swiggy Food discovery. No cart or order tool is exposed here.

The wire contract is verified at connection time against tools/list. The
normalizers intentionally keep absent provider fields absent; a missing ID or
availability status must never become a made-up restaurant or safety claim.
"""
from __future__ import annotations

import json
import math
from typing import Any


FOOD_URL = "https://mcp.swiggy.com/food"
READ_TOOLS = frozenset({"get_addresses", "search_restaurants", "get_restaurant_menu", "search_menu"})


class DiscoveryError(RuntimeError):
    pass


def _number(value, maximum: float) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and 0 <= number <= maximum else None


def _strings(value) -> list[str]:
    return [part for part in value if isinstance(part, str)] if isinstance(value, list) else []


class SwiggyFoodClient:
    def __init__(self, access_token: str):
        if not access_token:
            raise ValueError("A Swiggy access token is required")
        self.access_token = access_token

    async def call(self, name: str, arguments: dict[str, Any]) -> dict:
        if name not in READ_TOOLS:
            raise ValueError("Unsupported discovery operation")
        # Imported only when live discovery is used, so the existing simulator
        # and its tests do not depend on a live connection or credentials.
        import httpx2
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client

        try:
            async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {self.access_token}"},
                                          timeout=15) as http:
                async with Client(streamable_http_client(FOOD_URL, http_client=http)) as client:
                    listed = await client.list_tools()
                    if name not in {tool.name for tool in listed.tools}:
                        raise DiscoveryError(f"Swiggy discovery tool {name} is unavailable")
                    result = await client.call_tool(name, arguments)
                    if result.is_error:
                        raise DiscoveryError(f"Swiggy {name} failed")
                    payload = result.structured_content
                    if not isinstance(payload, dict):
                        for block in result.content:
                            if getattr(block, "type", None) == "text":
                                try:
                                    payload = json.loads(block.text)
                                except ValueError:
                                    continue
                                break
                    if not isinstance(payload, dict) or payload.get("success") is False:
                        raise DiscoveryError(f"Swiggy {name} returned an invalid response")
                    return payload.get("data", payload)
        except DiscoveryError:
            raise
        except Exception as exc:
            raise DiscoveryError("Swiggy is unavailable or your connection expired; reconnect and retry") from exc


def addresses(data: dict) -> dict:
    rows = data.get("addresses")
    if not isinstance(rows, list):
        raise DiscoveryError("Swiggy address response has no address list")
    pagination = data.get("pagination")
    return {
        "addresses": [{"id": row["id"], "label": next((v for v in
                       (row.get("addressCategory"), row.get("addressTag"))
                       if isinstance(v, str) and v), "Address"),
                       "address": row.get("addressLine") if isinstance(row.get("addressLine"), str) else None}
                      for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)],
        "pagination": pagination if isinstance(pagination, dict) else {},
    }


def restaurants(data: dict) -> dict:
    rows = data.get("restaurants")
    if not isinstance(rows, list):
        raise DiscoveryError("Swiggy restaurant response has no restaurant list")
    found = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        if row.get("availabilityStatus") != "OPEN":
            continue
        found.append({
            "id": row["id"], "name": row.get("name") if isinstance(row.get("name"), str) else None,
            "cuisines": _strings(row.get("cuisines")),
            "rating": _number(row.get("avgRating"), 5),
            "area": row.get("areaName") if isinstance(row.get("areaName"), str) else None,
            "distance_km": _number(row.get("distanceKm"), 1000),
            "eta_min": _number(row.get("deliveryTimeMinutes"), 1440),
            "availability": row["availabilityStatus"],
        })
    return {"restaurants": found, "next_offset": data.get("nextOffset"), "has_more": bool(data.get("hasMore"))}


def menu(data: dict, restaurant_id: str) -> dict:
    restaurant = data.get("restaurant") or {}
    if restaurant.get("id") != restaurant_id:
        raise DiscoveryError("Swiggy menu restaurant does not match the selection")
    if restaurant.get("isOpen") is False:
        raise DiscoveryError("That Swiggy restaurant is closed")
    rows = data.get("items")
    if not isinstance(rows, list):
        raise DiscoveryError("Swiggy menu response has no items")
    items = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        if row.get("inStock") in (0, False):
            continue
        veg = row.get("isVeg")
        veg = veg if isinstance(veg, bool) else (bool(veg) if veg in (0, 1) else None)
        items.append({
            "id": row["id"], "restaurant_id": restaurant_id,
            "name": row.get("name") if isinstance(row.get("name"), str) else None,
            "price": _number(row.get("price"), 100000), "veg": veg,
            "rating": _number(row.get("rating"), 5), "categories": _strings(row.get("categories")),
            "nutrition": None, "allergens": None,
        })
    return {"restaurant_id": restaurant_id, "items": items, "truncated": bool(data.get("truncated"))}
