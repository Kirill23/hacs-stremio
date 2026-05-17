"""Playback manager: generalized media_player.play_media caller.

Knows how to play a stream URL on any HA media_player entity. Builds the
correct media_content_type/media_content_id, attaches metadata (title,
poster) via the ``extra`` field, and fails fast with translated errors
when the target entity is missing, the wrong domain, or unavailable.

Fire-and-forget: does not wait for the device to actually start playback.
Progress tracking is the ProgressSyncManager's job.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_MEDIA_PLAYER_DOMAIN_PREFIX = "media_player."
_UNAVAILABLE_STATES = {"unavailable", "unknown"}


class PlaybackManager:
    """Plays a stream URL on any HA media_player entity."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def play(
        self,
        entity_id: str,
        stream_url: str,
        media_info: dict[str, Any],
        content_type_override: str | None = None,
        blocking: bool = False,
    ) -> None:
        """Hand a stream URL to the named media_player entity.

        Args:
            entity_id: HA entity id (must start with "media_player.").
            stream_url: HTTP(S) URL the device will fetch and play.
            media_info: Optional metadata. Recognized keys: title, poster,
                year, type ("movie" or "series"), season, episode.
            content_type_override: If set, use this exact string as
                media_content_type (e.g. "url" to trigger the Apple TV
                integration's app-launch path, or a specific MIME like
                "application/x-mpegURL"). When ``None``, defaults to
                ``"tvshow"`` for series and ``"video"`` otherwise.
            blocking: Pass-through to ``hass.services.async_call``. The
                default ``False`` is right for the normal play_stream
                flow (fire-and-forget; progress sync tracks state from
                state-change events). Set ``True`` for callers like the
                Apple TV handover that need media_player errors to
                propagate so existing except blocks can handle them.

        Raises:
            ServiceValidationError: entity does not exist, is not a
                media_player, or is unavailable.
        """
        self._validate_entity(entity_id)

        title = media_info.get("title") or ""
        poster = media_info.get("poster") or ""
        if content_type_override is not None:
            media_type = content_type_override
        else:
            media_type = "tvshow" if media_info.get("type") == "series" else "video"

        extra: dict[str, Any] = {}
        if title:
            extra["title"] = title
        if poster:
            extra["thumb"] = poster
        # Optional helper fields some media_player platforms use:
        if media_info.get("year"):
            extra["metadata"] = {"year": media_info["year"]}

        payload: dict[str, Any] = {
            "entity_id": entity_id,
            "media_content_id": stream_url,
            "media_content_type": media_type,
        }
        if extra:
            payload["extra"] = extra

        _LOGGER.info(
            "Playing %r on %s (type=%s)", title or stream_url, entity_id, media_type
        )
        await self._hass.services.async_call(
            "media_player",
            "play_media",
            payload,
            blocking=blocking,
        )

    def _validate_entity(self, entity_id: str) -> None:
        if not entity_id.startswith(_MEDIA_PLAYER_DOMAIN_PREFIX):
            raise ServiceValidationError(
                f"entity_id must be a media_player entity, got: {entity_id}",
                translation_domain=DOMAIN,
                translation_key="entity_not_media_player",
                translation_placeholders={"entity_id": entity_id},
            )
        state = self._hass.states.get(entity_id)
        if state is None:
            raise ServiceValidationError(
                f"Entity not found: {entity_id}",
                translation_domain=DOMAIN,
                translation_key="entity_not_found",
                translation_placeholders={"entity_id": entity_id},
            )
        if state.state in _UNAVAILABLE_STATES:
            raise ServiceValidationError(
                f"Entity is {state.state}: {entity_id}",
                translation_domain=DOMAIN,
                translation_key="entity_unavailable",
                translation_placeholders={
                    "entity_id": entity_id,
                    "state": state.state,
                },
            )
