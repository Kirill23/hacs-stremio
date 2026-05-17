"""Tests for PlaybackManager."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.stremio.playback_manager import PlaybackManager


@pytest.fixture
def mock_hass() -> HomeAssistant:
    """Minimal HomeAssistant mock for play_media tests."""
    hass = MagicMock(spec=HomeAssistant)
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock(return_value=None)
    hass.states = MagicMock()
    return hass


def _entity_state(state: str = "idle"):
    s = MagicMock()
    s.state = state
    return s


async def test_play_calls_media_player_play_media(mock_hass) -> None:
    mock_hass.states.get.return_value = _entity_state("idle")
    mgr = PlaybackManager(mock_hass)

    await mgr.play(
        entity_id="media_player.living_room",
        stream_url="https://example.com/movie.mp4",
        media_info={"title": "Inception", "poster": "https://example.com/p.jpg"},
    )

    mock_hass.services.async_call.assert_awaited_once()
    args, kwargs = mock_hass.services.async_call.call_args
    assert args[0] == "media_player"
    assert args[1] == "play_media"
    payload = args[2]
    assert payload["entity_id"] == "media_player.living_room"
    assert payload["media_content_id"] == "https://example.com/movie.mp4"
    assert payload["media_content_type"] == "video"
    # Extra metadata passed via the ``extra`` key
    assert payload["extra"]["title"] == "Inception"
    assert payload["extra"]["thumb"] == "https://example.com/p.jpg"


async def test_play_raises_for_non_media_player_entity(mock_hass) -> None:
    mgr = PlaybackManager(mock_hass)
    with pytest.raises(ServiceValidationError):
        await mgr.play(
            entity_id="light.living_room",
            stream_url="https://example.com/movie.mp4",
            media_info={},
        )
    mock_hass.services.async_call.assert_not_awaited()


async def test_play_raises_when_entity_missing(mock_hass) -> None:
    mock_hass.states.get.return_value = None
    mgr = PlaybackManager(mock_hass)
    with pytest.raises(ServiceValidationError):
        await mgr.play(
            entity_id="media_player.nonexistent",
            stream_url="https://example.com/movie.mp4",
            media_info={},
        )


async def test_play_raises_when_entity_unavailable(mock_hass) -> None:
    mock_hass.states.get.return_value = _entity_state("unavailable")
    mgr = PlaybackManager(mock_hass)
    with pytest.raises(ServiceValidationError):
        await mgr.play(
            entity_id="media_player.unplugged_tv",
            stream_url="https://example.com/movie.mp4",
            media_info={},
        )


async def test_play_dispatches_with_blocking_false(mock_hass) -> None:
    """play() must use blocking=False so it doesn't await device readiness."""
    mock_hass.states.get.return_value = _entity_state("idle")
    mgr = PlaybackManager(mock_hass)
    await mgr.play(
        entity_id="media_player.tv",
        stream_url="https://example.com/x.mp4",
        media_info={},
    )
    _, kwargs = mock_hass.services.async_call.call_args
    assert kwargs.get("blocking") is False


async def test_play_content_type_override(mock_hass) -> None:
    """content_type_override replaces the default 'video' selection.

    Required for Apple TV's VLC handover, which needs
    media_content_type='url' to trigger the Apple TV integration's
    app-launch path instead of the streaming path.
    """
    mock_hass.states.get.return_value = _entity_state("idle")
    mgr = PlaybackManager(mock_hass)
    await mgr.play(
        entity_id="media_player.apple_tv",
        stream_url="vlc://x-callback-url/stream?url=...",
        media_info={"title": "X"},
        content_type_override="url",
    )
    args, _ = mock_hass.services.async_call.call_args
    payload = args[2]
    assert payload["media_content_type"] == "url"


async def test_play_blocking_true_passes_through(mock_hass) -> None:
    """blocking=True must be passed through to async_call.

    Required for callers (e.g. the Apple TV handover) that rely on
    media_player service errors propagating so their except blocks
    can convert them into HandoverError.
    """
    mock_hass.states.get.return_value = _entity_state("idle")
    mgr = PlaybackManager(mock_hass)
    await mgr.play(
        entity_id="media_player.tv",
        stream_url="https://example.com/x.mp4",
        media_info={},
        blocking=True,
    )
    _, kwargs = mock_hass.services.async_call.call_args
    assert kwargs.get("blocking") is True
