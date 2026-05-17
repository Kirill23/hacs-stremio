"""Tests for stream URL resolution."""

from __future__ import annotations

import pytest

from custom_components.stremio.stream_resolver import (
    StreamUnplayableError,
    is_stream_playable,
    resolve_stream_url,
)


def test_resolve_returns_direct_https_url() -> None:
    stream = {"url": "https://debrid.example.com/file.mp4"}
    assert resolve_stream_url(stream) == "https://debrid.example.com/file.mp4"


def test_resolve_returns_direct_http_url() -> None:
    stream = {"url": "http://example.com/file.mp4"}
    assert resolve_stream_url(stream) == "http://example.com/file.mp4"


def test_resolve_constructs_torrent_server_url_with_fileidx() -> None:
    stream = {"infoHash": "abc123", "fileIdx": 2}
    server = "http://homeassistant.local:11470"
    assert (
        resolve_stream_url(stream, server)
        == "http://homeassistant.local:11470/abc123/2"
    )


def test_resolve_defaults_fileidx_to_zero() -> None:
    stream = {"infoHash": "abc123"}
    server = "http://homeassistant.local:11470"
    assert (
        resolve_stream_url(stream, server)
        == "http://homeassistant.local:11470/abc123/0"
    )


def test_resolve_strips_trailing_slash_from_server() -> None:
    stream = {"infoHash": "abc123"}
    server = "http://homeassistant.local:11470/"
    assert (
        resolve_stream_url(stream, server)
        == "http://homeassistant.local:11470/abc123/0"
    )


def test_resolve_prefers_direct_url_over_infohash() -> None:
    stream = {
        "url": "https://debrid.example.com/file.mp4",
        "infoHash": "abc123",
    }
    server = "http://homeassistant.local:11470"
    assert resolve_stream_url(stream, server) == "https://debrid.example.com/file.mp4"


def test_resolve_raises_when_neither_url_nor_infohash() -> None:
    with pytest.raises(StreamUnplayableError):
        resolve_stream_url({})


def test_resolve_raises_for_infohash_without_server() -> None:
    with pytest.raises(StreamUnplayableError):
        resolve_stream_url({"infoHash": "abc123"}, torrent_server_url=None)


def test_resolve_raises_for_infohash_with_empty_server() -> None:
    with pytest.raises(StreamUnplayableError):
        resolve_stream_url({"infoHash": "abc123"}, torrent_server_url="")


def test_resolve_raises_for_magnet_url() -> None:
    # Magnet links are not HTTP and must not be returned as-is.
    with pytest.raises(StreamUnplayableError):
        resolve_stream_url({"url": "magnet:?xt=urn:btih:abc123"})


def test_is_stream_playable_true_for_direct_url() -> None:
    assert is_stream_playable({"url": "https://example.com/x.mp4"}) is True


def test_is_stream_playable_false_for_infohash_without_server() -> None:
    assert is_stream_playable({"infoHash": "abc"}) is False


def test_is_stream_playable_true_for_infohash_with_server() -> None:
    assert is_stream_playable({"infoHash": "abc"}, "http://server:11470") is True


def test_is_stream_playable_false_for_empty_dict() -> None:
    assert is_stream_playable({}) is False
