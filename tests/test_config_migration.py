"""Tests for config entry auto-migration from v1 (Apple-TV-era) to v2."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.stremio import async_migrate_entry
from custom_components.stremio.const import DOMAIN


@pytest.mark.asyncio
async def test_migrate_strips_apple_tv_options(hass) -> None:
    """v1 config entry with Apple-TV options gets cleaned to v2 shape."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={
            "email": "user@example.com",
            "password": "hash",
            "enable_apple_tv_handover": True,
            "apple_tv_entity_id": "media_player.apple_tv",
            "apple_tv_credentials": "...",
            "apple_tv_identifier": "AABBCC",
            "handover_method": "airplay",
            "apple_tv_device": "Living Room",
        },
        options={
            "player_scan_interval": 30,
            "torrent_server_url": "http://localhost:11470",
            "enable_apple_tv_handover": True,
            "handover_method": "airplay",
        },
    )
    entry.add_to_hass(hass)

    result = await async_migrate_entry(hass, entry)

    assert result is True
    assert entry.version == 2
    # Apple-TV keys are gone
    for key in [
        "enable_apple_tv_handover",
        "apple_tv_entity_id",
        "apple_tv_credentials",
        "apple_tv_identifier",
        "handover_method",
        "apple_tv_device",
    ]:
        assert key not in entry.data
        assert key not in entry.options
    # Non-Apple-TV settings preserved
    assert entry.data["email"] == "user@example.com"
    assert entry.options["player_scan_interval"] == 30
    assert entry.options["torrent_server_url"] == "http://localhost:11470"


@pytest.mark.asyncio
async def test_migrate_idempotent_on_v2_entry(hass) -> None:
    """Calling migrate on an already-v2 entry is a no-op."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={"email": "user@example.com", "password": "hash"},
        options={"torrent_server_url": "http://localhost:11470"},
    )
    entry.add_to_hass(hass)

    result = await async_migrate_entry(hass, entry)

    assert result is True
    assert entry.version == 2
    assert entry.options["torrent_server_url"] == "http://localhost:11470"


@pytest.mark.asyncio
async def test_migrate_handles_entry_with_no_apple_tv_keys(hass) -> None:
    """Fresh v1 entry without Apple-TV opts still bumps version cleanly."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={"email": "user@example.com", "password": "hash"},
        options={"player_scan_interval": 30},
    )
    entry.add_to_hass(hass)

    result = await async_migrate_entry(hass, entry)

    assert result is True
    assert entry.version == 2
