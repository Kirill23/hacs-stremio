"""Playback session registry and Stremio progress sync.

This module tracks active playback sessions (one per HA media_player
entity) that the integration initiated via stremio.play_stream. It
listens for state changes on those entities and writes throttled
progress updates to Stremio's datastore so the Stremio web/mobile apps
see correct continue-watching state.

This first version covers the registry. The state-change listener and
throttled writer are added in the next task.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant

if TYPE_CHECKING:
    from .stremio_client import StremioClient

_LOGGER = logging.getLogger(__name__)


@dataclass
class PlaybackSession:
    """State for one tracked playback session."""

    media_id: str
    media_type: str
    media_content_id: str  # the stream URL we handed to play_media
    started_at: float = field(default_factory=time.monotonic)
    last_synced_at: float = 0.0
    last_position: float = 0.0
    last_duration: float = 0.0


class ProgressSyncManager:
    """Tracks playback sessions and syncs progress to Stremio."""

    def __init__(self, hass: HomeAssistant, client: "StremioClient") -> None:
        self._hass = hass
        self._client = client
        self._sessions: dict[str, PlaybackSession] = {}

    def register_session(
        self,
        entity_id: str,
        media_id: str,
        media_type: str,
        media_content_id: str,
    ) -> None:
        """Start tracking playback on entity_id.

        Replaces any existing session for the same entity_id.
        """
        self._sessions[entity_id] = PlaybackSession(
            media_id=media_id,
            media_type=media_type,
            media_content_id=media_content_id,
        )
        _LOGGER.debug(
            "Registered session: entity=%s media=%s (%s)",
            entity_id,
            media_id,
            media_type,
        )

    def unregister_session(self, entity_id: str) -> None:
        """Stop tracking playback on entity_id. Safe to call twice."""
        if entity_id in self._sessions:
            del self._sessions[entity_id]
            _LOGGER.debug("Unregistered session: entity=%s", entity_id)

    def get_session(self, entity_id: str) -> PlaybackSession | None:
        return self._sessions.get(entity_id)

    def active_entities(self) -> list[str]:
        return list(self._sessions.keys())
