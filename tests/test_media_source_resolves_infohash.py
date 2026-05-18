"""media_source.async_resolve_media routes through stream_resolver in v2."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.media_source.error import Unresolvable

from custom_components.stremio.const import CONF_TORRENT_SERVER_URL, DOMAIN
from custom_components.stremio.media_source import StremioMediaSource


def _mock_item(identifier: str) -> MagicMock:
    item = MagicMock()
    item.identifier = identifier
    return item


@pytest.mark.asyncio
async def test_resolves_infohash_via_torrent_server(hass) -> None:
    """A media-source URI pointing at an infoHash-only stream resolves via the configured torrent server."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={"email": "x", "password": "y"},
        options={CONF_TORRENT_SERVER_URL: "http://127.0.0.1:11470"},
    )
    entry.add_to_hass(hass)

    coordinator = MagicMock()
    client = MagicMock()
    client.async_get_streams = AsyncMock(
        return_value=[{"name": "Torrentio P2P", "infoHash": "abc123", "fileIdx": 0}]
    )
    coordinator.client = client
    hass.data[DOMAIN] = {entry.entry_id: {"coordinator": coordinator, "client": client}}

    source = StremioMediaSource(hass)
    result = await source.async_resolve_media(_mock_item("movie/tt001#0"))

    assert result.url == "http://127.0.0.1:11470/abc123/0"


@pytest.mark.asyncio
async def test_resolves_direct_url_unchanged(hass) -> None:
    """A stream with a direct URL is returned as-is."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={"email": "x", "password": "y"},
        options={},
    )
    entry.add_to_hass(hass)

    coordinator = MagicMock()
    client = MagicMock()
    client.async_get_streams = AsyncMock(
        return_value=[{"name": "Real-Debrid", "url": "https://debrid/x.mp4"}]
    )
    coordinator.client = client
    hass.data[DOMAIN] = {entry.entry_id: {"coordinator": coordinator, "client": client}}

    source = StremioMediaSource(hass)
    result = await source.async_resolve_media(_mock_item("movie/tt001#0"))

    assert result.url == "https://debrid/x.mp4"


@pytest.mark.asyncio
async def test_raises_unresolvable_when_no_url_and_no_server(hass) -> None:
    """infoHash-only stream + no torrent server -> Unresolvable with actionable message."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={"email": "x", "password": "y"},
        options={},  # no torrent_server_url
    )
    entry.add_to_hass(hass)

    coordinator = MagicMock()
    client = MagicMock()
    client.async_get_streams = AsyncMock(
        return_value=[{"name": "Torrentio P2P", "infoHash": "abc123"}]
    )
    coordinator.client = client
    hass.data[DOMAIN] = {entry.entry_id: {"coordinator": coordinator, "client": client}}

    source = StremioMediaSource(hass)
    with pytest.raises(Unresolvable) as exc_info:
        await source.async_resolve_media(_mock_item("movie/tt001#0"))

    # User-facing message should point at the remediation paths
    msg = str(exc_info.value).lower()
    assert "stremio server" in msg or "torrent server" in msg or "debrid" in msg
