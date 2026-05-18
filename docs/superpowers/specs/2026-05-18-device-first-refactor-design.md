# v2: Device-first refactor

**Status:** Approved design, ready for implementation planning
**Date:** 2026-05-18
**Author:** Brainstormed with Claude (Opus 4.7)
**Predecessor:** `2026-05-17-torrentio-playback-design.md` (v1 — kept the Apple-TV-first code paths alongside the new device-agnostic pipeline)

---

## Problem

v1 added a clean device-agnostic playback pipeline (`stream_resolver` → `PlaybackManager` → `ProgressSyncManager`) but kept the Apple-TV-first code paths intact:

- `media_player.async_play_media` is still hardcoded to attempt Apple TV handover, and silently returns when Apple TV isn't configured. The visible failure mode is *click play in HA media browser → nothing happens*.
- `apple_tv_handover.py` (~990 lines) and the `pyatv>=0.16.0` dependency exist solely to do AirPlay things that HA's stock `apple_tv` integration already does.
- Apple-TV-specific options dominate the config flow (six related keys).
- The custom picker dialog in `frontend/stremio-stream-dialog.js` is a parallel device-selection UI to HA's native "send to device" picker. It has its own bugs (Lovelace caching pain, infoHash forwarding that we patched, etc.) and adds maintenance load.

Practical result of the v1 dual-pipeline state: users hit the *old* code path through the most obvious entry point (HA media browser). The integration is functionally device-agnostic only inside the picker dialog.

The stated v2 direction from the user:

> "we should be able to stream on all media devices. no specific focus on apple tv"

> "we are refactoring this integration to work with our new concept. apple tv no longer primary objective — streaming across all type of media devices is"

## Locked-in decisions

| Decision | Choice | Rationale |
|---|---|---|
| Primary user surfaces for v2 | **Zentiahome** (consuming HA APIs) + **HA media browser** | The custom Lovelace picker dialog and stock HA dashboard cards are explicitly out of scope as primary surfaces. |
| `media_player.stremio` | **Status-only entity** | Removes the dual identity (status display + play target). HA's media-browser send-to-device picker shows real device entities; Stremio doesn't try to be a virtual receiver. |
| `apple_tv_handover.py` + `pyatv` dependency | **Remove entirely** | HA's stock `apple_tv` integration handles AirPlay. Stremio doesn't need its own implementation. |
| `stremio.handover_to_apple_tv` service | **Alias to `stremio.play_stream`** (compat shim, deprecated) | Existing user automations keep working. One INFO-level deprecation warning per HA start. |
| Apple-TV-specific config options | **Remove from config flow** | `CONF_APPLE_TV_*`, `CONF_ENABLE_APPLE_TV_HANDOVER`, `CONF_HANDOVER_METHOD` are no longer meaningful. |
| Custom Lovelace cards | **Keep display cards; remove only the picker dialog** | Display cards (library, browse, recommendations, etc.) are useful dashboard widgets. The picker is the buggy / hard-to-maintain one. |
| New `stremio.get_library` service | **Add** | Explicit, paginatable library access for Zentiahome. Avoids the implicit "empty query matches all" trick that today's `search_library` relies on. |
| Existing-user migration | **Auto-migrate config entries silently** | Strip removed options on load via `async_migrate_entry`. No reconfiguration required. |
| Implementation approach | **Single-branch refactor** | All changes in one feature branch; cohesive PR (or local merge). Staging adds ceremony without value for solo development. |

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  Clients (interchangeable)                                            │
│                                                                       │
│  Zentiahome (your app)              HA Media Browser                  │
│  ────────────────────              ───────────────────                │
│  HA REST + WebSocket               Native HA UI                       │
└───────────────┬───────────────────────────┬──────────────────────────┘
                │                           │
                ▼                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Home Assistant + Stremio integration (v2)                            │
