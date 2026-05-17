"""End-to-end orchestration test for the new playback flow.

Exercises the full chain:
    get_streams service -> play_stream service -> coordinator.register_playback_session
    -> ProgressSyncManager state-change listener -> async_update_library_progress write

All external I/O is mocked; real HA hass + services machinery is used.

Plan bug workaround - Option A:
    The plan's test replaced hass.services.async_call after the get_streams call,
    which would prevent the play_stream service handler from running.  Instead we
    patch PlaybackManager.play (so media_player.play_media is never dispatched to a
    real device) while letting the real play_stream handler execute end-to-end.
    This means coordinator.register_playback_session is called for real, wiring the
    ProgressSyncManager session so that the subsequent state change triggers a
    genuine async_update_library_progress call.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.stremio.const import DOMAIN
from custom_components.stremio.coordinator import StremioDataUpdateCoordinator
from custom_components.stremio.services import async_setup_services

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_streams() -> list[dict]:
    return [
        {
            "name": "Real-Debrid 1080p",
            "title": "[CACHED] Inception.2010.1080p",
            "url": "https://debrid.example.com/inception.mp4",
        },
        {
            "name": "Torrentio P2P",
            "title": "Inception.2010.1080p",
            "infoHash": "abc123",
        },
    ]


@pytest.fixture
def mock_stremio_client() -> MagicMock:
    """Minimal StremioClient double used across the whole test."""
    client = MagicMock()
    client.async_get_streams = AsyncMock(return_value=[])
    client.async_update_library_progress = AsyncMock(return_value=None)
    return client


@pytest.fixture
async def e2e_hass(hass, mock_stremio_client):
    """Set up a real hass with the Stremio integration wired manually.

    - A real StremioDataUpdateCoordinator is constructed (with a mocked
      StremioClient so no network calls occur).
    - The ProgressSyncManager inside the coordinator is started so its
      STATE_CHANGED listener is active.
    - All services are registered via async_setup_services.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"email": "test@example.com", "password": "test"},
        options={
            "addon_stream_order": "",
            "stream_quality_preference": "any",
            # No torrent_server_url -> P2P-only streams are not playable
            "progress_sync_enabled": True,
        },
        entry_id="e2e_entry",
    )
    entry.add_to_hass(hass)

    coordinator = StremioDataUpdateCoordinator(
        hass=hass,
        client=mock_stremio_client,
        entry=entry,
    )
    # Start the ProgressSyncManager so state-change events are handled.
    coordinator.start_progress_sync()

    hass.data[DOMAIN] = {
        entry.entry_id: {
            "coordinator": coordinator,
            "client": mock_stremio_client,
        }
    }

    await async_setup_services(hass)
    await hass.async_block_till_done()

    return hass, coordinator, mock_stremio_client


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_playback_flow(e2e_hass, fake_streams):
    """get_streams -> play_stream -> register session -> state change -> progress write."""
    hass, coordinator, mock_client = e2e_hass

    # ------------------------------------------------------------------
    # Phase 1: get_streams returns annotated results (playable flag).
    # ------------------------------------------------------------------
    mock_client.async_get_streams = AsyncMock(return_value=fake_streams)

    streams_result = await hass.services.async_call(
        DOMAIN,
        "get_streams",
        {"media_id": "tt1375666", "media_type": "movie"},
        blocking=True,
        return_response=True,
    )

    streams = streams_result["streams"]

    # Direct-URL stream is playable; P2P-only stream is not (no torrent server).
    assert streams[0]["playable"] is True, "Expected cached/URL stream to be playable"
    assert streams[1]["playable"] is False, "Expected P2P stream to be not playable"

    direct_url = streams[0]["url"]

    # ------------------------------------------------------------------
    # Phase 2: play_stream runs the real handler end-to-end.
    # We patch PlaybackManager.play to avoid dispatching to a real device
    # while still letting the service handler register the session.
    # ------------------------------------------------------------------
    hass.states.async_set("media_player.tv", "idle")

    with patch("custom_components.stremio.services.PlaybackManager") as mock_pm_class:
        mock_pm = MagicMock()
        mock_pm.play = AsyncMock(return_value=None)
        mock_pm_class.return_value = mock_pm

        await hass.services.async_call(
            DOMAIN,
            "play_stream",
            {
                "stream_url": direct_url,
                "entity_id": "media_player.tv",
                "media_id": "tt1375666",
                "media_type": "movie",
            },
            blocking=True,
        )

    # PlaybackManager.play was invoked with the correct entity.
    mock_pm.play.assert_called_once()
    call_kwargs = mock_pm.play.call_args
    assert call_kwargs.kwargs.get("entity_id") == "media_player.tv"
    assert call_kwargs.kwargs.get("stream_url") == direct_url

    # ------------------------------------------------------------------
    # Phase 3: coordinator registered the session via ProgressSyncManager.
    # ------------------------------------------------------------------
    session = coordinator.progress_sync.get_session("media_player.tv")
    assert session is not None, "Expected a playback session to be registered"
    assert session.media_id == "tt1375666"
    assert session.media_content_id == direct_url

    # ------------------------------------------------------------------
    # Phase 4: simulate Chromecast going paused -> immediate progress write.
    # ------------------------------------------------------------------
    mock_client.async_update_library_progress.reset_mock()

    hass.states.async_set(
        "media_player.tv",
        "paused",
        {
            "media_content_id": direct_url,
            "media_position": 600.0,
            "media_duration": 8400.0,
        },
    )
    # Allow the STATE_CHANGED event to be processed by the listener.
    await hass.async_block_till_done()

    # ProgressSyncManager must have written progress at least once.
    assert (
        mock_client.async_update_library_progress.await_count >= 1
    ), "Expected async_update_library_progress to be called after paused state"

    last_call = mock_client.async_update_library_progress.await_args
    assert last_call.kwargs["media_id"] == "tt1375666"
    assert last_call.kwargs["position_seconds"] == 600.0
    assert last_call.kwargs["duration_seconds"] == 8400.0
