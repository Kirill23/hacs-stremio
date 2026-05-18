"""Tests for the deprecated stremio.handover_to_apple_tv compat shim."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.stremio.const import DOMAIN
from custom_components.stremio.services import async_setup_services


def _setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={"email": "x", "password": "y"},
        options={},
        entry_id="test_handover",
    )
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.register_playback_session = MagicMock()
    client = AsyncMock()
    hass.data[DOMAIN] = {entry.entry_id: {"coordinator": coordinator, "client": client}}
    return entry


@pytest.mark.asyncio
async def test_handover_to_apple_tv_dispatches_via_play_stream(hass) -> None:
    """The compat shim plays via the same pipeline as stremio.play_stream."""
    _setup_entry(hass)
    hass.states.async_set("media_player.apple_tv", "idle")
    await async_setup_services(hass)

    with patch("custom_components.stremio.services.PlaybackManager") as mock_pm_class:
        mock_pm = MagicMock()
        mock_pm.play = AsyncMock(return_value=None)
        mock_pm_class.return_value = mock_pm

        await hass.services.async_call(
            DOMAIN,
            "handover_to_apple_tv",
            {
                "stream_url": "https://debrid/x.mp4",
                "entity_id": "media_player.apple_tv",
                "media_id": "tt001",
                "media_type": "movie",
            },
            blocking=True,
        )

        mock_pm.play.assert_awaited_once()


@pytest.mark.asyncio
async def test_handover_to_apple_tv_logs_deprecation_warning_once(
    hass, caplog: pytest.LogCaptureFixture
) -> None:
    """Deprecation warning emits exactly once per HA start, not per call."""
    from custom_components.stremio import services as services_module

    _setup_entry(hass)
    hass.states.async_set("media_player.apple_tv", "idle")
    await async_setup_services(hass)
    # Reset the module-level "warned" flag so the test sees the first warning
    services_module._HANDOVER_DEPRECATION_WARNED = False

    with patch("custom_components.stremio.services.PlaybackManager") as mock_pm_class:
        mock_pm = MagicMock()
        mock_pm.play = AsyncMock(return_value=None)
        mock_pm_class.return_value = mock_pm

        with caplog.at_level(logging.WARNING):
            await hass.services.async_call(
                DOMAIN,
                "handover_to_apple_tv",
                {
                    "stream_url": "https://debrid/x.mp4",
                    "entity_id": "media_player.apple_tv",
                    "media_id": "tt001",
                    "media_type": "movie",
                },
                blocking=True,
            )
            await hass.services.async_call(
                DOMAIN,
                "handover_to_apple_tv",
                {
                    "stream_url": "https://debrid/x.mp4",
                    "entity_id": "media_player.apple_tv",
                    "media_id": "tt001",
                    "media_type": "movie",
                },
                blocking=True,
            )

    deprecation_warnings = [
        r for r in caplog.records if "deprecated" in r.message.lower()
    ]
    assert len(deprecation_warnings) == 1
