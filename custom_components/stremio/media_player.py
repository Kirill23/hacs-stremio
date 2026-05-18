"""Media player platform for Stremio integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import StremioDataUpdateCoordinator
from .entity_helpers import get_device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Stremio media player platform.

    Args:
        hass: Home Assistant instance
        entry: Config entry
        async_add_entities: Callback to add entities
    """
    coordinator: StremioDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]

    # Create media player entity
    async_add_entities([StremioMediaPlayer(coordinator, entry)])


class StremioMediaPlayer(
    CoordinatorEntity[StremioDataUpdateCoordinator], MediaPlayerEntity
):
    """Representation of a Stremio media player."""

    _attr_supported_features = MediaPlayerEntityFeature(0)

    def __init__(
        self,
        coordinator: StremioDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the media player.

        Args:
            coordinator: Data update coordinator
            entry: Config entry
        """
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_media_player"
        self._attr_translation_key = "stremio"
        self._attr_has_entity_name = True
        self._attr_device_info = get_device_info(entry)
        # Track previous state to avoid unnecessary updates
        self._previous_state: MediaPlayerState | None = None
        self._previous_media_title: str | None = None
        self._previous_media_id: str | None = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator.

        This method is conservative about triggering state updates to prevent
        unnecessary UI redraws. State updates only occur:
        1. When the entity is first initialized (previous values are None)
        2. When the playback state changes (playing vs idle)
        """
        current_state = self.state
        current_title = self.media_title
        current_media_id = self._get_current_media_id()

        # Check if this is the first update (initialization)
        is_first_update = (
            self._previous_state is None
            and self._previous_media_title is None
            and self._previous_media_id is None
        )

        # Check if anything actually changed
        state_changed = current_state != self._previous_state
        title_changed = current_title != self._previous_media_title
        media_id_changed = current_media_id != self._previous_media_id
        something_changed = state_changed or title_changed or media_id_changed

        # Only update state if:
        # 1. This is the first update (initialization), OR
        # 2. The state went from PLAYING to IDLE or vice versa (major state change)
        should_update = is_first_update or (something_changed and state_changed)

        if should_update and something_changed:
            _LOGGER.debug(
                "Media player state update: state=%s (changed=%s), title=%s (changed=%s), "
                "media_id=%s (changed=%s), first=%s",
                current_state,
                state_changed,
                current_title,
                title_changed,
                current_media_id,
                media_id_changed,
                is_first_update,
            )
            self._previous_state = current_state
            self._previous_media_title = current_title
            self._previous_media_id = current_media_id
            self.async_write_ha_state()
        else:
            # Still update the tracked values to stay in sync, but don't trigger UI update
            if something_changed:
                _LOGGER.debug(
                    "Suppressing state update to avoid unnecessary UI redraws "
                    "(state=%s, title=%s, media_id=%s)",
                    current_state,
                    current_title,
                    current_media_id,
                )
                self._previous_state = current_state
                self._previous_media_title = current_title
                self._previous_media_id = current_media_id

    def _get_current_media_id(self) -> str | None:
        """Get the current media IMDB ID from coordinator data."""
        if self.coordinator.data and self.coordinator.data.get("current_watching"):
            return self.coordinator.data["current_watching"].get("imdb_id")
        return None

    @property
    def state(self) -> MediaPlayerState:
        """Return the state of the media player."""
        if self.coordinator.data and self.coordinator.data.get("current_watching"):
            return MediaPlayerState.PLAYING
        return MediaPlayerState.IDLE

    @property
    def media_content_type(self) -> str | None:
        """Return the content type of current playing media."""
        if self.coordinator.data and self.coordinator.data.get("current_watching"):
            media_type = self.coordinator.data["current_watching"].get("type")
            if media_type == "series":
                return MediaType.TVSHOW
            elif media_type == "movie":
                return MediaType.MOVIE
        return None

    @property
    def media_title(self) -> str | None:
        """Return the title of current playing media."""
        if self.coordinator.data and self.coordinator.data.get("current_watching"):
            return self.coordinator.data["current_watching"].get("title")
        return None

    @property
    def media_image_url(self) -> str | None:
        """Return the image URL of current playing media."""
        if self.coordinator.data and self.coordinator.data.get("current_watching"):
            return self.coordinator.data["current_watching"].get("poster")
        return None

    @property
    def media_position(self) -> int | None:
        """Return the position of current playing media in seconds."""
        if self.coordinator.data and self.coordinator.data.get("current_watching"):
            return self.coordinator.data["current_watching"].get("time_offset")
        return None

    @property
    def media_duration(self) -> int | None:
        """Return the duration of current playing media in seconds."""
        if self.coordinator.data and self.coordinator.data.get("current_watching"):
            return self.coordinator.data["current_watching"].get("duration")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes."""
        if self.coordinator.data and self.coordinator.data.get("current_watching"):
            current = self.coordinator.data["current_watching"]
            attrs = {
                "type": current.get("type"),
                "season": current.get("season"),
                "episode": current.get("episode"),
                "episode_title": current.get("episode_title"),
                "year": current.get("year"),
                "imdb_id": current.get("imdb_id"),
                "progress_percent": current.get("progress_percent"),
                "poster": current.get("poster"),
                # Extended metadata (may be populated from metadata fetch)
                "description": current.get("description"),
                "genres": current.get("genres", []),
                "cast": current.get("cast", []),
                "director": current.get("director"),
                "backdrop_url": current.get("backdrop"),
                "runtime": current.get("duration"),
                "rating": current.get("rating"),
                "series_title": current.get("series_title"),
            }
            # Remove None values to keep attributes clean
            return {k: v for k, v in attrs.items() if v is not None}
        return {}

    async def async_play_media(
        self, media_type: str, media_id: str, **kwargs: Any
    ) -> None:
        """Reject play_media — this entity is status-only in v2.

        Playback routing through this entity has been removed. Callers should
        invoke ``stremio.play_stream`` (resolves the URL and dispatches to
        any media_player entity), or play directly to a real device entity
        using HA's standard ``media_player.play_media`` service.
        """
        raise ServiceValidationError(
            "media_player.stremio is a status entity, not a playback target. "
            "Use stremio.play_stream or play directly to your device entity.",
            translation_domain=DOMAIN,
            translation_key="stremio_entity_not_a_player",
        )
