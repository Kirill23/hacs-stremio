"""Tests for ProgressSyncManager."""

from __future__ import annotations

import time
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


def _make_state(
    state: str,
    media_content_id: str | None = None,
    media_position: float | None = None,
    media_duration: float | None = None,
) -> MagicMock:
    s = MagicMock()
    s.state = state
    s.attributes = {}
    if media_content_id is not None:
        s.attributes["media_content_id"] = media_content_id
    if media_position is not None:
        s.attributes["media_position"] = media_position
    if media_duration is not None:
        s.attributes["media_duration"] = media_duration
    return s


def _state_event(entity_id: str, new_state) -> MagicMock:
    e = MagicMock()
    e.data = {"entity_id": entity_id, "new_state": new_state}
    return e


async def test_state_change_to_playing_does_not_immediately_write(
    mock_hass, mock_client
) -> None:
    """First playing state shouldn't trigger an immediate datastore write."""
    mgr = ProgressSyncManager(mock_hass, mock_client)
    mgr.register_session(
        entity_id="media_player.tv",
        media_id="tt001",
        media_type="movie",
        media_content_id="https://a/x.mp4",
    )
    state = _make_state(
        "playing",
        media_content_id="https://a/x.mp4",
        media_position=15.0,
        media_duration=7200.0,
    )
    await mgr._handle_state_change(_state_event("media_player.tv", state))

    # Session updated but no write (within throttle interval)
    s = mgr.get_session("media_player.tv")
    assert s.last_position == 15.0
    assert s.last_duration == 7200.0
    mock_client.async_update_library_progress.assert_not_awaited()


async def test_state_change_after_throttle_interval_writes(
    mock_hass, mock_client
) -> None:
    from custom_components.stremio.const import (
        PROGRESS_SYNC_INTERVAL_SECONDS,
    )

    mgr = ProgressSyncManager(mock_hass, mock_client)
    mgr.register_session(
        entity_id="media_player.tv",
        media_id="tt001",
        media_type="movie",
        media_content_id="https://a/x.mp4",
    )
    # Pretend last sync happened > interval ago
    mgr._sessions["media_player.tv"].last_synced_at = (
        time.monotonic() - PROGRESS_SYNC_INTERVAL_SECONDS - 1
    )

    state = _make_state(
        "playing",
        media_content_id="https://a/x.mp4",
        media_position=300.0,
        media_duration=7200.0,
    )
    await mgr._handle_state_change(_state_event("media_player.tv", state))

    mock_client.async_update_library_progress.assert_awaited_once_with(
        media_id="tt001",
        media_type="movie",
        position_seconds=300.0,
        duration_seconds=7200.0,
    )


async def test_paused_triggers_immediate_write(mock_hass, mock_client) -> None:
    mgr = ProgressSyncManager(mock_hass, mock_client)
    mgr.register_session(
        entity_id="media_player.tv",
        media_id="tt001",
        media_type="movie",
        media_content_id="https://a/x.mp4",
    )
    # Within throttle window
    mgr._sessions["media_player.tv"].last_synced_at = time.monotonic()

    state = _make_state(
        "paused",
        media_content_id="https://a/x.mp4",
        media_position=42.0,
        media_duration=7200.0,
    )
    await mgr._handle_state_change(_state_event("media_player.tv", state))

    mock_client.async_update_library_progress.assert_awaited_once()


async def test_mismatched_content_id_unregisters_session(
    mock_hass, mock_client
) -> None:
    mgr = ProgressSyncManager(mock_hass, mock_client)
    mgr.register_session(
        entity_id="media_player.tv",
        media_id="tt001",
        media_type="movie",
        media_content_id="https://a/x.mp4",
    )

    # User cast something else to the same Chromecast
    state = _make_state(
        "playing",
        media_content_id="https://youtube.com/some-video",
        media_position=10.0,
        media_duration=600.0,
    )
    await mgr._handle_state_change(_state_event("media_player.tv", state))

    assert mgr.get_session("media_player.tv") is None
    mock_client.async_update_library_progress.assert_not_awaited()


async def test_state_change_for_unregistered_entity_is_ignored(
    mock_hass, mock_client
) -> None:
    mgr = ProgressSyncManager(mock_hass, mock_client)
    state = _make_state("playing", media_content_id="https://x/y.mp4")
    await mgr._handle_state_change(_state_event("media_player.not_tracked", state))
    mock_client.async_update_library_progress.assert_not_awaited()


async def test_idle_with_high_position_writes_watched(mock_hass, mock_client) -> None:
    """Going to idle past WATCHED_THRESHOLD triggers a final write and unregister."""
    mgr = ProgressSyncManager(mock_hass, mock_client)
    mgr.register_session(
        entity_id="media_player.tv",
        media_id="tt001",
        media_type="movie",
        media_content_id="https://a/x.mp4",
    )
    # Simulate that we last saw position near the end
    mgr._sessions["media_player.tv"].last_position = 7000.0
    mgr._sessions["media_player.tv"].last_duration = 7200.0

    state = _make_state("idle")  # no media_content_id (Chromecast cleared it)
    await mgr._handle_state_change(_state_event("media_player.tv", state))

    mock_client.async_update_library_progress.assert_awaited_once_with(
        media_id="tt001",
        media_type="movie",
        position_seconds=7000.0,
        duration_seconds=7200.0,
    )
    # Session is cleaned up after the final write
    assert mgr.get_session("media_player.tv") is None


async def test_sync_write_failure_keeps_session_alive(mock_hass, mock_client) -> None:
    from custom_components.stremio.stremio_client import StremioConnectionError
    from custom_components.stremio.const import (
        PROGRESS_SYNC_INTERVAL_SECONDS,
    )

    mock_client.async_update_library_progress.side_effect = StremioConnectionError(
        "API down"
    )

    mgr = ProgressSyncManager(mock_hass, mock_client)
    mgr.register_session(
        entity_id="media_player.tv",
        media_id="tt001",
        media_type="movie",
        media_content_id="https://a/x.mp4",
    )
    mgr._sessions["media_player.tv"].last_synced_at = (
        time.monotonic() - PROGRESS_SYNC_INTERVAL_SECONDS - 1
    )

    state = _make_state(
        "playing",
        media_content_id="https://a/x.mp4",
        media_position=300.0,
        media_duration=7200.0,
    )
    # Should not raise
    await mgr._handle_state_change(_state_event("media_player.tv", state))

    # Session still registered
    assert mgr.get_session("media_player.tv") is not None
