"""Constants for the Stremio integration."""

import json
from datetime import timedelta
from pathlib import Path
from typing import Final

# Read version from manifest.json
MANIFEST_PATH = Path(__file__).parent / "manifest.json"
with open(MANIFEST_PATH, encoding="utf-8") as f:
    INTEGRATION_VERSION: Final[str] = json.load(f).get("version", "0.0.0")

DOMAIN: Final = "stremio"

# Frontend constants
URL_BASE: Final[str] = "/stremio"

# JavaScript modules to register with Lovelace
JSMODULES: Final[list[dict[str, str]]] = [
    {
        "name": "Stremio Cards Bundle",
        "filename": "stremio-card-bundle.js",
        "version": INTEGRATION_VERSION,
    },
]

# Update intervals
DEFAULT_SCAN_INTERVAL: Final = timedelta(seconds=30)
LIBRARY_SCAN_INTERVAL: Final = timedelta(minutes=5)
CATALOG_SCAN_INTERVAL: Final = timedelta(hours=6)  # Catalogs change less frequently

# Configuration
CONF_AUTH_KEY: Final = "auth_key"
CONF_PLAYER_SCAN_INTERVAL: Final = "player_scan_interval"
CONF_LIBRARY_SCAN_INTERVAL: Final = "library_scan_interval"
CONF_POLLING_GATE_ENTITIES: Final = "polling_gate_entities"
CONF_SHOW_COPY_URL: Final = "show_copy_url"
CONF_DEFAULT_CATALOG_SOURCE: Final = "default_catalog_source"
CONF_ADDON_STREAM_ORDER: Final = "addon_stream_order"
CONF_STREAM_QUALITY_PREFERENCE: Final = "stream_quality_preference"
CONF_RESET_ADDON_ORDER: Final = "reset_addon_order"

# Torrent server (companion add-on)
CONF_TORRENT_SERVER_URL: Final = "torrent_server_url"
DEFAULT_TORRENT_SERVER_URL: Final = ""
STREMIO_SERVER_DEFAULT_PORT: Final = 11470
STREMIO_SERVER_PROBE_TIMEOUT: Final = 2.0  # seconds, for options-flow auto-detect
STREMIO_SERVER_PROBE_HOSTS: Final[tuple[str, ...]] = (
    "homeassistant.local",
    "127.0.0.1",
)

# Progress sync
CONF_PROGRESS_SYNC_ENABLED: Final = "progress_sync_enabled"
DEFAULT_PROGRESS_SYNC_ENABLED: Final = True
PROGRESS_SYNC_INTERVAL_SECONDS: Final = 30
PENDING_SESSION_TTL_SECONDS: Final = 60
WATCHED_THRESHOLD: Final = 0.9  # mark watched when position/duration >= this

# Options defaults
DEFAULT_PLAYER_SCAN_INTERVAL: Final = 30  # seconds
DEFAULT_LIBRARY_SCAN_INTERVAL: Final = 300  # seconds (5 minutes)
DEFAULT_CONTINUE_WATCHING_LIMIT: Final = 100  # Max items in continue watching list
DEFAULT_POLLING_GATE_ENTITIES: Final[list[str]] = []
DEFAULT_SHOW_COPY_URL: Final = True  # Show "Copy URL" in media browser streams
DEFAULT_CATALOG_SOURCE: Final = "cinemeta"  # Default metadata addon
DEFAULT_ADDON_STREAM_ORDER: Final[list[str]] = []  # Empty = use Stremio's order
DEFAULT_STREAM_QUALITY_PREFERENCE: Final = "any"  # any, 4k, 1080p, 720p

# Stream quality options
STREAM_QUALITY_OPTIONS: Final = ["any", "4k", "1080p", "720p", "480p"]

# Catalog source options (addon IDs)
CATALOG_SOURCE_OPTIONS: Final = {
    "cinemeta": "Cinemeta (Default)",
    "tmdb": "TMDB",
}

# Polling gate intervals (seconds)
POLLING_GATE_ACTIVE_INTERVAL: Final = None  # Use configured interval
POLLING_GATE_IDLE_INTERVAL: Final = 86400  # 24 hours when all gate entities are off

