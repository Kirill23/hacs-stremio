"""Playback session registry and Stremio progress sync.

Tracks active playback sessions (one per HA media_player entity) that the
integration initiated via stremio.play_stream. Subscribes to HA state
changes on those entities; throttle-writes progress back to Stremio so
the web/mobile apps see continue-watching state.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant

from .const import PROGRESS_SYNC_INTERVAL_SECONDS

if TYPE_CHECKING:
    from .stremio_client import StremioClient

_LOGGER = logging.getLogger(__name__)

_PLAYING_STATES = {"playing"}
_PAUSED_STATES = {"paused"}
_TERMINAL_STATES = {"idle", "off", "standby", "unavailable", "unknown"}


@dataclass
class PlaybackSession:
    """State for one tracked playback session."""

    media_id: str
    media_type: str
    media_content_id: str
    started_at: float = field(default_factory=time.monotonic)
    last_synced_at: float = field(default_factory=time.monotonic)
    last_position: float = 0.0
    last_duration: float = 0.0


class ProgressSyncManager:
    """Tracks playback sessions and syncs progress to Stremio."""

    def __init__(self, hass: HomeAssistant, client: "StremioClient") -> None:
        self._hass = hass
        self._client = client
        self._sessions: dict[str, PlaybackSession] = {}
        self._unsub_state_listener: Callable[[], None] | None = None

    def start(self) -> None:
        """Subscribe to HA state changes. Idempotent."""
        if self._unsub_state_listener is not None:
            return
        self._unsub_state_listener = self._hass.bus.async_listen(
            EVENT_STATE_CHANGED, self._handle_state_change
        )
        _LOGGER.debug("ProgressSyncManager started")

    def stop(self) -> None:
        if self._unsub_state_listener is not None:
            self._unsub_state_listener()
            self._unsub_state_listener = None
        self._sessions.clear()

    def register_session(
        self,
        entity_id: str,
        media_id: str,
        media_type: str,
        media_content_id: str,
    ) -> None:
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
        if entity_id in self._sessions:
            del self._sessions[entity_id]
            _LOGGER.debug("Unregistered session: entity=%s", entity_id)

    def get_session(self, entity_id: str) -> PlaybackSession | None:
        return self._sessions.get(entity_id)

    def active_entities(self) -> list[str]:
        return list(self._sessions.keys())

    async def _handle_state_change(self, event: Event) -> None:
        """Process a state_changed event for any registered entity."""
        entity_id = event.data.get("entity_id")
        if entity_id not in self._sessions:
            return

        session = self._sessions[entity_id]
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        attrs = getattr(new_state, "attributes", {}) or {}
        current_content_id = attrs.get("media_content_id")
        state_value = new_state.state

        # User cast different content to this device -> our session is dead.
        if (
            current_content_id
            and current_content_id != session.media_content_id
            and state_value in _PLAYING_STATES | _PAUSED_STATES
        ):
            _LOGGER.debug(
                "Content mismatch on %s (expected %s, got %s); unregistering",
                entity_id,
                session.media_content_id,
                current_content_id,
            )
            self.unregister_session(entity_id)
            return

        # Update last-known position/duration from the player attributes.
        position = attrs.get("media_position")
        duration = attrs.get("media_duration")
        if position is not None:
            session.last_position = float(position)
        if duration is not None:
            session.last_duration = float(duration)

        # Terminal states: write a final update if we have meaningful state.
        if state_value in _TERMINAL_STATES:
            if session.last_position > 0 and session.last_duration > 0:
                await self._safe_write_progress(session)
            self.unregister_session(entity_id)
            return

        # Pause: immediate flush.
        if state_value in _PAUSED_STATES:
            await self._safe_write_progress(session)
            return

        # Playing: throttle.
        if state_value in _PLAYING_STATES:
            now = time.monotonic()
            if now - session.last_synced_at >= PROGRESS_SYNC_INTERVAL_SECONDS:
                await self._safe_write_progress(session)

    async def _safe_write_progress(self, session: PlaybackSession) -> None:
        """Write progress; swallow errors so listener stays healthy."""
        try:
            await self._client.async_update_library_progress(
                media_id=session.media_id,
                media_type=session.media_type,
                position_seconds=session.last_position,
                duration_seconds=session.last_duration,
            )
            session.last_synced_at = time.monotonic()
        except Exception as err:  # noqa: BLE001 — explicitly broad: sync must not crash
            _LOGGER.warning(
                "Progress sync write failed for %s: %s",
                session.media_id,
                err,
            )
