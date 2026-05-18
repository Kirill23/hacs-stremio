"""Tests for ProgressSyncManager pending-session correlation (Option X)."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.stremio.progress_sync import (
    PendingSession,
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


def _make_state(state, media_content_id=None):
    s = MagicMock()
    s.state = state
    s.attributes = {}
    if media_content_id is not None:
        s.attributes["media_content_id"] = media_content_id
    return s


def _state_event(entity_id, new_state):
    e = MagicMock()
    e.data = {"entity_id": entity_id, "new_state": new_state}
    return e


def test_register_pending_session_records_url(mock_hass, mock_client) -> None:
    mgr = ProgressSyncManager(mock_hass, mock_client)
    mgr.register_pending_session(
        media_id="tt001",
        media_type="movie",
        media_content_id="https://debrid/x.mp4",
    )
    pending = mgr.get_pending_session("https://debrid/x.mp4")
    assert isinstance(pending, PendingSession)
    assert pending.media_id == "tt001"


async def test_state_change_graduates_pending_to_active_session(
    mock_hass, mock_client
) -> None:
    """When a media_player enters playing with a pending URL, register a real session."""
    mgr = ProgressSyncManager(mock_hass, mock_client)
    mgr.register_pending_session(
        media_id="tt001",
        media_type="movie",
        media_content_id="https://debrid/x.mp4",
    )

    state = _make_state("playing", media_content_id="https://debrid/x.mp4")
    await mgr._handle_state_change(_state_event("media_player.tv", state))

    session = mgr.get_session("media_player.tv")
    assert isinstance(session, PlaybackSession)
    assert session.media_id == "tt001"
    assert session.media_content_id == "https://debrid/x.mp4"
    # And the pending entry is consumed
    assert mgr.get_pending_session("https://debrid/x.mp4") is None


async def test_state_change_with_unmatched_url_does_not_graduate(
    mock_hass, mock_client
) -> None:
    """Random state changes don't accidentally register sessions."""
    mgr = ProgressSyncManager(mock_hass, mock_client)
    state = _make_state("playing", media_content_id="https://youtube.com/x")
    await mgr._handle_state_change(_state_event("media_player.tv", state))

    assert mgr.get_session("media_player.tv") is None


def test_pending_session_gc_removes_expired_entries(mock_hass, mock_client) -> None:
    from custom_components.stremio.const import PENDING_SESSION_TTL_SECONDS

    mgr = ProgressSyncManager(mock_hass, mock_client)
    mgr.register_pending_session(
        media_id="tt001",
        media_type="movie",
        media_content_id="https://debrid/x.mp4",
    )
    # Force the pending session to look ancient.
    mgr._pending["https://debrid/x.mp4"].created_at = (
        time.monotonic() - PENDING_SESSION_TTL_SECONDS - 1
    )
    mgr._gc_pending()
    assert mgr.get_pending_session("https://debrid/x.mp4") is None


def test_pending_session_gc_keeps_fresh_entries(mock_hass, mock_client) -> None:
    mgr = ProgressSyncManager(mock_hass, mock_client)
    mgr.register_pending_session(
        media_id="tt001",
        media_type="movie",
        media_content_id="https://debrid/x.mp4",
    )
    mgr._gc_pending()
    assert mgr.get_pending_session("https://debrid/x.mp4") is not None
