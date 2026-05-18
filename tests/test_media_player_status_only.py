"""media_player.stremio is status-only in v2 — no play_media support."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.exceptions import ServiceValidationError


@pytest.mark.asyncio
async def test_play_media_raises_service_validation_error() -> None:
    """media_player.stremio rejects play_media with a translated error."""
    from custom_components.stremio.media_player import StremioMediaPlayer

    coordinator = MagicMock()
    coordinator.data = {}
    entry = MagicMock()
    entry.options = {}
    entry.entry_id = "abc"

    player = StremioMediaPlayer(coordinator, entry)

    with pytest.raises(ServiceValidationError) as exc_info:
        await player.async_play_media(
            media_type="video",
            media_id="media-source://stremio/movie/tt001#0",
        )

    err = exc_info.value
    assert err.translation_key == "stremio_entity_not_a_player"
    assert err.translation_domain == "stremio"


@pytest.mark.asyncio
async def test_no_async_browse_media_method() -> None:
    """async_browse_media is removed in v2; HA will fall back to media_source."""
    from custom_components.stremio.media_player import StremioMediaPlayer

    coordinator = MagicMock()
    coordinator.data = {}
    entry = MagicMock()
    entry.options = {}

    player = StremioMediaPlayer(coordinator, entry)
    # Either the method does not exist, or it raises NotImplementedError.
    # Both are acceptable signals to HA that browsing happens via
    # media_source, not via this entity.
    assert not hasattr(player, "async_browse_media") or (
        callable(getattr(player, "async_browse_media"))
        and (
            await _raises_not_implemented(
                lambda: player.async_browse_media(media_content_id=None)
            )
        )
    )


async def _raises_not_implemented(coro_factory) -> bool:
    try:
        await coro_factory()
    except NotImplementedError:
        return True
    except Exception:
        return False
    return False
