"""Tests for Stremio services."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.stremio.const import (
    DOMAIN,
    SERVICE_SEARCH_LIBRARY,
    SERVICE_GET_STREAMS,
    SERVICE_ADD_TO_LIBRARY,
    SERVICE_REMOVE_FROM_LIBRARY,
    SERVICE_REFRESH_LIBRARY,
    SERVICE_HANDOVER_TO_APPLE_TV,
    SERVICE_GET_UPCOMING_EPISODES,
    SERVICE_GET_RECOMMENDATIONS,
    SERVICE_GET_SIMILAR_CONTENT,
)
from custom_components.stremio.services import (
    async_setup_services,
    async_unload_services,
)

from .conftest import MOCK_LIBRARY_ITEMS, MOCK_STREAMS


@pytest.fixture
def mock_service_hass(hass: HomeAssistant, mock_coordinator):
    """Set up mock hass with coordinator for services."""
    mock_client = AsyncMock()
    mock_client.async_get_streams = AsyncMock(return_value=MOCK_STREAMS)
    mock_client.async_add_to_library = AsyncMock(return_value=True)
    mock_client.async_remove_from_library = AsyncMock(return_value=True)
    mock_client.async_get_upcoming_episodes = AsyncMock(
        return_value=[
            {
                "series_id": "tt0903747",
                "series_title": "Breaking Bad",
                "season": 5,
                "episode": 10,
                "episode_title": "Buried",
                "air_date": "2024-01-15T00:00:00Z",
                "air_date_formatted": "2024-01-15",
                "days_until": 3,
            }
        ]
    )
    mock_client.async_get_recommendations = AsyncMock(
        return_value=[
            {
                "id": "tt1234567",
                "title": "Recommended Movie",
                "type": "movie",
                "recommendation_reason": "Based on your interest in Drama",
            }
        ]
    )
    mock_client.async_get_similar_content = AsyncMock(
        return_value=[
            {
                "id": "tt7654321",
                "title": "Similar Show",
                "type": "series",
                "similarity_reason": "Similar Drama content",
            }
        ]
    )

    hass.data[DOMAIN] = {
        "test_entry": {
            "coordinator": mock_coordinator,
            "client": mock_client,
        }
    }

    return hass


class TestSearchLibraryService:
    """Tests for the search_library service."""

    @pytest.mark.asyncio
    async def test_search_by_title(self, mock_service_hass, mock_coordinator):
        """Test searching library by title."""
        mock_coordinator.data = {"library": MOCK_LIBRARY_ITEMS}

        await async_setup_services(mock_service_hass)

        # Call the service
        result = await mock_service_hass.services.async_call(
            DOMAIN,
            SERVICE_SEARCH_LIBRARY,
            {"query": "Shawshank", "search_type": "title", "limit": 10},
            blocking=True,
            return_response=True,
        )

        assert "results" in result
        assert "count" in result

    @pytest.mark.asyncio
    async def test_search_empty_query(self, mock_service_hass, mock_coordinator):
        """Test searching with empty query."""
        mock_coordinator.data = {"library": MOCK_LIBRARY_ITEMS}

        await async_setup_services(mock_service_hass)

        result = await mock_service_hass.services.async_call(
            DOMAIN,
            SERVICE_SEARCH_LIBRARY,
            {"query": "", "search_type": "all", "limit": 10},
            blocking=True,
            return_response=True,
        )

        # Should return empty results
        assert result["count"] == 0


class TestGetStreamsService:
    """Tests for the get_streams service."""

    @pytest.mark.asyncio
    async def test_get_stream_url_success(self, mock_service_hass, mock_coordinator):
        """Test getting stream URL successfully."""
        await async_setup_services(mock_service_hass)

        result = await mock_service_hass.services.async_call(
            DOMAIN,
            SERVICE_GET_STREAMS,
            {
                "media_id": "tt0111161",
                "media_type": "movie",
            },
            blocking=True,
            return_response=True,
        )

        assert "streams" in result
        assert "count" in result

    @pytest.mark.asyncio
    async def test_get_stream_url_series(self, mock_service_hass, mock_coordinator):
        """Test getting stream URL for series episode."""
        await async_setup_services(mock_service_hass)

        result = await mock_service_hass.services.async_call(
            DOMAIN,
            SERVICE_GET_STREAMS,
            {
                "media_id": "tt0903747",
                "media_type": "series",
                "season": 1,
                "episode": 1,
            },
            blocking=True,
            return_response=True,
        )

        assert "streams" in result

    @pytest.mark.asyncio
    async def test_get_stream_url_series_missing_episode(
        self, mock_service_hass, mock_coordinator
    ):
        """Test validation error when series missing season/episode."""
        await async_setup_services(mock_service_hass)

        with pytest.raises(ServiceValidationError):
            await mock_service_hass.services.async_call(
                DOMAIN,
                SERVICE_GET_STREAMS,
                {
                    "media_id": "tt0903747",
                    "media_type": "series",
                    # Missing season and episode
                },
                blocking=True,
                return_response=True,
            )

    @pytest.mark.asyncio
    async def test_get_streams_with_addon_order(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test get_streams service passes addon order to client."""
        from pytest_homeassistant_custom_component.common import MockConfigEntry

        # Create config entry with addon order preference
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={"email": "test@example.com", "password": "test"},
            options={
                "addon_stream_order": "Torrentio\\nCinemetaStreams",
                "stream_quality_preference": "any",
            },
            entry_id="test_entry_with_prefs",
        )
        entry.add_to_hass(hass)

        mock_client = AsyncMock()
        mock_client.async_get_streams = AsyncMock(return_value=MOCK_STREAMS)

        hass.data[DOMAIN] = {
            entry.entry_id: {
                "coordinator": mock_coordinator,
                "client": mock_client,
            }
        }

        await async_setup_services(hass)

        await hass.services.async_call(
            DOMAIN,
            SERVICE_GET_STREAMS,
            {
                "media_id": "tt0111161",
                "media_type": "movie",
            },
            blocking=True,
            return_response=True,
        )

        # Verify client was called with preferences
        mock_client.async_get_streams.assert_called_once()
        call_kwargs = mock_client.async_get_streams.call_args
        assert "addon_order" in call_kwargs.kwargs or len(call_kwargs.args) > 4

    @pytest.mark.asyncio
    async def test_get_streams_with_quality_preference(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test get_streams service passes quality preference to client."""
        from pytest_homeassistant_custom_component.common import MockConfigEntry

        # Create config entry with quality preference
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={"email": "test@example.com", "password": "test"},
            options={
                "addon_stream_order": "",
                "stream_quality_preference": "1080p",
            },
            entry_id="test_entry_quality",
        )
        entry.add_to_hass(hass)

        mock_client = AsyncMock()
        mock_client.async_get_streams = AsyncMock(return_value=MOCK_STREAMS)

        hass.data[DOMAIN] = {
            entry.entry_id: {
                "coordinator": mock_coordinator,
                "client": mock_client,
            }
        }

        await async_setup_services(hass)

        await hass.services.async_call(
            DOMAIN,
            SERVICE_GET_STREAMS,
            {
                "media_id": "tt0111161",
                "media_type": "movie",
            },
            blocking=True,
            return_response=True,
        )

        # Verify client was called with quality preference
        mock_client.async_get_streams.assert_called_once()
        call_kwargs = mock_client.async_get_streams.call_args
        assert "quality_preference" in call_kwargs.kwargs or len(call_kwargs.args) > 5


class TestAddToLibraryService:
    """Tests for the add_to_library service."""

    @pytest.mark.asyncio
    async def test_add_to_library(self, mock_service_hass, mock_coordinator):
        """Test adding item to library."""
        await async_setup_services(mock_service_hass)

        # Call the service without response since it doesn't support responses
        await mock_service_hass.services.async_call(
            DOMAIN,
            SERVICE_ADD_TO_LIBRARY,
            {
                "media_id": "tt1234567",
                "media_type": "movie",
            },
            blocking=True,
        )

        # Verify client method was called
        client = mock_service_hass.data[DOMAIN]["test_entry"]["client"]
        client.async_add_to_library.assert_called_once()

        # Verify refresh was requested
        mock_coordinator.async_request_refresh.assert_called()


class TestRemoveFromLibraryService:
    """Tests for the remove_from_library service."""

    @pytest.mark.asyncio
    async def test_remove_from_library(self, mock_service_hass, mock_coordinator):
        """Test removing item from library."""
        await async_setup_services(mock_service_hass)

        # Call the service without response since it doesn't support responses
        await mock_service_hass.services.async_call(
            DOMAIN,
            SERVICE_REMOVE_FROM_LIBRARY,
            {
                "media_id": "tt0111161",
            },
            blocking=True,
        )

        # Verify client method was called
        client = mock_service_hass.data[DOMAIN]["test_entry"]["client"]
        client.async_remove_from_library.assert_called_once()

        # Verify refresh was requested
        mock_coordinator.async_request_refresh.assert_called()


class TestRefreshLibraryService:
    """Tests for the refresh_library service."""

    @pytest.mark.asyncio
    async def test_refresh_library(self, mock_service_hass, mock_coordinator):
        """Test refreshing library."""
        await async_setup_services(mock_service_hass)

        # Call the service without response since it doesn't support responses
        await mock_service_hass.services.async_call(
            DOMAIN,
            SERVICE_REFRESH_LIBRARY,
            {},
            blocking=True,
        )

        mock_coordinator.async_request_refresh.assert_called()


class TestServiceRegistration:
    """Tests for service registration."""

    @pytest.mark.asyncio
    async def test_services_registered(
        self, hass: HomeAssistant, mock_config_entry, mock_coordinator
    ):
        """Test that all services are registered on setup."""
        mock_client = AsyncMock()
        hass.data[DOMAIN] = {
            mock_config_entry.entry_id: {
                "coordinator": mock_coordinator,
                "client": mock_client,
            }
        }

        await async_setup_services(hass)

        # Verify services were registered
        assert hass.services.has_service(DOMAIN, SERVICE_SEARCH_LIBRARY)
        assert hass.services.has_service(DOMAIN, SERVICE_GET_STREAMS)
        assert hass.services.has_service(DOMAIN, SERVICE_ADD_TO_LIBRARY)
        assert hass.services.has_service(DOMAIN, SERVICE_REMOVE_FROM_LIBRARY)
        assert hass.services.has_service(DOMAIN, SERVICE_REFRESH_LIBRARY)
        assert hass.services.has_service(DOMAIN, SERVICE_HANDOVER_TO_APPLE_TV)
        assert hass.services.has_service(DOMAIN, SERVICE_GET_UPCOMING_EPISODES)
        assert hass.services.has_service(DOMAIN, SERVICE_GET_RECOMMENDATIONS)
        assert hass.services.has_service(DOMAIN, SERVICE_GET_SIMILAR_CONTENT)

    @pytest.mark.asyncio
    async def test_services_unregistered(self, hass: HomeAssistant):
        """Test that all services are unregistered on unload."""
        # First setup services
        hass.data[DOMAIN] = {
            "test_entry": {"coordinator": MagicMock(), "client": AsyncMock()}
        }
        await async_setup_services(hass)

        # Then unload
        await async_unload_services(hass)

        # Verify services were removed
        assert not hass.services.has_service(DOMAIN, SERVICE_SEARCH_LIBRARY)
        assert not hass.services.has_service(DOMAIN, SERVICE_GET_STREAMS)


class TestGetUpcomingEpisodesService:
    """Tests for the get_upcoming_episodes service."""

    @pytest.mark.asyncio
    async def test_get_upcoming_episodes(self, mock_service_hass, mock_coordinator):
        """Test getting upcoming episodes."""
        await async_setup_services(mock_service_hass)

        result = await mock_service_hass.services.async_call(
            DOMAIN,
            SERVICE_GET_UPCOMING_EPISODES,
            {"days_ahead": 7},
            blocking=True,
            return_response=True,
        )

        assert "episodes" in result
        assert "count" in result
        assert "days_ahead" in result

        # Verify client method was called
        client = mock_service_hass.data[DOMAIN]["test_entry"]["client"]
        client.async_get_upcoming_episodes.assert_called_once_with(days_ahead=7)

    @pytest.mark.asyncio
    async def test_get_upcoming_episodes_default_days(
        self, mock_service_hass, mock_coordinator
    ):
        """Test getting upcoming episodes with default days_ahead."""
        await async_setup_services(mock_service_hass)

        result = await mock_service_hass.services.async_call(
            DOMAIN,
            SERVICE_GET_UPCOMING_EPISODES,
            {},
            blocking=True,
            return_response=True,
        )

        assert "episodes" in result
        # Verify default value of 7 was used
        client = mock_service_hass.data[DOMAIN]["test_entry"]["client"]
        client.async_get_upcoming_episodes.assert_called_once_with(days_ahead=7)


class TestGetRecommendationsService:
    """Tests for the get_recommendations service."""

    @pytest.mark.asyncio
    async def test_get_recommendations_all(self, mock_service_hass, mock_coordinator):
        """Test getting all recommendations."""
        await async_setup_services(mock_service_hass)

        result = await mock_service_hass.services.async_call(
            DOMAIN,
            SERVICE_GET_RECOMMENDATIONS,
            {"limit": 20},
            blocking=True,
            return_response=True,
        )

        assert "recommendations" in result
        assert "count" in result

        # Verify client method was called
        client = mock_service_hass.data[DOMAIN]["test_entry"]["client"]
        client.async_get_recommendations.assert_called_once_with(
            media_type=None,
            limit=20,
        )

    @pytest.mark.asyncio
    async def test_get_recommendations_movies_only(
        self, mock_service_hass, mock_coordinator
    ):
        """Test getting movie recommendations only."""
        await async_setup_services(mock_service_hass)

        result = await mock_service_hass.services.async_call(
            DOMAIN,
            SERVICE_GET_RECOMMENDATIONS,
            {"media_type": "movie", "limit": 10},
            blocking=True,
            return_response=True,
        )

        assert "recommendations" in result
        assert "media_type" in result

        # Verify client method was called with movie type
        client = mock_service_hass.data[DOMAIN]["test_entry"]["client"]
        client.async_get_recommendations.assert_called_once_with(
            media_type="movie",
            limit=10,
        )

    @pytest.mark.asyncio
    async def test_get_recommendations_series_only(
        self, mock_service_hass, mock_coordinator
    ):
        """Test getting series recommendations only."""
        await async_setup_services(mock_service_hass)

        result = await mock_service_hass.services.async_call(
            DOMAIN,
            SERVICE_GET_RECOMMENDATIONS,
            {"media_type": "series", "limit": 15},
            blocking=True,
            return_response=True,
        )

        assert "recommendations" in result

        # Verify client method was called with series type
        client = mock_service_hass.data[DOMAIN]["test_entry"]["client"]
        client.async_get_recommendations.assert_called_once_with(
            media_type="series",
            limit=15,
        )


class TestGetStreamsPlayableFlag:
    """Tests for the playable flag annotation in get_streams."""

    @pytest.mark.asyncio
    async def test_get_streams_annotates_playable_flag(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Each stream in the response gets a 'playable' bool."""
        from pytest_homeassistant_custom_component.common import MockConfigEntry

        fake_streams = [
            {"name": "Direct URL", "url": "https://debrid/x.mp4"},
            {"name": "Magnet only", "infoHash": "abc123"},
        ]

        entry = MockConfigEntry(
            domain=DOMAIN,
            data={"email": "test@example.com", "password": "test"},
            options={
                "addon_stream_order": "",
                "stream_quality_preference": "any",
            },
            entry_id="test_entry_playable",
        )
        entry.add_to_hass(hass)

        mock_client = AsyncMock()
        mock_client.async_get_streams = AsyncMock(return_value=fake_streams)

        hass.data[DOMAIN] = {
            entry.entry_id: {
                "coordinator": mock_coordinator,
                "client": mock_client,
            }
        }

        await async_setup_services(hass)

        result = await hass.services.async_call(
            DOMAIN,
            SERVICE_GET_STREAMS,
            {"media_id": "tt001", "media_type": "movie"},
            blocking=True,
            return_response=True,
        )

        streams = result["streams"]
        assert streams[0]["playable"] is True
        assert streams[1]["playable"] is False


class TestGetSimilarContentService:
    """Tests for the get_similar_content service."""

    @pytest.mark.asyncio
    async def test_get_similar_content(self, mock_service_hass, mock_coordinator):
        """Test getting similar content."""
        await async_setup_services(mock_service_hass)

        result = await mock_service_hass.services.async_call(
            DOMAIN,
            SERVICE_GET_SIMILAR_CONTENT,
            {"media_id": "tt0903747", "limit": 10},
            blocking=True,
            return_response=True,
        )

        assert "similar" in result
        assert "count" in result
        assert "source_media_id" in result

        # Verify client method was called
        client = mock_service_hass.data[DOMAIN]["test_entry"]["client"]
        client.async_get_similar_content.assert_called_once_with(
            media_id="tt0903747",
            limit=10,
        )

    @pytest.mark.asyncio
    async def test_get_similar_content_default_limit(
        self, mock_service_hass, mock_coordinator
    ):
        """Test getting similar content with default limit."""
        await async_setup_services(mock_service_hass)

        result = await mock_service_hass.services.async_call(
            DOMAIN,
            SERVICE_GET_SIMILAR_CONTENT,
            {"media_id": "tt0468569"},
            blocking=True,
            return_response=True,
        )

        assert "similar" in result

        # Verify default limit of 10 was used
        client = mock_service_hass.data[DOMAIN]["test_entry"]["client"]
        client.async_get_similar_content.assert_called_once_with(
            media_id="tt0468569",
            limit=10,
        )


class TestPlayStreamService:
    """Tests for the play_stream service."""

    @pytest.mark.asyncio
    async def test_play_stream_resolves_and_dispatches(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """play_stream calls media_player.play_media and registers a session."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from pytest_homeassistant_custom_component.common import MockConfigEntry

        from custom_components.stremio.const import DOMAIN

        entry = MockConfigEntry(
            domain=DOMAIN,
            data={"email": "test@example.com", "password": "test"},
            options={
                "addon_stream_order": "",
                "stream_quality_preference": "any",
            },
            entry_id="test_entry_play_stream",
        )
        entry.add_to_hass(hass)

        coordinator = MagicMock()
        coordinator.register_playback_session = MagicMock()
        mock_client = AsyncMock()

        hass.data[DOMAIN] = {
            entry.entry_id: {
                "coordinator": coordinator,
                "client": mock_client,
            }
        }

        # Make sure the target entity exists in HA state.
        hass.states.async_set("media_player.tv", "idle")

        await async_setup_services(hass)

        with patch(
            "custom_components.stremio.services.PlaybackManager"
        ) as mock_pm_class:
            mock_pm = MagicMock()
            mock_pm.play = AsyncMock(return_value=None)
            mock_pm_class.return_value = mock_pm

            await hass.services.async_call(
                DOMAIN,
                "play_stream",
                {
                    "stream_url": "https://debrid/x.mp4",
                    "entity_id": "media_player.tv",
                    "media_id": "tt001",
                    "media_type": "movie",
                },
                blocking=True,
            )

        # Registered the session
        coordinator.register_playback_session.assert_called_once_with(
            entity_id="media_player.tv",
            media_id="tt001",
            media_type="movie",
            media_content_id="https://debrid/x.mp4",
        )

    @pytest.mark.asyncio
    async def test_play_stream_raises_for_unplayable_stream(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """A stream entry with no URL and no torrent server -> ServiceValidationError."""
        from homeassistant.exceptions import ServiceValidationError

        from pytest_homeassistant_custom_component.common import MockConfigEntry

        from custom_components.stremio.const import DOMAIN

        entry = MockConfigEntry(
            domain=DOMAIN,
            data={"email": "test@example.com", "password": "test"},
            options={
                "addon_stream_order": "",
                "stream_quality_preference": "any",
                # No torrent_server_url configured
            },
            entry_id="test_entry_unplayable",
        )
        entry.add_to_hass(hass)

        mock_client = AsyncMock()
        hass.data[DOMAIN] = {
            entry.entry_id: {
                "coordinator": mock_coordinator,
                "client": mock_client,
            }
        }

        # Make sure the target entity exists
        hass.states.async_set("media_player.tv", "idle")

        await async_setup_services(hass)

        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN,
                "play_stream",
                {
                    # Empty stream_url with no torrent server -> unplayable
                    "stream_url": "",
                    "entity_id": "media_player.tv",
                    "media_id": "tt001",
                    "media_type": "movie",
                },
                blocking=True,
            )

    @pytest.mark.asyncio
    async def test_play_stream_resolves_infohash_via_torrent_server(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """info_hash + configured torrent_server_url -> playable URL handed to play_media."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from pytest_homeassistant_custom_component.common import MockConfigEntry

        from custom_components.stremio.const import (
            CONF_TORRENT_SERVER_URL,
            DOMAIN,
        )

        entry = MockConfigEntry(
            domain=DOMAIN,
            data={"email": "test@example.com", "password": "test"},
            options={
                "addon_stream_order": "",
                "stream_quality_preference": "any",
                CONF_TORRENT_SERVER_URL: "http://127.0.0.1:11470",
            },
            entry_id="test_entry_infohash",
        )
        entry.add_to_hass(hass)

        coordinator = MagicMock()
        coordinator.register_playback_session = MagicMock()
        mock_client = AsyncMock()

        hass.data[DOMAIN] = {
            entry.entry_id: {
                "coordinator": coordinator,
                "client": mock_client,
            }
        }

        hass.states.async_set("media_player.tv", "idle")
        await async_setup_services(hass)

        with patch(
            "custom_components.stremio.services.PlaybackManager"
        ) as mock_pm_class:
            mock_pm = MagicMock()
            mock_pm.play = AsyncMock(return_value=None)
            mock_pm_class.return_value = mock_pm

            await hass.services.async_call(
                DOMAIN,
                "play_stream",
                {
                    # InfoHash-only stream (typical Torrentio without debrid)
                    "stream_url": "",
                    "info_hash": "abc123def456",
                    "file_idx": 2,
                    "entity_id": "media_player.tv",
                    "media_id": "tt001",
                    "media_type": "movie",
                },
                blocking=True,
            )

            # Resolver should have built the torrent-server URL and passed it
            # to PlaybackManager.play, and the session should be registered
            # against that same URL.
            mock_pm.play.assert_awaited_once()
            play_kwargs = mock_pm.play.await_args.kwargs
            expected_url = "http://127.0.0.1:11470/abc123def456/2"
            assert play_kwargs["stream_url"] == expected_url

        coordinator.register_playback_session.assert_called_once_with(
            entity_id="media_player.tv",
            media_id="tt001",
            media_type="movie",
            media_content_id=expected_url,
        )
