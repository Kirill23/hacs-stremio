"""Tests for ProgressSyncManager."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.stremio.progress_sync import (
    PlaybackSession,
    ProgressSyncManager,
)


@pytest.fixture
def mock_hass() -> HomeAssistant:
    hass = MagicMock(spec=HomeAssistant)
    hass.bus = MagicMock()
    hass.bus.async_listen = MagicMock(return_value=lambda: None)
    return hass


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.async_update_library_progress = AsyncMock(return_value=None)
    return client


def test_register_session_records_entity_and_media(mock_hass, mock_client) -> None:
    mgr = ProgressSyncManager(mock_hass, mock_client)
    mgr.register_session(
        entity_id="media_player.living_room",
        media_id="tt1375666",
        media_type="movie",
        media_content_id="https://example.com/movie.mp4",
    )
    session = mgr.get_session("media_player.living_room")
    assert isinstance(session, PlaybackSession)
    assert session.media_id == "tt1375666"
    assert session.media_type == "movie"
    assert session.media_content_id == "https://example.com/movie.mp4"


def test_unregister_session_removes_entry(mock_hass, mock_client) -> None:
    mgr = ProgressSyncManager(mock_hass, mock_client)
    mgr.register_session(
        entity_id="media_player.living_room",
        media_id="tt1375666",
        media_type="movie",
        media_content_id="https://example.com/movie.mp4",
    )
    mgr.unregister_session("media_player.living_room")
    assert mgr.get_session("media_player.living_room") is None


def test_register_replaces_existing_session(mock_hass, mock_client) -> None:
    mgr = ProgressSyncManager(mock_hass, mock_client)
    mgr.register_session(
        entity_id="media_player.tv",
        media_id="tt001",
        media_type="movie",
        media_content_id="https://a/x.mp4",
    )
    mgr.register_session(
        entity_id="media_player.tv",
        media_id="tt002",
        media_type="movie",
        media_content_id="https://b/y.mp4",
    )
    s = mgr.get_session("media_player.tv")
    assert s.media_id == "tt002"


def test_active_entities_returns_currently_registered(mock_hass, mock_client) -> None:
    mgr = ProgressSyncManager(mock_hass, mock_client)
    mgr.register_session(
        entity_id="media_player.a",
        media_id="tt1",
        media_type="movie",
        media_content_id="https://a/1.mp4",
    )
    mgr.register_session(
        entity_id="media_player.b",
        media_id="tt2",
        media_type="movie",
        media_content_id="https://b/2.mp4",
    )
    assert set(mgr.active_entities()) == {"media_player.a", "media_player.b"}
