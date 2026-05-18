# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.0] — 2026-05-18

### Breaking

- Removed `media_player.stremio.async_play_media`. The entity is now
  status-only. Routes that targeted it now raise `ServiceValidationError`
  with a clear message pointing at `stremio.play_stream` or HA's native
  send-to-device picker in the media browser.
- Removed `apple_tv_handover.py` module and the `pyatv` runtime dependency.
  Apple TV streaming continues to work via HA's stock `apple_tv` integration.
- Removed Apple-TV-specific config options (`enable_apple_tv_handover`,
  `apple_tv_entity_id`, `apple_tv_credentials`, `apple_tv_identifier`,
  `handover_method`, `apple_tv_device`). Auto-migration silently strips
  these from existing config entries on first v0.7.0 load.
- Removed the custom Lovelace stream-dialog picker
  (`stremio-stream-dialog.js`). Dashboards that referenced
  `custom:stremio-stream-dialog` will need to be updated.
- Removed the `StremioAppleTVHandoverButton` button entity (no replacement —
  use `stremio.play_stream` with any media_player entity instead).

### Added

- New service `stremio.get_library` — clean, paginated, type-filtered
  library access. Designed for external clients (e.g. Zentiahome) that
  want to render their own library UI.
- HA media browser flow now uses the same `stream_resolver` as
  `stremio.play_stream`, so it can play infoHash-only Torrentio streams
  via a configured torrent server.
- Progress sync now works for plays initiated through the HA media browser
  (not only `stremio.play_stream` calls), via pending-session URL
  correlation.

### Deprecated

- `stremio.handover_to_apple_tv` service is now a compat shim that
  delegates to `stremio.play_stream`. One deprecation warning is logged
  per HA start. The service will be removed in a future release; migrate
  existing automations to `stremio.play_stream`.

## [0.4.0] - 2025-01-XX

### Added

- **Comprehensive UI Editor Support** for all cards
  - Collapsible sections (Entity, Display, Layout, Behavior, Device)
  - All configuration options now accessible via visual editor
  - Entity quick-select buttons for Stremio sensors

- **New Configuration Options** for Library and Continue Watching cards:
  - `poster_aspect_ratio`: 2:3, 16:9, 1:1, 4:3 options
  - `horizontal_scroll`: Carousel mode for compact layouts
  - `card_height`: Custom card height (px) or auto
  - `show_title`: Toggle titles below posters
  - `show_media_type_badge`: Movie/TV badge overlay
  - `tap_action`: details, play, or streams on tap
  - `default_sort`: recent, title, or progress
  - `apple_tv_entity`: Device integration for handover

- **New Configuration Options** for Browse Card:
  - Layout options: columns, poster_aspect_ratio, horizontal_scroll
  - Display toggles: show_title, show_rating
  - Behavior options: default_view, default_type, tap_action

- **New Configuration Options** for Media Details Card:
  - `show_description`, `show_progress` toggles
  - `expand_description`: Start expanded
  - `max_description_lines`: Collapsed line limit
  - `apple_tv_entity`: Device handover support

- **New Configuration Options** for Player Card:
  - `show_browse_button`, `show_backdrop`
  - `compact_mode`: Smaller layout option
  - `apple_tv_entity`: Device handover support

### Changed

- **Breaking**: Replaced `stremio-api` dependency with native aiohttp implementation
  - Resolves pydantic v2 dependency conflict with Home Assistant's pydantic v1
  - Improves compatibility and reduces external dependencies
  - All API calls now use aiohttp directly with proper error handling

- Card editors now use modern collapsible section design consistent with HA style
- Improved CSS variables for dynamic grid columns and poster aspect ratios
- Version bumped to 0.4.0 for automatic cache busting

## [0.3.6] - Previous Release

## [1.0.0] - 2026-01-17

### Added

- **Initial Release** 🎉

#### Core Features

- Config flow for easy setup with Stremio credentials
- Options flow for customizing update intervals and features
- DataUpdateCoordinator for efficient API polling

#### Entities

- **Sensors**
  - Current media sensor (shows currently playing content)
  - Last watched sensor
  - Library count sensor
  - Continue watching count sensor
- **Binary Sensors**
  - Is playing binary sensor
  - Has new content binary sensor
- **Media Player**
  - Read-only media player entity with playback state

#### Services

- `stremio.search_library` - Search your library
- `stremio.get_stream_url` - Get playable stream URLs
- `stremio.add_to_library` - Add media to library
- `stremio.remove_from_library` - Remove media from library
- `stremio.refresh_library` - Force library refresh
- `stremio.handover_to_apple_tv` - Apple TV handover service

#### Events

- `stremio_playback_started` - Fired when playback begins
- `stremio_playback_stopped` - Fired when playback stops
- `stremio_new_content` - Fired when new library content detected

#### Apple TV Handover

- AirPlay handover for HLS streams
- VLC deep link fallback for other formats
- Configurable handover method (Auto/AirPlay/VLC)

#### Custom Lovelace Cards

- `stremio-player-card` - Current playback display
- `stremio-library-card` - Library browser with search
- `stremio-media-details-card` - Detailed media info
- `stremio-stream-dialog` - Stream selector dialog
- Auto-registration of cards (no manual resource setup)

#### Media Source Integration

- Browse Stremio library in HA Media Browser
- Navigate: Library → Movies/Series → Seasons → Episodes
- Direct playback from Media Browser

#### Documentation

- Comprehensive setup guide
- Configuration reference
- Services documentation
- UI/Cards guide
- Events documentation
- Example automations
- API reference
- Development guide

### Dependencies

- Home Assistant 2025.1+
- Python 3.11+
- stremio-api>=0.1.0
- pyatv>=0.16.0 (optional, for AirPlay)

---

## Future Releases

### Planned for v1.1.0

- Real-time playback control (requires Stremio API updates)
- Chromecast handover support
- Multi-account support
- Statistics dashboard card

### Planned for v1.2.0

- Jellyfin library sync
- Plex cross-platform library
- IFTTT-style recipes
- Additional language translations

---

[Unreleased]: https://github.com/tamaygz/hacs-stremio/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/tamaygz/hacs-stremio/releases/tag/v1.0.0
