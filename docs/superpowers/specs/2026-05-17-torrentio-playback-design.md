# Torrentio playback to HA media devices + progress sync

**Status:** Approved design, ready for implementation planning
**Date:** 2026-05-17
**Author:** Brainstormed with Claude (Opus 4.7)
**Target repos:**
- HACS integration: `https://github.com/Kirill23/hacs-stremio.git`
- Companion add-on: `https://github.com/Kirill23/stremio-link-conversion.git`

---

## Problem

The current integration can browse a user's Stremio library and surface stream entries from installed addons (including Torrentio). But pressing "play" fails with *"no supported format found"* — because Torrentio returns either:

1. A direct HTTPS URL (only when a debrid service like Real-Debrid is configured in the Torrentio addon manifest URL), or
2. A torrent `infoHash` + tracker list (essentially a magnet) — which no Chromecast or smart TV can play directly.

The current `services.handle_handover_to_apple_tv` grabs `streams[0]["url"]` and pipes it to `media_player.play_media`. When the chosen stream is infoHash-only, that URL is missing/invalid, and consumer media devices reject it.

We want to:

1. Play Torrentio content reliably on any HA media_player entity (primary target: Chromecast; smart TVs and others on a best-effort basis).
2. Make the integration consumable by Zentiahome (a separate app sitting on top of HA's REST/WebSocket APIs) without any Zentiahome-specific code in the integration.
3. Sync playback progress back to Stremio's "continue watching" state, matching the behavior of Stremio's own mobile apps.

## Locked-in decisions

| Decision | Choice | Rationale |
|---|---|---|
| Stream source — primary | Debrid (Real-Debrid / AllDebrid / Premiumize) configured in the user's Torrentio addon URL | Torrentio with debrid returns clean HTTPS URLs that Chromecast and smart TVs play natively; no infrastructure to host. |
| Stream source — fallback | External `stremio/server` instance | Same component Stremio's desktop app uses internally; well-known HTTP API; codec detection and transcoding for Chromecast-incompatible source formats. |
| Where the torrent server runs | Separate process — never embedded in HA | A torrent client inside HA's Python process would saturate network, burn CPU on a Pi, wear SD cards, and block HA's event loop. |
| How users install the torrent server | Companion HA Add-on (separate repo) | One-click install for HAOS/Supervisor users; bare-Docker users run `stremio/server` themselves and paste the URL into integration options. |
| Zentiahome API surface | Standard HA REST + WebSocket APIs only | Zentiahome is just another HA client. No Zentiahome-specific code, no custom HTTP views needed in the integration. |
| v1 feature set | Library/catalog browsing + play to Chromecast + play to any media_player + progress sync to Stremio | All four are coherent in scope and don't require speculative abstraction. |
| Stream selection UX | Caller (Lovelace card / Zentiahome) shows a picker every time | The integration does not auto-select. It returns the full list with playable flags; the UI lets the user choose. Clean separation, no opinionated defaults to fight. |
| Implementation approach | Evolutionary — extend existing modules, introduce three new ones for genuinely new functionality | Approach 1 in the brainstorming. Lower risk to the working Apple TV path; fits the existing single-coordinator architecture. |

## Architecture

Two deliverables, deliberately separate:

### The HACS integration (this repo, extended)

Lives entirely in HA's Python process. Holds **zero** torrenting logic. Knows how to:

- Browse Stremio library and catalogs (already works today).
- Take a chosen stream URL and play it on any HA `media_player` entity (new).
- If a stream lacks a direct URL, ask a configured `stremio/server` to translate `infoHash` → HTTP URL (new, thin).
- Sync playback progress back to Stremio's datastore (new).

The existing `StremioDataUpdateCoordinator` remains the single source of truth for polled data. New playback features attach to it (sessions registered on `play_stream`; the coordinator listens for media_player state changes only for registered entities). **No new background polling loops are added.**

The existing `apple_tv_handover.HandoverManager` and its `stremio.handover_to_apple_tv` service stay. Its inner `media_player.play_media` call is delegated to the new `PlaybackManager`. The user-facing service signature is unchanged.

### The companion HA Add-on (`Kirill23/stremio-link-conversion`)

A thin Docker wrapper around the official `stremio-server` Node.js application. The add-on consists of:

```
stremio-link-conversion/
├── repository.yaml                  # Tells HA add-on store about this repo
└── stremio-server/
    ├── config.yaml                  # Add-on manifest (port 11470, options)
    ├── Dockerfile                   # Multi-arch: amd64, arm64, armv7
    ├── run.sh                       # Boots stremio-server with HA-friendly defaults
    ├── README.md
    ├── icon.png
    └── logo.png
```

The Dockerfile builds from HA's official add-on base image (which provides multi-arch support including aarch64 for Raspberry Pi), installs Node.js, installs the `stremio-server` package, and configures the cache directory to live on the add-on's persistent volume so torrent cache survives container restarts.

For users on Home Assistant OS or Supervisor: one click in the add-on store, container starts, port 11470 exposed on the HA host. The integration auto-detects it. Zero performance impact on HA's own runtime (separate container, separate resource limits).

For users on bare-Docker HA installs: they run upstream `stremio/server` themselves and paste the URL into the integration's options. Documented in setup docs.

## Components

### New modules in the HACS integration

| Module | Responsibility | Approx LOC |
|---|---|---|
| `stream_resolver.py` | Pure function: given a Stremio stream dict, return a playable HTTPS URL — either the direct URL it has, or one constructed from its `infoHash` against the configured torrent server. Raises `StreamUnplayableError` if neither path works. No state, no side effects beyond an optional HEAD check. | ~80 |
| `playback_manager.py` | Generalizes `apple_tv_handover`'s `media_player.play_media` call to work on any entity. Builds the call with correct `media_content_type`, `media_content_id`, and `extra` (poster, title, metadata). Fire-and-forget — does not verify playback succeeded. | ~150 |
| `progress_sync.py` | Tracks active playback sessions per `media_player` entity in an in-memory registry. Subscribes to state-change events only for registered entities. Throttled (~30s) writes to Stremio's datastore while playing; immediate flush on pause; marks watched at >90% of duration. Unregisters silently when the entity plays content we didn't start. | ~250 |

### Extended files

- **`services.py`**
  - New service `stremio.play_stream(stream_url, entity_id, media_id, media_type, season?, episode?)`. Resolves URL via `stream_resolver`, calls `PlaybackManager.play`, registers a `ProgressSync` session.
  - Existing `stremio.get_streams` response shape gets one new field per stream: `playable: bool` (true if the stream has a direct URL, or has an `infoHash` AND a torrent server is configured).
  - Existing `stremio.handover_to_apple_tv` becomes a thin compatibility wrapper that internally calls the new `play_stream` flow.

- **`stremio_client.py`**
  - New method `async_update_library_progress(media_id, media_type, position_seconds, duration_seconds)` writing a `lastWatched` entry via `datastorePut`. Payload shape must match what Stremio's mobile apps write so the Stremio web/mobile apps pick up the updates.

- **`coordinator.py`**
  - Instantiates and owns the `ProgressSyncManager`.
  - Exposes `register_playback_session(entity_id, ...)` and `unregister_playback_session(entity_id)` for services to call.

- **`config_flow.py`** (options flow)
  - New option `torrent_server_url` (text, defaults to empty). When the user opens the options flow for the first time (or any time the field is empty), the integration probes `http://homeassistant.local:11470` and `http://127.0.0.1:11470` with a short timeout; if a `stremio/server` responds, the field is pre-filled. Probing does not happen at integration install time — only when the user is actively configuring options — so it has no impact on HA startup.
  - New option `progress_sync_enabled` (boolean, default `true`).

- **`const.py`**
  - `SERVICE_PLAY_STREAM = "play_stream"`
  - `CONF_TORRENT_SERVER_URL = "torrent_server_url"`
  - `CONF_PROGRESS_SYNC_ENABLED = "progress_sync_enabled"`
  - `STREMIO_SERVER_DEFAULT_PORT = 11470`
  - `PROGRESS_SYNC_INTERVAL_SECONDS = 30`
  - `WATCHED_THRESHOLD = 0.9`

- **`frontend/stremio-stream-dialog.js`** (existing picker)
  - "Cached" badge for streams Torrentio marks as debrid-cached.
  - "Needs torrent server" indicator for streams where `playable: false`.
  - Device dropdown populated from `media_player.*` entities.
  - "Play" button calls `stremio.play_stream` with the selection (replaces the current "open URL in browser" behavior).

- **`manifest.json`**, **`README.md`**
  - Update `documentation`, `issue_tracker`, `codeowners` and README badges to point at `Kirill23/hacs-stremio`.

### Deliberately untouched in v1

- `apple_tv_handover.py` — its Apple-TV-specific logic (AirPlay launch, VLC fallback, pyatv-based device discovery) stays. Only its inner `media_player.play_media` call is replaced by a `PlaybackManager.play` call.
- `media_player.py`, `sensor.py`, `binary_sensor.py`, `button.py`, `media_source.py` — no changes. Library/catalog browsing already works through these.

## Data flow

### Browse flow

Largely unchanged from today. Zentiahome (or HA's media browser, or a Lovelace card) calls HA's standard APIs:

```
Zentiahome
   │  GET /api/states/sensor.stremio_library_count
   │  WebSocket subscribe state_changed
   │  POST /api/services/stremio/search_library     (existing service)
   │  POST /api/services/stremio/browse_catalog     (existing service)
   ▼
Home Assistant
   ▼
StremioDataUpdateCoordinator  (cached, refreshed per scan_interval)
   ▼
StremioClient                 (only if cache miss)
   ▼
Stremio API (api.strem.io)
```

No new infrastructure needed.

### Play + progress sync flow

```
1. User in Zentiahome selects "Inception" → device "Living Room Chromecast".
   Zentiahome calls: POST /api/services/stremio/get_streams
                     { media_id: "tt1375666", media_type: "movie" }

2. HA → StremioClient.async_get_streams() queries all installed addons.
   Returns list of stream dicts, each now annotated with `playable: true|false`.

3. Zentiahome renders its picker (filename, quality, size, cached badge, playable
   flag). User picks a stream.

4. Zentiahome calls: POST /api/services/stremio/play_stream
                     { stream_url: "<chosen>",
                       entity_id: "media_player.living_room",
                       media_id: "tt1375666",
                       media_type: "movie" }

5. HA → services.handle_play_stream:
     a. stream_resolver.resolve_stream_url(stream, torrent_server_url)
          → returns HTTPS URL directly, OR constructs
            "http://<torrent_server>/stream/<infoHash>/..." URL
            (fails fast with clear error if neither path works).
     b. playback_manager.play(entity_id, url, media_info)
          → service call: media_player.play_media on the Chromecast.
     c. coordinator.register_playback_session(entity_id, media_id, ...).

6. Chromecast starts playing.

7. ProgressSyncManager (already subscribed to state_changed for registered
   entities) sees the Chromecast enter "playing" state. Every PROGRESS_SYNC_INTERVAL_SECONDS
   it reads media_position, calls StremioClient.async_update_library_progress()
   → datastorePut to Stremio. On "paused" → immediate final write. On "idle"
   with position > 90% of duration → mark watched. On entity playing different
   content (user switched manually) → unregister the session.
```

### Why the boundaries fall here

- **The picker lives in the caller.** The integration exposes data + an execute service. Zentiahome and the Lovelace card both render their own pickers using the same data. No picker UI ships in the integration's Python code.
- **The stream resolver is pure.** No HA state, no async I/O beyond an optional HEAD check. Easy to test exhaustively.
- **The playback manager is fire-and-forget.** It doesn't try to verify playback succeeded — that's the progress sync's job (it has the state_changed feed anyway). This avoids duplicating logic.
- **Progress sync is event-driven, not polled.** It reuses HA's state machine, which is already updated by the Chromecast (or other) integration polling the device. Zero new polling loops.

### Lovelace card (HA-native users)

The existing `frontend/stremio-stream-dialog.js` already implements roughly steps 1-3 of the play flow. We extend it with the device dropdown (step 4 input) and switch the "Play" action from "open URL in browser" to "call `stremio.play_stream`." No new card needed for v1.

## Error handling

**Stream resolution fails** (the current bug we're explicitly fixing)
- Stream has no `url` AND no usable `infoHash`+server combo → `StreamUnplayableError` from `stream_resolver`. Surfaced as `ServiceValidationError` with a translated, actionable message naming both remediation paths (configure Real-Debrid in the Torrentio addon URL, or install the Stremio Server companion add-on) and linking to the integration's setup docs page. The actual docs URL is populated during implementation once the docs page exists in the new repo. No silent fallback to "play anyway and let the TV fail with a cryptic error."

**Target device problems**
- Entity doesn't exist or is `unavailable` → `ServiceValidationError` before any work happens. Message names the specific entity_id.
- `media_player.play_media` accepted but device can't decode the format → not directly detectable. We don't try. Within seconds the progress sync sees the entity go to `idle` and unregisters cleanly. User retries with a different stream. (Codec heuristics are a v2 candidate.)

**Torrent server problems**
- `torrent_server_url` configured but unreachable → `StreamUnplayableError` with a message pointing at the add-on (not the integration). Don't retry inline (would block the service call).
- Server reachable but returns 5xx for the infoHash → same.

**Stremio API failures during progress sync**
- `datastorePut` raises → log warning, keep session active, retry on next 30s tick. Don't crash the sync manager — losing one tick is acceptable; losing the whole sync isn't.
- Auth expired → existing `StremioClient` reauth flow handles it. The first failing write triggers reauth; subsequent writes succeed.

**User behavior we handle gracefully**
- User stops via TV remote → entity goes `idle`, sync writes final position, unregisters. No error.
- User casts something else to the same device → state_changed with a different `media_content_id`. Sync detects mismatch and unregisters silently. We don't write progress for content we didn't start.
- HA restart mid-playback → in-memory session registry is lost. Acceptable: debrid stream URLs typically expire within hours, so persisting them wouldn't help. Progress already written to Stremio is preserved; only the last <30s is lost.

**Multi-entry edge case**
- Two Stremio config entries (two accounts) share the same media_player pool. If account A starts a play, then account B starts a different play on the same Chromecast, A's sync sees the content mismatch and unregisters; B's sync owns it. Documented behavior.

**Principles**
- Fail fast with translated, actionable messages — never let the user see "no supported format found" again with no path forward.
- WARNING for recoverable issues (sync retry). ERROR only for true failures.
- No silent fallbacks that mask the real problem.

## Testing

**New test files:**

- `tests/test_stream_resolver.py` — pure unit tests. URL-only stream returns the URL. infoHash + server returns the constructed server URL. Stream with neither, or infoHash with no server, raises `StreamUnplayableError`. ~6-8 tests, fast, no HA fixtures.
- `tests/test_playback_manager.py` — verifies the right `media_player.play_media` call is built (`content_type`, `content_id`, `extra` metadata). Verifies entity validation errors. Uses HA's mock service registry.
- `tests/test_progress_sync.py` — the most substantive. Covers: session register → state→playing → throttled (no immediate write); after `PROGRESS_SYNC_INTERVAL_SECONDS` → write happens; state→paused → immediate flush; state→idle with position >90% → "watched" write; state with mismatched `media_content_id` → session unregistered; sync write failure → session preserved, retries next tick. ~12-15 tests.

**Extended test files:**

- `tests/test_services.py` — adds `play_stream` tests (signature validation, orchestration, error mapping) and a regression test confirming `handover_to_apple_tv` still works via the compat shim.
- `tests/test_stremio_client.py` — adds `async_update_library_progress` payload-shape test. The body must match what Stremio's mobile apps write, otherwise Stremio's web/mobile apps won't pick up our updates.
- `tests/conftest.py` — adds `MOCK_TORRENT_SERVER_URL`, a `mock_chromecast_entity` fixture, an `aioresponses`-based fake torrent server, and mock stream dicts in both shapes (URL-only and infoHash-only).

**One light end-to-end test:** `get_streams → play_stream → progress sync writes`, with all external calls mocked. Verifies orchestration without depending on real devices or APIs.

**Deliberately not tested:**

- The companion add-on itself. It's a Dockerfile wrapping upstream `stremio-server`; we trust upstream. The add-on README documents a manual smoke test (`curl localhost:11470` returns 200).
- Real Chromecast playback. No real cast device in CI; HA's state-change utilities are the substitute.

**Coverage:** maintain existing ≥80% baseline.

**CI changes folded in:**

- `.github/workflows/test.yml` is currently `workflow_dispatch` only (per `CLAUDE.md`). For the new `Kirill23/hacs-stremio` repo, switch to `push` + `pull_request` triggers so regressions are caught automatically.
- The add-on repo gets its own minimal CI: multi-arch Docker build (linux/amd64, linux/arm64, linux/arm/v7) on tag pushes.

## Out of scope for v1

The following are explicitly deferred:

- Codec-mismatch detection (auto-skipping to a Chromecast-compatible stream when the first one fails to play).
- Subtitle sync (downloading and attaching subs from Stremio's subtitle addons).
- Series binge-mode (auto-playing the next episode when the current one ends).
- Stream auto-selection based on user history ("you usually prefer 1080p WEB-DL").
- Cast-group / multi-room sync.
- A v2 of the companion add-on with built-in debrid integration (so users get a fast path without paying for an external debrid service).

These are real follow-up candidates and should be tracked, but they would each add at least a week of work and aren't required for the v1 use case.

## Estimated scope

| Workstream | Effort |
|---|---|
| `stream_resolver.py` + tests | 1-2 days |
| `playback_manager.py` + generalizing apple_tv_handover delegation + tests | 3-4 days |
| `progress_sync.py` + tests | 4-6 days |
| `services.py` — new `play_stream`, extend `get_streams` response, compat shim, tests | 2-3 days |
| `stremio_client.async_update_library_progress` + tests | 1-2 days |
| `config_flow.py` options + auto-detection + tests | 1-2 days |
| Frontend `stremio-stream-dialog.js` extensions | 2-3 days |
| Repo rebranding (`Kirill23/hacs-stremio`) — manifest, README, CI | 1 day |
| Companion add-on: Dockerfile, config, run.sh, README, multi-arch CI | 3-5 days |
| End-to-end smoke testing on a real Pi + Chromecast | 2-3 days |
| **Total** | **~3-4 weeks of focused work** |
