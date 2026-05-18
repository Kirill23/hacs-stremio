"""Tests for stremio.get_library service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.stremio.const import DOMAIN
from custom_components.stremio.services import async_setup_services


def _make_entry(hass: HomeAssistant, library: list[dict]) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={"email": "x", "password": "y"},
        options={},
        entry_id="test_lib",
    )
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.get_library_items = MagicMock(
        side_effect=lambda media_type, skip, limit: [
            i for i in library if media_type == "all" or i.get("type") == media_type
        ][skip : skip + limit]
    )
    client = AsyncMock()
    hass.data[DOMAIN] = {entry.entry_id: {"coordinator": coordinator, "client": client}}
    return entry


@pytest.mark.asyncio
async def test_get_library_returns_all_items_by_default(hass) -> None:
    library = [
        {"imdb_id": "tt001", "type": "movie", "title": "A"},
        {"imdb_id": "tt002", "type": "series", "title": "B"},
        {"imdb_id": "tt003", "type": "movie", "title": "C"},
    ]
    _make_entry(hass, library)
    await async_setup_services(hass)

    result = await hass.services.async_call(
        DOMAIN,
        "get_library",
        {},
        blocking=True,
        return_response=True,
    )

    assert result["count"] == 3
    assert {i["imdb_id"] for i in result["items"]} == {"tt001", "tt002", "tt003"}


@pytest.mark.asyncio
async def test_get_library_filters_by_type(hass) -> None:
    library = [
        {"imdb_id": "tt001", "type": "movie", "title": "A"},
        {"imdb_id": "tt002", "type": "series", "title": "B"},
        {"imdb_id": "tt003", "type": "movie", "title": "C"},
    ]
    _make_entry(hass, library)
    await async_setup_services(hass)

    result = await hass.services.async_call(
        DOMAIN,
        "get_library",
        {"type": "movie"},
        blocking=True,
        return_response=True,
    )

    assert result["count"] == 2
    assert all(i["type"] == "movie" for i in result["items"])


@pytest.mark.asyncio
async def test_get_library_paginates(hass) -> None:
    library = [
        {"imdb_id": f"tt{i:03d}", "type": "movie", "title": f"M{i}"} for i in range(25)
    ]
    _make_entry(hass, library)
    await async_setup_services(hass)

    page1 = await hass.services.async_call(
        DOMAIN,
        "get_library",
        {"skip": 0, "limit": 10},
        blocking=True,
        return_response=True,
    )
    page2 = await hass.services.async_call(
        DOMAIN,
        "get_library",
        {"skip": 10, "limit": 10},
        blocking=True,
        return_response=True,
    )

    assert page1["count"] == 10
    assert page2["count"] == 10
    assert page1["items"][0]["imdb_id"] != page2["items"][0]["imdb_id"]
