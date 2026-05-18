"""Tests for Stremio button platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, EntityCategory
from homeassistant.core import HomeAssistant

from custom_components.stremio.button import (
    BUTTON_TYPES,
    StremioButton,
    async_setup_entry,
)
from custom_components.stremio.const import DOMAIN

from .conftest import MOCK_LIBRARY_ITEMS, MOCK_STREAMS


@pytest.fixture
def mock_button_coordinator(mock_coordinator):
    """Create coordinator with button-specific data."""
    mock_coordinator.data = {
        "library": MOCK_LIBRARY_ITEMS,
        "current_watching": {
            "title": "The Shawshank Redemption",
            "type": "movie",
            "imdb_id": "tt0111161",
            "season": None,
            "episode": None,
        },
    }
    mock_coordinator.async_request_refresh = AsyncMock()
    return mock_coordinator


class TestStremioButton:
    """Tests for the Stremio button entity."""

    @pytest.mark.asyncio
    async def test_button_press(
        self, hass: HomeAssistant, mock_button_coordinator, mock_config_entry
    ):
        """Test button press calls coordinator refresh."""
        button = StremioButton(
            mock_button_coordinator, mock_config_entry, BUTTON_TYPES[0]
        )
        button.hass = hass

        await button.async_press()

        # Should trigger coordinator refresh
        mock_button_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_button_unique_id(
        self, hass: HomeAssistant, mock_button_coordinator, mock_config_entry
    ):
        """Test button unique ID generation."""
        button = StremioButton(
            mock_button_coordinator, mock_config_entry, BUTTON_TYPES[0]
        )
        button.hass = hass

        expected_id = f"{mock_config_entry.entry_id}_{BUTTON_TYPES[0].key}"
        assert button.unique_id == expected_id

    @pytest.mark.asyncio
    async def test_button_device_info(
        self, hass: HomeAssistant, mock_button_coordinator, mock_config_entry
    ):
        """Test button has correct device info."""
        button = StremioButton(
            mock_button_coordinator, mock_config_entry, BUTTON_TYPES[0]
        )
        button.hass = hass

        assert button.device_info is not None
        assert "identifiers" in button.device_info

    @pytest.mark.asyncio
    async def test_button_entity_category(
        self, hass: HomeAssistant, mock_button_coordinator, mock_config_entry
    ):
        """Test button has diagnostic entity category."""
        button = StremioButton(
            mock_button_coordinator, mock_config_entry, BUTTON_TYPES[0]
        )
        button.hass = hass

        # All button types should be diagnostic
        assert button.entity_description.entity_category == EntityCategory.DIAGNOSTIC


class TestButtonPlatformSetup:
    """Tests for button platform setup."""

    @pytest.mark.asyncio
    async def test_async_setup_entry_without_apple_tv(
        self, hass: HomeAssistant, mock_config_entry, mock_coordinator
    ):
        """Test button platform setup without Apple TV handover."""
        hass.data[DOMAIN] = {
            mock_config_entry.entry_id: {"coordinator": mock_coordinator}
        }

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Should add only standard buttons (not Apple TV button)
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == len(BUTTON_TYPES)
        assert all(isinstance(e, StremioButton) for e in entities)
