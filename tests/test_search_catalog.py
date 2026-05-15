"""Tests for the Stremio search_catalog client method.

Verifies the Cinemeta search endpoint integration, including URL construction,
result normalization (consistency with async_get_catalog), pagination via
``skip``, and error handling.
"""

import re

import pytest
from aioresponses import aioresponses

from custom_components.stremio.stremio_client import (
    StremioClient,
    StremioConnectionError,
)


@pytest.fixture
def mock_search_response():
    """Mock Cinemeta search response (raw meta format)."""
    return {
        "metas": [
            {
                "id": "tt1375666",
                "type": "movie",
                "name": "Inception",
                "poster": "https://example.com/inception.jpg",
                "releaseInfo": "2010",
                "description": "A thief who steals corporate secrets...",
                "genres": ["Action", "Sci-Fi"],
                "imdbRating": "8.8",
                "cast": ["Leonardo DiCaprio"],
                "director": ["Christopher Nolan"],
            },
            {
                "id": "tt1535108",
                "type": "movie",
                "name": "Elysium",
                "poster": "https://example.com/elysium.jpg",
                "releaseInfo": "2013",
                "genres": ["Action", "Sci-Fi"],
                "imdbRating": "6.6",
            },
        ]
    }


@pytest.mark.asyncio
async def test_search_catalog_movie(mock_search_response):
    """Search returns normalized items with title/year/rating/etc."""
    client = StremioClient("test@example.com", "fake_auth_key")

    with aioresponses() as mock_aio:
        mock_aio.get(
            "https://v3-cinemeta.strem.io/catalog/movie/imdb/search=Inception.json",
            payload=mock_search_response,
            status=200,
        )

        result = await client.async_search_catalog(
            query="Inception", media_type="movie"
        )

    assert len(result) == 2
    first = result[0]
    # Normalized fields shared with async_get_catalog
    assert first["id"] == "tt1375666"
    assert first["imdb_id"] == "tt1375666"
    assert first["type"] == "movie"
    assert first["title"] == "Inception"
    assert first["poster"] == "https://example.com/inception.jpg"
    assert first["year"] == "2010"
    assert first["rating"] == "8.8"
    assert first["genres"] == ["Action", "Sci-Fi"]
    # Original Cinemeta key name should NOT be exposed
    assert "name" not in first


@pytest.mark.asyncio
async def test_search_catalog_series():
    """Search supports series media type."""
    client = StremioClient("test@example.com", "fake_auth_key")

    series_response = {
        "metas": [
            {
                "id": "tt0903747",
                "type": "series",
                "name": "Breaking Bad",
                "poster": "https://example.com/bb.jpg",
            }
        ]
    }

    with aioresponses() as mock_aio:
        mock_aio.get(
            re.compile(
                r"https://v3-cinemeta\.strem\.io/catalog/series/imdb/search=Breaking.*\.json"
            ),
            payload=series_response,
            status=200,
        )

        result = await client.async_search_catalog(
            query="Breaking Bad", media_type="series"
        )

    assert len(result) == 1
    assert result[0]["title"] == "Breaking Bad"
    assert result[0]["type"] == "series"


@pytest.mark.asyncio
async def test_search_catalog_url_encodes_query():
    """Search query is URL-encoded so spaces / special chars work."""
    client = StremioClient("test@example.com", "fake_auth_key")

    with aioresponses() as mock_aio:
        # Spaces must be %20-encoded inside the path segment.
        mock_aio.get(
            "https://v3-cinemeta.strem.io/catalog/movie/imdb/search=The%20Matrix.json",
            payload={"metas": []},
            status=200,
        )

        result = await client.async_search_catalog(query="The Matrix")

    assert result == []


@pytest.mark.asyncio
async def test_search_catalog_pagination(mock_search_response):
    """Skip param is encoded into Cinemeta path extras (search=...&skip=...)."""
    client = StremioClient("test@example.com", "fake_auth_key")

    with aioresponses() as mock_aio:
        mock_aio.get(
            "https://v3-cinemeta.strem.io/catalog/movie/imdb/search=Inception&skip=20.json",
            payload=mock_search_response,
            status=200,
        )

        result = await client.async_search_catalog(
            query="Inception", media_type="movie", skip=20
        )

    assert len(result) == 2
    assert result[0]["title"] == "Inception"


@pytest.mark.asyncio
async def test_search_catalog_respects_limit(mock_search_response):
    """The limit parameter caps the returned list size."""
    client = StremioClient("test@example.com", "fake_auth_key")

    with aioresponses() as mock_aio:
        mock_aio.get(
            "https://v3-cinemeta.strem.io/catalog/movie/imdb/search=Inception.json",
            payload=mock_search_response,
            status=200,
        )

        result = await client.async_search_catalog(
            query="Inception", media_type="movie", limit=1
        )

    assert len(result) == 1


@pytest.mark.asyncio
async def test_search_catalog_empty_query():
    """Empty/whitespace queries short-circuit to [] without an HTTP call."""
    client = StremioClient("test@example.com", "fake_auth_key")

    # No mocked endpoint - if a request is made, aioresponses would raise.
    with aioresponses():
        assert await client.async_search_catalog(query="") == []
        assert await client.async_search_catalog(query="   ") == []


@pytest.mark.asyncio
async def test_search_catalog_http_error():
    """Non-200 status returns empty list (logged warning, no exception)."""
    client = StremioClient("test@example.com", "fake_auth_key")

    with aioresponses() as mock_aio:
        mock_aio.get(
            "https://v3-cinemeta.strem.io/catalog/movie/imdb/search=Inception.json",
            status=500,
        )

        result = await client.async_search_catalog(query="Inception")

    assert result == []


@pytest.mark.asyncio
async def test_search_catalog_network_error():
    """Network/client errors raise StremioConnectionError."""
    import aiohttp

    client = StremioClient("test@example.com", "fake_auth_key")

    with aioresponses() as mock_aio:
        mock_aio.get(
            "https://v3-cinemeta.strem.io/catalog/movie/imdb/search=Inception.json",
            exception=aiohttp.ClientError("Network error"),
        )

        with pytest.raises(StremioConnectionError):
            await client.async_search_catalog(query="Inception")


@pytest.mark.asyncio
async def test_search_catalog_no_metas_key():
    """Response without a ``metas`` key yields an empty list (defensive)."""
    client = StremioClient("test@example.com", "fake_auth_key")

    with aioresponses() as mock_aio:
        mock_aio.get(
            "https://v3-cinemeta.strem.io/catalog/movie/imdb/search=Inception.json",
            payload={},
            status=200,
        )

        result = await client.async_search_catalog(query="Inception")

    assert result == []