# Sensor Types
SENSOR_TYPES: Final = {
    "current_media": {
        "name": "Current Watching",
        "icon": "mdi:play-circle",
        "device_class": None,
    },
    "last_watched": {
        "name": "Last Watched",
        "icon": "mdi:history",
        "device_class": None,
    },
    "current_position": {
        "name": "Current Position",
        "icon": "mdi:progress-clock",
        "device_class": "duration",
        "unit": "s",
    },
    "current_duration": {
        "name": "Current Duration",
        "icon": "mdi:timer",
        "device_class": "duration",
        "unit": "s",
    },
    "library_count": {
        "name": "Library Count",
        "icon": "mdi:library",
        "device_class": None,
    },
    "continue_watching": {
        "name": "Continue Watching",
        "icon": "mdi:play-pause",
        "device_class": None,
    },
    "total_watch_time": {
        "name": "Total Watch Time",
        "icon": "mdi:clock",
        "device_class": "duration",
        "unit": "h",
    },
    "favorite_genre": {
        "name": "Favorite Genre",
        "icon": "mdi:star",
        "device_class": None,
    },
    "watch_streak": {
        "name": "Watch Streak",
        "icon": "mdi:fire",
        "device_class": None,
        "unit": "days",
    },
}

# Binary Sensor Types
BINARY_SENSOR_TYPES: Final = {
    "is_playing": {
        "name": "Playing",
        "icon": "mdi:play",
        "device_class": "running",
    },
    "new_content": {
        "name": "New Content Available",
        "icon": "mdi:new-box",
        "device_class": "update",
    },
}

# Event Types
EVENT_PLAYBACK_STARTED: Final = "stremio_playback_started"
EVENT_PLAYBACK_STOPPED: Final = "stremio_playback_stopped"
EVENT_NEW_CONTENT: Final = "stremio_new_content"
EVENT_STREAM_URL: Final = "stremio_stream_url"
EVENT_NEW_EPISODES: Final = "stremio_new_episodes_detected"
EVENT_RESUME_AVAILABLE: Final = "stremio_resume_available"

# Service Names
SERVICE_GET_STREAMS: Final = "get_streams"
SERVICE_GET_SERIES_METADATA: Final = "get_series_metadata"
SERVICE_SEARCH_LIBRARY: Final = "search_library"
SERVICE_GET_LIBRARY: Final = "get_library"
SERVICE_ADD_TO_LIBRARY: Final = "add_to_library"
SERVICE_REMOVE_FROM_LIBRARY: Final = "remove_from_library"
SERVICE_REFRESH_LIBRARY: Final = "refresh_library"
SERVICE_HANDOVER_TO_APPLE_TV: Final = "handover_to_apple_tv"
SERVICE_BROWSE_CATALOG: Final = "browse_catalog"
SERVICE_SEARCH_CATALOG: Final = "search_catalog"
SERVICE_GET_UPCOMING_EPISODES: Final = "get_upcoming_episodes"
SERVICE_GET_RECOMMENDATIONS: Final = "get_recommendations"
SERVICE_GET_SIMILAR_CONTENT: Final = "get_similar_content"
SERVICE_GET_ADDONS: Final = "get_addons"
SERVICE_PLAY_STREAM: Final = "play_stream"

# API Constants
API_BASE_URL: Final = "https://api.strem.io"
API_TIMEOUT: Final = 10

# Defaults
DEFAULT_NAME: Final = "Stremio"

# Catalog constants
CINEMETA_BASE_URL: Final = "https://v3-cinemeta.strem.io"
CATALOG_TYPE_MOVIE: Final = "movie"
CATALOG_TYPE_SERIES: Final = "series"
CATALOG_ID_TOP: Final = "top"
CATALOG_ID_POPULAR: Final = "popular"

# Catalog definitions for browsing
CATALOG_DEFINITIONS: Final = {
    "popular_movies": {
        "name": "Popular Movies",
        "type": CATALOG_TYPE_MOVIE,
        "catalog_id": CATALOG_ID_TOP,
        "extra": "popular.json",
    },
    "popular_series": {
        "name": "Popular TV Shows",
        "type": CATALOG_TYPE_SERIES,
        "catalog_id": CATALOG_ID_TOP,
        "extra": "popular.json",
    },
    "new_movies": {
        "name": "New Movies",
        "type": CATALOG_TYPE_MOVIE,
        "catalog_id": CATALOG_ID_TOP,
        "extra": "popular.json?genre=",  # Recent movies from popular
    },
    "new_series": {
        "name": "New TV Shows",
        "type": CATALOG_TYPE_SERIES,
        "catalog_id": CATALOG_ID_TOP,
        "extra": "popular.json?genre=",  # Recent series from popular
    },
}

# Supported genres for Cinemeta filtering
CINEMETA_GENRES: Final = [
    "Action",
    "Adventure",
    "Animation",
    "Biography",
    "Comedy",
    "Crime",
    "Documentary",
    "Drama",
    "Family",
    "Fantasy",
    "History",
    "Horror",
    "Mystery",
    "Romance",
    "Sci-Fi",
    "Sport",
    "Thriller",
    "War",
    "Western",
]