│                                                                       │
│  Services:                                                            │
│    stremio.search_library        stremio.browse_catalog               │
│    stremio.get_library  (NEW)    stremio.search_catalog               │
│    stremio.get_streams           stremio.get_recommendations          │
│    stremio.get_series_metadata   stremio.get_upcoming_episodes        │
│    stremio.add_to_library        stremio.remove_from_library          │
│    stremio.refresh_library       stremio.get_addons                   │
│    stremio.play_stream           ← the only play action               │
│    stremio.handover_to_apple_tv  ← deprecated, alias of play_stream   │
│                                                                       │
│  Status entities (no playback control):                               │
│    media_player.stremio          sensor.stremio_*                     │
│    binary_sensor.stremio_*                                            │
│                                                                       │
│  Internal modules (preserved from v1):                                │
│    StremioClient                 PlaybackManager                      │
│    StremioDataUpdateCoordinator  ProgressSyncManager                  │
│    stream_resolver               media_source (now uses resolver)     │
│                                                                       │
│  REMOVED:                                                             │
│    apple_tv_handover.py (~990 LOC)                                    │
│    pyatv dependency                                                   │
│    Apple-TV config options (~6 keys)                                  │
│    Picker dialog (frontend/stremio-stream-dialog.js, ~810 LOC)        │
│    Apple-TV branches in media_player.async_play_media                 │
└──────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
              ┌─────────────────────────────────────┐
              │  Real device entities, via HA's     │
              │  stock integrations (cast,          │
              │  apple_tv, dlna_dmr, etc.)          │
              │                                     │
              │  media_player.chromecast_*          │
              │  media_player.samsung_tv            │
              │  media_player.apple_tv_*            │
              └─────────────────────────────────────┘
                                  │
                                  │ HTTP(S) URL
                                  ▼
              ┌─────────────────────────────────────┐
              │  Stream source                       │
              │  • Debrid HTTPS (Torrentio + RD)     │
              │  • stremio/server torrent gateway    │
              │    (companion add-on)                │
              │  • Other addons (WatchHub etc.)      │
              └─────────────────────────────────────┘
```

**The big simplification:** the integration owns no device-specific code. It's a Stremio↔HA bridge that dispatches to whatever `media_player.*` entity HA already knows about. Apple TV, Chromecast, Samsung, DLNA all look identical from Stremio's perspective.

## Data flow

### Flow A — Zentiahome plays a movie on a Chromecast

```
1. Zentiahome connects HA WebSocket. Subscribes to:
   - state_changed for media_player.* and sensor.stremio_*
   - Stremio custom events (stremio_playback_started, etc.)

2. Browse library. Either:
   a. Call stremio.get_library {type, skip, limit}  ← new in v2
   b. Call stremio.search_library  {query, search_type}
   c. WS: media_source/browse_media  {"media_content_id": "media-source://stremio/"}

3. User picks a media item. Zentiahome calls:
   POST /api/services/stremio/get_streams
   data: {media_id, media_type, season?, episode?}
   ← returns streams[] each with {name, title, url, infoHash, fileIdx,
                                    quality, size, playable: bool}

