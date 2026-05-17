"""Stream URL resolution for Stremio addons.

Translates stream entries from Stremio addons (which may have direct URLs,
torrent infoHashes, or both) into HTTP/HTTPS URLs that HA media_player
entities can play.

The two paths:
- Direct URL (debrid-cached or HTTP source) -> returned as-is.
- infoHash + configured torrent server -> URL constructed against the
  server using the same path format the official stremio-server uses:
  http://<server>/<infoHash>/<fileIdx>

Neither path available -> StreamUnplayableError. Callers surface this as
a user-facing error pointing at the two remediation paths (debrid in
Torrentio config; or install the companion add-on).
"""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


class StreamUnplayableError(Exception):
    """Raised when a stream cannot be converted to a playable URL.

    Means the stream has neither a direct HTTP(S) URL nor a usable
    infoHash + configured torrent server combination.
    """


def resolve_stream_url(
    stream: dict[str, Any],
    torrent_server_url: str | None = None,
) -> str:
    """Return a playable HTTP(S) URL for a Stremio stream entry.

    Args:
        stream: A stream dict from StremioClient.async_get_streams. Keys of
            interest: ``url`` (optional), ``infoHash`` (optional), ``fileIdx``
            (optional; defaults to 0).
        torrent_server_url: Base URL of a stremio-server instance, e.g.
            "http://homeassistant.local:11470". May be None or empty.

    Returns:
        An HTTP or HTTPS URL ready to hand to media_player.play_media.

    Raises:
        StreamUnplayableError: If neither a direct HTTP(S) URL nor an
            infoHash + torrent server combination is available.
    """
    direct_url = stream.get("url")
    if direct_url and isinstance(direct_url, str) and direct_url.startswith(
        ("http://", "https://")
    ):
        return direct_url

    info_hash = stream.get("infoHash")
    if info_hash and torrent_server_url:
        file_idx = stream.get("fileIdx", 0)
        base = torrent_server_url.rstrip("/")
        return f"{base}/{info_hash}/{file_idx}"

    raise StreamUnplayableError(
        f"Stream cannot be resolved to a playable URL: "
        f"has_direct_url={bool(direct_url)}, "
        f"has_infoHash={bool(info_hash)}, "
        f"torrent_server_configured={bool(torrent_server_url)}"
    )


def is_stream_playable(
    stream: dict[str, Any],
    torrent_server_url: str | None = None,
) -> bool:
    """Return True if the stream can be resolved to a playable URL.

    Used by ``stremio.get_streams`` to annotate each stream with a
    ``playable`` flag so callers can grey out unplayable rows in the picker.
    """
    try:
        resolve_stream_url(stream, torrent_server_url)
        return True
    except StreamUnplayableError:
        return False