4. Zentiahome renders its own stream picker UI.
   User picks one stream + a target device (Zentiahome already has the
   media_player.* list from step 1's WS subscription).

5. Zentiahome calls:
   POST /api/services/stremio/play_stream
   data: {
       stream_url:  <chosen.url>      // may be empty
       info_hash:   <chosen.infoHash> // may be empty
       file_idx:    <chosen.fileIdx>  // defaults 0
       entity_id:   "media_player.chromecast_living_room"
       media_id:    "tt1375666"
       media_type:  "movie"
       season:      <optional>
       episode:     <optional>
   }

6. services.handle_play_stream:
   a. stream_resolver.resolve_stream_url(stream_dict, torrent_server_url)
      → direct URL OR http://torrent-server/<infoHash>/<fileIdx>
   b. PlaybackManager.play(entity_id=chromecast, stream_url=resolved, media_info=...)
      → hass.services.async_call("media_player", "play_media", ..., blocking=False)
   c. coordinator.register_playback_session(entity_id, media_id, media_type, media_content_id)

7. Chromecast plays. State transitions to "playing" propagate over the
   bus. Zentiahome's WS subscription sees them → its UI reflects state.

8. ProgressSyncManager observes the same state changes. On 30s ticks
   while playing, immediate on pause/stop:
   → StremioClient.async_update_library_progress() writes to api.strem.io
   → continue-watching syncs across all the user's Stremio devices

9. Transport control: Zentiahome calls standard HA services:
   POST /api/services/media_player/media_pause  {entity_id: chromecast}
   POST /api/services/media_player/media_seek   {entity_id, seek_position}
   POST /api/services/media_player/media_stop   {entity_id}
   No Stremio code involved in transport — HA → device integration directly.
```

**Key insight for Zentiahome:** after the initial `play_stream`, all transport (pause, seek, stop, volume) is standard HA `media_player.*` services on the device entity. Zentiahome doesn't need any Stremio-specific control surface for transport — only for content discovery and the initial play.

### Flow B — HA media browser plays a movie on a smart TV

```
1. User opens Settings → Media → Stremio in HA UI.

2. media_source.async_browse_media returns library + catalogs as tree.

3. User clicks a media item. HA presents the available stream entries
   (each is a child node — quality, size, source addon).

4. User clicks a specific stream. HA shows the native "Play on..." picker.
   media_player.stremio is NOT in this list (in v2 it has no play_media).
   User picks media_player.samsung_tv.

5. HA calls media_source.async_resolve_media → integration uses the
   same stream_resolver as Zentiahome's path → returns
   PlayMedia(url=resolved_url, mime_type=detected_mime_type).

6. HA's framework calls media_player.play_media on samsung_tv with that
   URL — no Stremio code in the dispatch path.

7. ProgressSync observes samsung_tv entering "playing" with the
   matching media_content_id (Option X in the design — see below) and
   registers a session implicitly. Progress writes proceed as in Flow A.
```

**Option X (cross-flow progress sync):** When `media_source.async_resolve_media` returns a URL, it also calls `coordinator.register_pending_session(media_id, media_type, resolved_url)`. The ProgressSyncManager's state listener correlates incoming state_changed events on any media_player to the pending session by matching `media_content_id == resolved_url`. Sessions get GC'd after a short TTL (e.g., 60s) if no matching state_changed arrives. Adds ~40 LOC to `progress_sync.py`. The alternative — only tracking Zentiahome-initiated plays — leaves the HA media browser flow without continue-watching sync, which would be surprising and inconsistent.

### Service contract for Zentiahome (full surface)

```
DISCOVERY (read-only):
  GET  /api/states/sensor.stremio_*
  GET  /api/states/media_player.stremio      (current playback metadata)
  WS   subscribe state_changed                (filter entity prefix)
  WS   subscribe event_type=stremio_*

LIBRARY ACTIONS (Stremio cloud):
  POST /api/services/stremio/search_library
  POST /api/services/stremio/get_library          (NEW in v2)
  POST /api/services/stremio/browse_catalog
  POST /api/services/stremio/search_catalog
  POST /api/services/stremio/get_recommendations
  POST /api/services/stremio/get_series_metadata
  POST /api/services/stremio/get_upcoming_episodes
  POST /api/services/stremio/add_to_library
  POST /api/services/stremio/remove_from_library
  POST /api/services/stremio/refresh_library
  POST /api/services/stremio/get_addons

STREAM RESOLUTION:
  POST /api/services/stremio/get_streams     ← returns streams[] w/ playable flag

PLAYBACK (the only Stremio play action):
  POST /api/services/stremio/play_stream     ← resolves + dispatches + tracks
  POST /api/services/stremio/handover_to_apple_tv  ← DEPRECATED alias

TRANSPORT CONTROL (standard HA, no Stremio code):
  POST /api/services/media_player/media_pause
  POST /api/services/media_player/media_play
  POST /api/services/media_player/media_stop
  POST /api/services/media_player/media_seek
  POST /api/services/media_player/volume_set
```

## Components

### Files modified

| File | Change | LOC delta |
|---|---|---|
| `__init__.py` | Drop Apple-TV setup branch. Add `async_migrate_entry` to silently strip Apple-TV options from existing config entries. Register `stremio.get_library` service. | +30 / -20 |
| `const.py` | Remove `CONF_APPLE_TV_*`, `CONF_ENABLE_APPLE_TV_HANDOVER`, `CONF_HANDOVER_METHOD`, `HANDOVER_METHOD_*`. Add `SERVICE_GET_LIBRARY`. | -30 / +1 |
| `coordinator.py` | Remove Apple-TV attributes. Add helper `get_library_items(media_type, skip, limit)` for the new service. | -10 / +20 |
| `services.py` | Remove `handle_handover_to_apple_tv`. Add compat alias: `stremio.handover_to_apple_tv` calls `handle_play_stream` after mapping `device_name` → `entity_id`. Add `handle_get_library`. | -60 / +50 |
| `services.yaml` | Remove handover service block. Add `get_library`. Mark deprecated entries in descriptions. | -70 / +30 |
| `media_player.py` | Remove `async_play_media`, `async_browse_media`, all `_resolve_and_build_media_info` helpers, Apple-TV imports. Keep status-display logic (current playback, position, poster). | -200 / 0 |
| `media_source.py` | `async_resolve_media` calls `stream_resolver.resolve_stream_url`. Calls `coordinator.register_pending_session` for Flow B progress sync. | +30 / -10 |
| `config_flow.py` | Remove Apple-TV schema fields, validation, options-flow steps. Keep `torrent_server_url` + `progress_sync_enabled` (from v1). | -150 / 0 |
| `progress_sync.py` | Add pending-session correlation: register a pending session by URL, match on incoming state_changed `media_content_id`, GC after TTL. | +40 / 0 |
| `manifest.json` | Remove `pyatv>=0.16.0` from `requirements`. Bump `version` to `0.7.0`. | -1 / 0 |
| `frontend/stremio-card-bundle.js` | Remove `import './stremio-stream-dialog.js'` and the corresponding `customCards.push` entry. | -5 / 0 |
| `frontend/stremio-media-details-card.js` | Remove the picker `show()` call. Remove the "View Streams" button entirely — users browse and play via the HA media browser or Zentiahome, not via this card. | -20 / 0 |
| `dashboard_helper.py` | Strip Apple-TV-specific config from the testing dashboard. | -20 / 0 |

### Files deleted

| File | LOC removed |
|---|---|
| `apple_tv_handover.py` | ~990 |
| `frontend/stremio-stream-dialog.js` | ~810 |
| Apple-TV-related tests in `test_services.py` (handover tests) | ~80 |
| Apple-TV-options tests in `test_config_flow.py` | ~80 |

### Files added

| File | Purpose | LOC |
|---|---|---|
| `tests/test_media_player_status_only.py` | Verify `media_player.stremio` raises `ServiceValidationError` on play_media; verify state attributes still populate. | ~80 |
| `tests/test_media_source_resolves_infohash.py` | End-to-end: `async_resolve_media` for an infoHash-only stream with torrent server configured. | ~60 |
| `tests/test_config_migration.py` | `async_migrate_entry` strips Apple-TV options without breaking the entry. | ~50 |
| `tests/test_get_library_service.py` | Tests for the new service (filtering, pagination). | ~70 |
| `tests/test_handover_compat_shim.py` | Verify deprecated service dispatches via play_stream and emits a single warning. | ~40 |

### Files preserved as-is

- `stream_resolver.py`, `playback_manager.py` — v1 modules untouched
- `progress_sync.py` PlaybackSession registry + throttled writes — preserved (only the registration trigger is extended)
- `stremio_client.py` — unchanged (cloud-API layer is independent of device routing)
- Status/display Lovelace cards (library, browse, continue-watching, recommendations, media-details, player) — preserved

**Net LOC delta:** approximately **-1,900** (large deletion, modest additions). The integration's playback-related code mass roughly halves.

## Error handling

**Stream unplayable** (existing) — Stream has no URL AND no usable infoHash → `ServiceValidationError` with translation_key `stream_unplayable`. Same as v1.

**Device not playable** (existing) — Entity doesn't exist / isn't `media_player.*` / is unavailable → `ServiceValidationError` from PlaybackManager. Same as v1.

**`media_player.stremio` invoked for play_media** (new in v2) — Legacy automation calls `media_player.play_media` on `media_player.stremio`. Override `async_play_media` to raise `ServiceValidationError` with translation_key `stremio_entity_not_a_player` and a message naming the right alternative ("use `stremio.play_stream` or play directly to your device entity").

**HA media browser hits an infoHash-only stream** (new in v2) — `async_resolve_media` calls `stream_resolver`. With torrent server configured → returns a real URL. Without → raises `Unresolvable("This Stremio stream requires a torrent server. Install the Stremio Server companion add-on, or configure Real-Debrid in your Torrentio addon URL.")`.

**Config migration failure** (new in v2) — `async_migrate_entry` runs in a try/except that logs WARNING and returns True (best-effort). Apple-TV-orphaned options remain in `entry.options` doing nothing. Integration loads cleanly either way.

**Deprecated handover service called** — Compat shim works, emits one INFO log per HA start: `"stremio.handover_to_apple_tv is deprecated; use stremio.play_stream instead. Will be removed in a future version."` Throttled so it doesn't spam.

**Principles** (unchanged from v1):
- Fail fast with translated, actionable messages
- WARNING for recoverable; ERROR for true failures
- No silent fallbacks that mask the real problem

## Testing

Same harness as v1 (`pytest-homeassistant-custom-component`).

**For deletions:** test files for removed code are deleted alongside the source. No orphan tests.

**New test files** (covered in Components → Files added).

**Updated test files:**
- `test_services.py` — remove handover tests; ensure existing `play_stream` / `get_streams` tests still pass
- `test_config_flow.py` — remove Apple-TV options tests; ensure torrent_server_url + progress_sync_enabled tests still pass
- `test_coordinator.py` — verify `get_library_items` helper

**Coverage target:** maintain ≥80% baseline. New paths at 90%+.

**Manual end-to-end smoke** (deferred to release verification):
1. HA media browser → click any Stremio item → "Send to device" picker → pick a Chromecast/Apple TV → plays
2. Same with an infoHash-only Torrentio stream + torrent server running → plays
3. Zentiahome calling `stremio.play_stream` end-to-end → plays
4. Existing automation calling `handover_to_apple_tv` still works (compat shim)
5. Existing user upgrading from v1 → entry auto-migrates → no errors → media browser flow works

## Migration story

`async_migrate_entry` runs on integration load when the config-entry `version` is older than 2:

1. Strip keys from `entry.data` and `entry.options`:
   - `CONF_ENABLE_APPLE_TV_HANDOVER`, `CONF_APPLE_TV_ENTITY_ID`, `CONF_APPLE_TV_CREDENTIALS`, `CONF_APPLE_TV_IDENTIFIER`, `CONF_HANDOVER_METHOD`, `CONF_APPLE_TV_DEVICE`
2. Bump `entry.version` to 2.
3. Log INFO: `"Migrated Stremio config entry to v2 (Apple-TV-specific options removed)."`

**User experience:**
- Existing user upgrades → restart HA → integration loads cleanly. Media browser flow now works as it should have all along.
- Existing automations using `stremio.handover_to_apple_tv` still work via compat shim. One deprecation log per HA start.
- Existing dashboards using display cards keep working. Dashboards using `stremio-stream-dialog` (probably none in the wild) lose that one card type. Documented in CHANGELOG.

## Out of scope for v2

- Rebuilding the picker dialog with a different architecture (we're removing it)
- Multi-device cast groups
- Codec heuristics on the play path
- Auto-stream-selection based on user preference (still a v3 candidate)
- A dedicated Zentiahome-specific HTTP API (Zentiahome uses standard HA APIs only)
- Series binge-mode autoplay
- Subtitle sync

## Estimated scope

| Workstream | Effort |
|---|---|
| Remove `apple_tv_handover.py` + pyatv + Apple-TV imports across the codebase | 1 day |
| Strip Apple-TV config-flow options + tests | 1 day |
| `media_player.stremio` → status-only + ServiceValidationError on play_media + tests | 1 day |
| `media_source.async_resolve_media` uses stream_resolver + Option X session registration + tests | 1-2 days |
| Auto-migrate config entries (`async_migrate_entry`) + tests | 0.5 days |
| Compat shim for `handover_to_apple_tv` + deprecation logging + tests | 0.5 days |
| New `stremio.get_library` service + tests | 0.5 days |
| Remove picker dialog files + update bundle + media-details-card | 0.5 days |
| Strip Apple-TV from `dashboard_helper.py` | 0.25 days |
| End-to-end smoke testing (real Pi + Chromecast + media browser) | 1 day |
| Bump manifest, update CHANGELOG, README updates | 0.25 days |
| **Total** | **~7-9 days of focused work** |
