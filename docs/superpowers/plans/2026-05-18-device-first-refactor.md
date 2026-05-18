# v2: Device-first refactor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the Apple-TV-first code paths from the integration so all interaction surfaces (HA media browser, Zentiahome, custom display cards) route playback through one device-agnostic pipeline. Add a clean `stremio.get_library` service and silently migrate existing config entries.

**Architecture:** Almost entirely deletion — `apple_tv_handover.py` (~990 LOC), `pyatv` dependency, the picker dialog (~810 LOC), and Apple-TV config options all go away. `media_player.stremio` becomes a status-only entity. `media_source.async_resolve_media` and `ProgressSyncManager` get small additions to make the HA media browser flow work end-to-end with the existing v1 stream_resolver + PlaybackManager pipeline.

**Tech Stack:** Python 3.12+ async, aiohttp, Home Assistant 2025.1+, `pytest-homeassistant-custom-component`, Lit (frontend).

**Spec:** `docs/superpowers/specs/2026-05-18-device-first-refactor-design.md`

**Branches:** This v2 work continues on the same `feature/torrentio-playback` branch the v1 commits land on (the worktree at `.worktrees/torrentio-playback`). New commits go on top of `497cc73` (last v1-era commit). The branch will eventually fast-forward to master.

---

## Conventions used in this plan

- **TDD throughout** — failing test first, run to confirm, implement, run to confirm pass, commit.
- **Atomic commits per task**, Conventional Commits format (`feat:`, `fix:`, `refactor:`, `chore:`, `test:`, `docs:`).
- **Exact file paths** — every step names the file. Approximate line numbers; rely on the surrounding code shown.
- **Run tests** with `pytest <file>::<test> -v` for single tests, `./scripts/run_tests.sh --quick` for the suite (skips lint), `./scripts/run_tests.sh` for full check.
- **Lint**: Black at line-length 88 (per `.pre-commit-config.yaml`); flake8 at 120 (ignore E501, W503). Format before commit.
- **Known baseline noise**: `test_coordinator_fetch_data_auth_failure` is a pre-existing failure on Python 3.13. Treat as expected; do not try to fix.
- **`CHANGELOG.md`** is updated in the final task — don't touch it per-task.

---

## Task 1: Config entry auto-migration

Lay the foundation: when v2 loads against a v1-or-pre-fork config entry, silently strip the Apple-TV-specific options. Done first so subsequent tasks can remove the constants those options reference without breaking existing installs.

**Files:**
- Modify: `custom_components/stremio/__init__.py`
- Create: `tests/test_config_migration.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_config_migration.py`:

```python
"""Tests for config entry auto-migration from v1 (Apple-TV-era) to v2."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.stremio import async_migrate_entry
from custom_components.stremio.const import DOMAIN


@pytest.mark.asyncio
async def test_migrate_strips_apple_tv_options(hass) -> None:
    """v1 config entry with Apple-TV options gets cleaned to v2 shape."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={
            "email": "user@example.com",
            "password": "hash",
            "enable_apple_tv_handover": True,
            "apple_tv_entity_id": "media_player.apple_tv",
            "apple_tv_credentials": "...",
            "apple_tv_identifier": "AABBCC",
            "handover_method": "airplay",
            "apple_tv_device": "Living Room",
        },
        options={
            "player_scan_interval": 30,
            "torrent_server_url": "http://localhost:11470",
            "enable_apple_tv_handover": True,
            "handover_method": "airplay",
        },
    )
    entry.add_to_hass(hass)

    result = await async_migrate_entry(hass, entry)

    assert result is True
    assert entry.version == 2
    # Apple-TV keys are gone
    for key in [
        "enable_apple_tv_handover",
        "apple_tv_entity_id",
        "apple_tv_credentials",
        "apple_tv_identifier",
        "handover_method",
        "apple_tv_device",
    ]:
        assert key not in entry.data
        assert key not in entry.options
    # Non-Apple-TV settings preserved
    assert entry.data["email"] == "user@example.com"
    assert entry.options["player_scan_interval"] == 30
    assert entry.options["torrent_server_url"] == "http://localhost:11470"


@pytest.mark.asyncio
async def test_migrate_idempotent_on_v2_entry(hass) -> None:
    """Calling migrate on an already-v2 entry is a no-op."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={"email": "user@example.com", "password": "hash"},
        options={"torrent_server_url": "http://localhost:11470"},
    )
    entry.add_to_hass(hass)

    result = await async_migrate_entry(hass, entry)

    assert result is True
    assert entry.version == 2
    assert entry.options["torrent_server_url"] == "http://localhost:11470"


@pytest.mark.asyncio
async def test_migrate_handles_entry_with_no_apple_tv_keys(hass) -> None:
    """Fresh v1 entry without Apple-TV opts still bumps version cleanly."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={"email": "user@example.com", "password": "hash"},
        options={"player_scan_interval": 30},
    )
    entry.add_to_hass(hass)

    result = await async_migrate_entry(hass, entry)

    assert result is True
    assert entry.version == 2
```

- [ ] **Step 2: Run to confirm failure**

```bash
source .venv/bin/activate
pytest tests/test_config_migration.py -v
```

Expected: `ImportError: cannot import name 'async_migrate_entry' from 'custom_components.stremio'`

- [ ] **Step 3: Implement `async_migrate_entry` in `__init__.py`**

Open `custom_components/stremio/__init__.py`. After the existing import block at the top, add (only if not already present):

```python
from homeassistant.config_entries import ConfigEntry
```

After the existing `async_unload_entry` function (or alongside `async_reload_entry`), add the migration function:

```python
# Apple-TV-era config keys that are removed in v2. Listed as literal strings
# (not imported from const.py) because the constants themselves are removed
# in a later task — the migration must keep working without them.
_APPLE_TV_LEGACY_KEYS: tuple[str, ...] = (
    "enable_apple_tv_handover",
    "apple_tv_entity_id",
    "apple_tv_credentials",
    "apple_tv_identifier",
    "handover_method",
    "apple_tv_device",
)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate older config entries to the v2 schema.

    v1 (and pre-fork) entries carry Apple-TV-specific options that v2 no
    longer reads. Strip them so users don't see surprising options remembered
    after upgrade, and bump the entry version so HA records that the
    migration ran. Idempotent — running on a v2 entry is a no-op.

    Args:
        hass: Home Assistant instance
        entry: Config entry to migrate

    Returns:
        True (migration is best-effort; orphan keys are harmless if present).
    """
    if entry.version >= 2:
        return True

    _LOGGER.info(
        "Migrating Stremio config entry %s to v2 (removing Apple-TV-specific options)",
        entry.entry_id,
    )

    new_data = {k: v for k, v in entry.data.items() if k not in _APPLE_TV_LEGACY_KEYS}
    new_options = {
        k: v for k, v in entry.options.items() if k not in _APPLE_TV_LEGACY_KEYS
    }

    hass.config_entries.async_update_entry(
        entry, data=new_data, options=new_options, version=2
    )
    return True
```

Note: the migration deliberately uses string literals for the legacy keys instead of importing the constants. The constants will be removed in Task 10 — the migration must keep working after that removal.

- [ ] **Step 4: Run to confirm passes**

```bash
pytest tests/test_config_migration.py -v
```

Expected: all 3 tests pass.

- [ ] **Step 5: Verify no whole-suite regression**

```bash
pytest tests/ -q 2>&1 | tail -5
```

Expected: same pass count as before (modulo the pre-existing `test_coordinator_fetch_data_auth_failure`).

- [ ] **Step 6: Format + commit**

```bash
black custom_components/stremio/__init__.py tests/test_config_migration.py
git add custom_components/stremio/__init__.py tests/test_config_migration.py
git commit -m "feat(migration): auto-migrate v1 Apple-TV options out of config entries

Existing v1 (and pre-fork) installations carry Apple-TV-specific options
that v2 stops reading. async_migrate_entry runs at HA load time and
silently strips those keys from entry.data/entry.options, then bumps the
entry version to 2 so HA records the migration ran. Idempotent — running
on a v2 entry is a no-op. Migration uses string literals for the legacy
keys rather than importing the constants, because the constants
themselves are removed in a later task and the migration must keep
working after that removal."
```

---

## Task 2: `media_player.stremio` becomes status-only

Replace the Apple-TV-only `async_play_media` with a `ServiceValidationError` raised with a clear, translated message. Remove the related Apple-TV helpers and imports. The entity continues to expose current-playback state attributes; it just stops claiming to be a play target.

This is the task that **immediately fixes the "click play in media browser, nothing happens" bug**. After this task lands, the same click raises a clear error in the HA UI instead of silently failing.

**Files:**
- Modify: `custom_components/stremio/media_player.py`
- Modify: `custom_components/stremio/strings.json`
- Modify: `custom_components/stremio/translations/en.json` (if present)
- Create: `tests/test_media_player_status_only.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_media_player_status_only.py`:

```python
"""media_player.stremio is status-only in v2 — no play_media support."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.exceptions import ServiceValidationError


@pytest.mark.asyncio
async def test_play_media_raises_service_validation_error() -> None:
    """media_player.stremio rejects play_media with a translated error."""
    from custom_components.stremio.media_player import StremioMediaPlayer

    coordinator = MagicMock()
    coordinator.data = {}
    entry = MagicMock()
    entry.options = {}
    entry.entry_id = "abc"

    player = StremioMediaPlayer(coordinator, entry)

    with pytest.raises(ServiceValidationError) as exc_info:
        await player.async_play_media(
            media_type="video",
            media_id="media-source://stremio/movie/tt001#0",
        )

    err = exc_info.value
    assert err.translation_key == "stremio_entity_not_a_player"
    assert err.translation_domain == "stremio"


@pytest.mark.asyncio
async def test_no_async_browse_media_method() -> None:
    """async_browse_media is removed in v2; HA will fall back to media_source."""
    from custom_components.stremio.media_player import StremioMediaPlayer

    coordinator = MagicMock()
    coordinator.data = {}
    entry = MagicMock()
    entry.options = {}

    player = StremioMediaPlayer(coordinator, entry)
    # Either the method does not exist, or it raises NotImplementedError.
    # Both are acceptable signals to HA that browsing happens via
    # media_source, not via this entity.
    assert not hasattr(player, "async_browse_media") or (
        callable(getattr(player, "async_browse_media"))
        and (
            await _raises_not_implemented(
                lambda: player.async_browse_media(media_content_id=None)
            )
        )
    )


async def _raises_not_implemented(coro_factory) -> bool:
    try:
        await coro_factory()
    except NotImplementedError:
        return True
    except Exception:
        return False
    return False
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_media_player_status_only.py -v
```

Expected: First test fails because the existing `async_play_media` returns silently when Apple TV is not configured (no exception raised).

- [ ] **Step 3: Replace `async_play_media` in `media_player.py`**

Open `custom_components/stremio/media_player.py`. Find the existing `async_play_media` method (starts around line 304). Replace its entire body with a `ServiceValidationError` raise:

```python
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
```

Make sure these imports exist at the top of `media_player.py`:

```python
from homeassistant.exceptions import ServiceValidationError
from .const import DOMAIN
```

(Both should already be there from existing code. Add only if missing.)

- [ ] **Step 4: Remove `async_browse_media` and its helpers**

In the same file, find and **delete**:
- The entire `async_browse_media` method (around line 280-302)
- The `_resolve_and_build_media_info` helper method (typically a private async method called from the old `async_play_media`)
- Any other private helpers that were ONLY called from the removed methods

Also remove now-unused imports at the top of the file. After your edits, run:

```bash
flake8 custom_components/stremio/media_player.py --max-line-length=120 --ignore=E501,W503 | grep "F401"
```

Any F401 (unused import) results are imports you should delete. Common candidates to remove: `from .apple_tv_handover import ...`, anything related to `MediaSource`/`MediaSourceItem`/`PlayMedia` that was only used by `async_browse_media`.

- [ ] **Step 5: Add the new error translation**

Open `custom_components/stremio/strings.json`. In the existing `exceptions` object (added in v1 Task 12), add:

```json
"stremio_entity_not_a_player": {
  "message": "media_player.stremio is a status entity, not a playback target. Use the stremio.play_stream service, or play directly to your device entity in Home Assistant's media browser."
}
```

If `custom_components/stremio/translations/en.json` exists, mirror the same addition there.

Validate JSON:

```bash
python3 -c "import json; json.load(open('custom_components/stremio/strings.json'))"
```

Expected: no output.

- [ ] **Step 6: Run all tests to confirm passes**

```bash
pytest tests/test_media_player_status_only.py -v
```

Expected: both new tests pass.

- [ ] **Step 7: Verify whole-suite regression**

```bash
pytest tests/ -q 2>&1 | tail -8
```

Some pre-existing tests in `test_media_player.py` may now fail because they exercised the removed `async_play_media` Apple-TV path. **Fix those by deleting them** (their target behavior is removed). Acceptable to also remove tests for `async_browse_media`.

Expected after fixups: at most the pre-existing baseline error remains.

- [ ] **Step 8: Format + commit**

```bash
black custom_components/stremio/media_player.py tests/test_media_player_status_only.py
git add custom_components/stremio/media_player.py \
        custom_components/stremio/strings.json \
        custom_components/stremio/translations/en.json \
        tests/test_media_player_status_only.py \
        tests/test_media_player.py
git commit -m "refactor(media_player): make stremio entity status-only

Removes async_play_media and async_browse_media. Playback target
responsibilities now live on real device media_player entities (Chromecast,
Apple TV, smart TV, etc.) reached via stremio.play_stream or HA's native
'send to device' picker in the media browser. The stremio entity continues
to expose current-playback state attributes (title, position, poster) for
dashboards.

Invoking play_media on media_player.stremio now raises ServiceValidationError
with translation_key stremio_entity_not_a_player — a clear, actionable
error instead of the silent return that produced the 'click play, nothing
happens' bug.

Removes tests that exercised the deleted Apple-TV path."
```

---

## Task 3: `media_source.async_resolve_media` uses `stream_resolver`

The HA media browser flow's "no playable stream URL available" error happens because `async_resolve_media` grabs `streams[stream_index].url` directly, which is empty for infoHash-only Torrentio streams. After this task, that same code path uses `stream_resolver.resolve_stream_url` and can produce a torrent-server URL for infoHash streams (when the torrent server is configured).

**Files:**
- Modify: `custom_components/stremio/media_source.py`
- Create: `tests/test_media_source_resolves_infohash.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_media_source_resolves_infohash.py`:

```python
"""media_source.async_resolve_media routes through stream_resolver in v2."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.media_source.error import Unresolvable

from custom_components.stremio.const import CONF_TORRENT_SERVER_URL, DOMAIN
from custom_components.stremio.media_source import StremioMediaSource


def _mock_item(identifier: str) -> MagicMock:
    item = MagicMock()
    item.identifier = identifier
    return item


@pytest.mark.asyncio
async def test_resolves_infohash_via_torrent_server(hass) -> None:
    """A media-source URI pointing at an infoHash-only stream resolves via the configured torrent server."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={"email": "x", "password": "y"},
        options={CONF_TORRENT_SERVER_URL: "http://127.0.0.1:11470"},
    )
    entry.add_to_hass(hass)

    coordinator = MagicMock()
    client = MagicMock()
    client.async_get_streams = AsyncMock(
        return_value=[{"name": "Torrentio P2P", "infoHash": "abc123", "fileIdx": 0}]
    )
    coordinator.client = client
    hass.data[DOMAIN] = {entry.entry_id: {"coordinator": coordinator, "client": client}}

    source = StremioMediaSource(hass)
    result = await source.async_resolve_media(_mock_item("movie/tt001#0"))

    assert result.url == "http://127.0.0.1:11470/abc123/0"


@pytest.mark.asyncio
async def test_resolves_direct_url_unchanged(hass) -> None:
    """A stream with a direct URL is returned as-is."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={"email": "x", "password": "y"},
        options={},
    )
    entry.add_to_hass(hass)

    coordinator = MagicMock()
    client = MagicMock()
    client.async_get_streams = AsyncMock(
        return_value=[{"name": "Real-Debrid", "url": "https://debrid/x.mp4"}]
    )
    coordinator.client = client
    hass.data[DOMAIN] = {entry.entry_id: {"coordinator": coordinator, "client": client}}

    source = StremioMediaSource(hass)
    result = await source.async_resolve_media(_mock_item("movie/tt001#0"))

    assert result.url == "https://debrid/x.mp4"


@pytest.mark.asyncio
async def test_raises_unresolvable_when_no_url_and_no_server(hass) -> None:
    """infoHash-only stream + no torrent server -> Unresolvable with actionable message."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={"email": "x", "password": "y"},
        options={},  # no torrent_server_url
    )
    entry.add_to_hass(hass)

    coordinator = MagicMock()
    client = MagicMock()
    client.async_get_streams = AsyncMock(
        return_value=[{"name": "Torrentio P2P", "infoHash": "abc123"}]
    )
    coordinator.client = client
    hass.data[DOMAIN] = {entry.entry_id: {"coordinator": coordinator, "client": client}}

    source = StremioMediaSource(hass)
    with pytest.raises(Unresolvable) as exc_info:
        await source.async_resolve_media(_mock_item("movie/tt001#0"))

    # User-facing message should point at the remediation paths
    msg = str(exc_info.value).lower()
    assert "stremio server" in msg or "torrent server" in msg or "debrid" in msg
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_media_source_resolves_infohash.py -v
```

Expected: first test fails — the existing code returns `streams[0]["url"]` which is undefined for infoHash-only streams.

- [ ] **Step 3: Update `async_resolve_media` in `media_source.py`**

Open `custom_components/stremio/media_source.py`. Add the import at the top alongside other `.` imports:

```python
from .stream_resolver import StreamUnplayableError, resolve_stream_url
from .const import CONF_TORRENT_SERVER_URL
```

Find the existing `async_resolve_media` method (around line 63). Locate the part that currently extracts the URL from `streams[stream_index]` (somewhere after `streams = await client.async_get_streams(...)`). Replace the direct-URL extraction with a call to `resolve_stream_url`. The relevant block becomes:

```python
# Pick the requested stream by index
if stream_index is None:
    stream_index = 0
if stream_index >= len(streams):
    raise Unresolvable(
        f"Stream index {stream_index} out of range (only {len(streams)} streams)"
    )
stream = streams[stream_index]

# Look up the configured torrent server URL from the integration's
# config entry options (None / empty means "no torrent server").
torrent_server_url: str | None = None
for entry in self.hass.config_entries.async_entries(DOMAIN):
    torrent_server_url = entry.options.get(CONF_TORRENT_SERVER_URL) or None
    if torrent_server_url:
        break

try:
    resolved_url = resolve_stream_url(stream, torrent_server_url)
except StreamUnplayableError as err:
    raise Unresolvable(
        f"This Stremio stream cannot be played. Either configure "
        f"Real-Debrid (or another debrid service) in your Torrentio "
        f"addon URL, or install the Stremio Server companion add-on. "
        f"({err})"
    ) from err

mime_type = self._get_mime_type(resolved_url, stream)
return PlayMedia(url=resolved_url, mime_type=mime_type)
```

The exact surrounding code in `media_source.py` may differ; preserve any pre-existing logic for `media_type`, `media_id`, `season`, `episode`, identifier parsing, and the call to `client.async_get_streams`. Only the URL-extraction-and-return tail changes.

- [ ] **Step 4: Run to confirm passes**

```bash
pytest tests/test_media_source_resolves_infohash.py -v
```

Expected: all 3 tests pass.

- [ ] **Step 5: Whole-suite regression check**

```bash
pytest tests/ -q 2>&1 | tail -5
```

Expected: at most the pre-existing baseline error.

- [ ] **Step 6: Format + commit**

```bash
black custom_components/stremio/media_source.py tests/test_media_source_resolves_infohash.py
git add custom_components/stremio/media_source.py tests/test_media_source_resolves_infohash.py
git commit -m "feat(media_source): resolve streams via stream_resolver

async_resolve_media used to grab streams[stream_index].url directly,
which is empty for infoHash-only Torrentio streams — the HA media browser
flow then surfaced 'no playable stream URL available'. Now it delegates
to the same stream_resolver the play_stream service uses, so the media
browser flow works end-to-end for infoHash streams whenever the torrent
server URL is configured.

When the stream truly cannot be resolved (no URL, no infoHash, or
infoHash with no torrent server), raises Unresolvable with a message
that points at the two remediation paths."
```

---

## Task 4: `ProgressSyncManager` pending-session correlation (Option X)

Right now, ProgressSync only writes Stremio progress for plays initiated by `stremio.play_stream`. The HA media browser flow bypasses that service — it calls `media_source.async_resolve_media`, gets a URL back, then HA dispatches `media_player.play_media` directly on the chosen device. ProgressSync never sees a "register session" call for that play.

This task adds **pending sessions**: when `async_resolve_media` is about to return a URL, it tells the coordinator "expect a play of this URL soon." When `_handle_state_change` fires on any media_player entering "playing" with a matching `media_content_id`, the pending session graduates to a regular registered session. Pending sessions GC after 60s if no play arrives.

**Files:**
- Modify: `custom_components/stremio/progress_sync.py`
- Modify: `custom_components/stremio/const.py`
- Modify: `tests/test_progress_sync.py`
- Create: `tests/test_progress_sync_pending_sessions.py`

- [ ] **Step 1: Add the TTL constant**

Open `custom_components/stremio/const.py`. After `PROGRESS_SYNC_INTERVAL_SECONDS`, add:

```python
PENDING_SESSION_TTL_SECONDS: Final = 60
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_progress_sync_pending_sessions.py`:

```python
"""Tests for ProgressSyncManager pending-session correlation (Option X)."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.stremio.progress_sync import (
    PendingSession,
    PlaybackSession,
    ProgressSyncManager,
)


@pytest.fixture
def mock_hass() -> HomeAssistant:
    hass = MagicMock(spec=HomeAssistant)
    hass.bus = MagicMock()
    hass.bus.async_listen = MagicMock(return_value=lambda: None)
    return hass


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.async_update_library_progress = AsyncMock(return_value=None)
    return client


def _make_state(state, media_content_id=None):
    s = MagicMock()
    s.state = state
    s.attributes = {}
    if media_content_id is not None:
        s.attributes["media_content_id"] = media_content_id
    return s


def _state_event(entity_id, new_state):
    e = MagicMock()
    e.data = {"entity_id": entity_id, "new_state": new_state}
    return e


def test_register_pending_session_records_url(mock_hass, mock_client) -> None:
    mgr = ProgressSyncManager(mock_hass, mock_client)
    mgr.register_pending_session(
        media_id="tt001",
        media_type="movie",
        media_content_id="https://debrid/x.mp4",
    )
    pending = mgr.get_pending_session("https://debrid/x.mp4")
    assert isinstance(pending, PendingSession)
    assert pending.media_id == "tt001"


async def test_state_change_graduates_pending_to_active_session(
    mock_hass, mock_client
) -> None:
    """When a media_player enters playing with a pending URL, register a real session."""
    mgr = ProgressSyncManager(mock_hass, mock_client)
    mgr.register_pending_session(
        media_id="tt001",
        media_type="movie",
        media_content_id="https://debrid/x.mp4",
    )

    state = _make_state("playing", media_content_id="https://debrid/x.mp4")
    await mgr._handle_state_change(_state_event("media_player.tv", state))

    session = mgr.get_session("media_player.tv")
    assert isinstance(session, PlaybackSession)
    assert session.media_id == "tt001"
    assert session.media_content_id == "https://debrid/x.mp4"
    # And the pending entry is consumed
    assert mgr.get_pending_session("https://debrid/x.mp4") is None


async def test_state_change_with_unmatched_url_does_not_graduate(
    mock_hass, mock_client
) -> None:
    """Random state changes don't accidentally register sessions."""
    mgr = ProgressSyncManager(mock_hass, mock_client)
    state = _make_state("playing", media_content_id="https://youtube.com/x")
    await mgr._handle_state_change(_state_event("media_player.tv", state))

    assert mgr.get_session("media_player.tv") is None


def test_pending_session_gc_removes_expired_entries(mock_hass, mock_client) -> None:
    from custom_components.stremio.const import PENDING_SESSION_TTL_SECONDS

    mgr = ProgressSyncManager(mock_hass, mock_client)
    mgr.register_pending_session(
        media_id="tt001",
        media_type="movie",
        media_content_id="https://debrid/x.mp4",
    )
    # Force the pending session to look ancient.
    mgr._pending["https://debrid/x.mp4"].created_at = (
        time.monotonic() - PENDING_SESSION_TTL_SECONDS - 1
    )
    mgr._gc_pending()
    assert mgr.get_pending_session("https://debrid/x.mp4") is None


def test_pending_session_gc_keeps_fresh_entries(mock_hass, mock_client) -> None:
    mgr = ProgressSyncManager(mock_hass, mock_client)
    mgr.register_pending_session(
        media_id="tt001",
        media_type="movie",
        media_content_id="https://debrid/x.mp4",
    )
    mgr._gc_pending()
    assert mgr.get_pending_session("https://debrid/x.mp4") is not None
```

- [ ] **Step 3: Run to confirm failure**

```bash
pytest tests/test_progress_sync_pending_sessions.py -v
```

Expected: `ImportError: cannot import name 'PendingSession'` (the class doesn't exist yet).

- [ ] **Step 4: Extend `progress_sync.py`**

Open `custom_components/stremio/progress_sync.py`. Update the imports at the top:

```python
from .const import (
    PENDING_SESSION_TTL_SECONDS,
    PROGRESS_SYNC_INTERVAL_SECONDS,
    WATCHED_THRESHOLD,
)
```

After the existing `PlaybackSession` dataclass, add:

```python
@dataclass
class PendingSession:
    """A session that's been resolved but not yet observed playing.

    Created when ``media_source.async_resolve_media`` is about to return a
    URL to HA's framework. When a ``state_changed`` event arrives showing
    any media_player entity playing that URL, the pending session
    graduates to a regular PlaybackSession on that entity. GC'd after
    PENDING_SESSION_TTL_SECONDS if no play arrives.
    """

    media_id: str
    media_type: str
    media_content_id: str
    created_at: float = field(default_factory=time.monotonic)
```

In `ProgressSyncManager.__init__`, after `self._sessions = {}`:

```python
self._pending: dict[str, PendingSession] = {}
```

Add the new public methods to `ProgressSyncManager`:

```python
def register_pending_session(
    self,
    media_id: str,
    media_type: str,
    media_content_id: str,
) -> None:
    """Mark a URL as awaiting playback (Option X cross-flow tracking).

    Called from ``media_source.async_resolve_media`` before returning a
    URL. When any media_player entity starts playing that URL, the
    listener will register a real session.
    """
    self._gc_pending()  # opportunistic cleanup
    self._pending[media_content_id] = PendingSession(
        media_id=media_id,
        media_type=media_type,
        media_content_id=media_content_id,
    )
    _LOGGER.debug(
        "Registered pending session: url=%s media=%s (%s)",
        media_content_id,
        media_id,
        media_type,
    )

def get_pending_session(self, media_content_id: str) -> PendingSession | None:
    return self._pending.get(media_content_id)

def _gc_pending(self) -> None:
    """Drop pending sessions older than PENDING_SESSION_TTL_SECONDS."""
    now = time.monotonic()
    expired = [
        url
        for url, p in self._pending.items()
        if now - p.created_at > PENDING_SESSION_TTL_SECONDS
    ]
    for url in expired:
        del self._pending[url]
```

Update `_handle_state_change`. At the top of the method, after extracting `entity_id` and `new_state`, but BEFORE the existing `if entity_id not in self._sessions: return` check, add this graduation block:

```python
attrs = getattr(new_state, "attributes", {}) or {}
content_id = attrs.get("media_content_id")

# Graduate a pending session if this state_change matches a URL we
# resolved via media_source but never explicitly registered.
if (
    content_id
    and entity_id not in self._sessions
    and content_id in self._pending
):
    pending = self._pending.pop(content_id)
    self.register_session(
        entity_id=entity_id,
        media_id=pending.media_id,
        media_type=pending.media_type,
        media_content_id=pending.media_content_id,
    )
    _LOGGER.debug(
        "Graduated pending session to active: entity=%s url=%s",
        entity_id,
        content_id,
    )
```

(Don't duplicate the `attrs = ...` line if it already appears further down. If the existing code already reads `attrs` later, hoist it to the top once.)

After that block, the existing `if entity_id not in self._sessions: return` and the rest of the method continue unchanged.

- [ ] **Step 5: Run to confirm passes**

```bash
pytest tests/test_progress_sync.py tests/test_progress_sync_pending_sessions.py -v
```

Expected: original 11 + 4 new pending-session tests, all pass.

- [ ] **Step 6: Format + commit**

```bash
black custom_components/stremio/progress_sync.py custom_components/stremio/const.py tests/test_progress_sync_pending_sessions.py
git add custom_components/stremio/progress_sync.py \
        custom_components/stremio/const.py \
        tests/test_progress_sync_pending_sessions.py
git commit -m "feat(progress_sync): pending-session URL correlation (Option X)

ProgressSync previously tracked only sessions explicitly registered via
stremio.play_stream. The HA media browser flow bypasses that service:
HA dispatches play_media directly to a device with a URL it got from
media_source.async_resolve_media. Add a pending-session registry keyed
by URL: when any media_player enters 'playing' with a URL matching a
pending entry, graduate it to a real session so continue-watching
progress flows for media-browser-initiated plays too.

Pending entries GC after PENDING_SESSION_TTL_SECONDS (60s) if no
matching play arrives. Next task wires the registration call into
async_resolve_media."
```

---

## Task 5: Wire `media_source` to register pending sessions

Tiny addition to the v1 `async_resolve_media` change from Task 3: call `coordinator.register_pending_session` just before returning the `PlayMedia`. With Task 4's correlation logic in place, this is what makes Flow B's progress sync work end-to-end.

**Files:**
- Modify: `custom_components/stremio/media_source.py`
- Modify: `custom_components/stremio/coordinator.py`
- Modify: `tests/test_media_source_resolves_infohash.py`

- [ ] **Step 1: Add a coordinator method that delegates to progress_sync**

Open `custom_components/stremio/coordinator.py`. After the existing `register_playback_session` method (added in v1 Task 9), add:

```python
def register_pending_session(
    self,
    media_id: str,
    media_type: str,
    media_content_id: str,
) -> None:
    """Mark a URL as awaiting playback (Option X — Flow B progress sync)."""
    self.progress_sync.register_pending_session(
        media_id=media_id,
        media_type=media_type,
        media_content_id=media_content_id,
    )
```

- [ ] **Step 2: Add the failing test**

Open `tests/test_media_source_resolves_infohash.py`. Add:

```python
@pytest.mark.asyncio
async def test_resolve_registers_pending_session(hass) -> None:
    """async_resolve_media notifies coordinator to expect playback of the resolved URL."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={"email": "x", "password": "y"},
        options={CONF_TORRENT_SERVER_URL: "http://127.0.0.1:11470"},
    )
    entry.add_to_hass(hass)

    coordinator = MagicMock()
    coordinator.register_pending_session = MagicMock()
    client = MagicMock()
    client.async_get_streams = AsyncMock(
        return_value=[{"name": "T", "infoHash": "abc123"}]
    )
    coordinator.client = client
    hass.data[DOMAIN] = {entry.entry_id: {"coordinator": coordinator, "client": client}}

    source = StremioMediaSource(hass)
    await source.async_resolve_media(_mock_item("movie/tt001#0"))

    coordinator.register_pending_session.assert_called_once_with(
        media_id="tt001",
        media_type="movie",
        media_content_id="http://127.0.0.1:11470/abc123/0",
    )
```

- [ ] **Step 3: Run to confirm failure**

```bash
pytest tests/test_media_source_resolves_infohash.py::test_resolve_registers_pending_session -v
```

Expected: `AssertionError: Expected mock to have been called once. Called 0 times.`

- [ ] **Step 4: Wire the registration into `async_resolve_media`**

Open `custom_components/stremio/media_source.py`. Find the `async_resolve_media` block from Task 3 where we just computed `resolved_url`. Just BEFORE the `return PlayMedia(...)` line, add:

```python
# Register a pending session so ProgressSyncManager will pick up
# whichever media_player ends up playing this URL (Option X).
try:
    coordinator.register_pending_session(
        media_id=media_id,
        media_type=media_type,
        media_content_id=resolved_url,
    )
except Exception:  # noqa: BLE001 — never fail playback resolution for telemetry
    _LOGGER.exception("Failed to register pending session; playback continues")
```

The `coordinator` variable should already be in scope from earlier in the method. If the variable name differs in the actual code, adapt.

- [ ] **Step 5: Run to confirm passes**

```bash
pytest tests/test_media_source_resolves_infohash.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 6: Format + commit**

```bash
black custom_components/stremio/media_source.py custom_components/stremio/coordinator.py
git add custom_components/stremio/media_source.py \
        custom_components/stremio/coordinator.py \
        tests/test_media_source_resolves_infohash.py
git commit -m "feat(media_source): register pending session before returning URL

Wires Task 4's pending-session correlation: media_source now calls
coordinator.register_pending_session right before returning the resolved
URL to HA. When the chosen device starts playing that URL, ProgressSync
sees the match and starts tracking — so continue-watching syncs to
Stremio for the HA-media-browser flow exactly like it already does for
the stremio.play_stream flow."
```

---

## Task 6: `stremio.get_library` service

A clean, paginated, type-filtered way for Zentiahome to read the user's full Stremio library. Today the only way is `stremio.search_library` with `query=""` (which works by accident — empty-substring matches everything). This service makes the contract explicit.

**Files:**
- Modify: `custom_components/stremio/const.py`
- Modify: `custom_components/stremio/services.py`
- Modify: `custom_components/stremio/services.yaml`
- Modify: `custom_components/stremio/coordinator.py`
- Create: `tests/test_get_library_service.py`

- [ ] **Step 1: Add constants**

Open `custom_components/stremio/const.py`. In the existing `SERVICE_*` block, add:

```python
SERVICE_GET_LIBRARY: Final = "get_library"
```

Also add field attribute constants if not already present (alongside other `ATTR_*`):

```python
ATTR_SKIP: Final = "skip"
ATTR_LIMIT: Final = "limit"
```

(`ATTR_SKIP` may already exist from v1 `browse_catalog`. Don't duplicate.)

- [ ] **Step 2: Add coordinator helper method**

Open `custom_components/stremio/coordinator.py`. Add:

```python
def get_library_items(
    self,
    media_type: str = "all",
    skip: int = 0,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return library items filtered + paginated.

    Args:
        media_type: One of "movie", "series", or "all".
        skip: Number of items to skip (for pagination).
        limit: Max items to return.

    Returns:
        List of library item dicts. Empty if coordinator has no data yet.
    """
    items: list[dict[str, Any]] = list(self.data.get("library", []) or [])
    if media_type != "all":
        items = [i for i in items if i.get("type") == media_type]
    return items[skip : skip + limit]
```

- [ ] **Step 3: Write failing tests**

Create `tests/test_get_library_service.py`:

```python
"""Tests for stremio.get_library service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.stremio.const import DOMAIN
from custom_components.stremio.services import async_setup_services


def _make_entry(hass: HomeAssistant, library: list[dict]) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={"email": "x", "password": "y"},
        options={},
        entry_id="test_lib",
    )
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.get_library_items = MagicMock(
        side_effect=lambda media_type, skip, limit: [
            i for i in library if media_type == "all" or i.get("type") == media_type
        ][skip : skip + limit]
    )
    client = AsyncMock()
    hass.data[DOMAIN] = {entry.entry_id: {"coordinator": coordinator, "client": client}}
    return entry


@pytest.mark.asyncio
async def test_get_library_returns_all_items_by_default(hass) -> None:
    library = [
        {"imdb_id": "tt001", "type": "movie", "title": "A"},
        {"imdb_id": "tt002", "type": "series", "title": "B"},
        {"imdb_id": "tt003", "type": "movie", "title": "C"},
    ]
    _make_entry(hass, library)
    await async_setup_services(hass)

    result = await hass.services.async_call(
        DOMAIN,
        "get_library",
        {},
        blocking=True,
        return_response=True,
    )

    assert result["count"] == 3
    assert {i["imdb_id"] for i in result["items"]} == {"tt001", "tt002", "tt003"}


@pytest.mark.asyncio
async def test_get_library_filters_by_type(hass) -> None:
    library = [
        {"imdb_id": "tt001", "type": "movie", "title": "A"},
        {"imdb_id": "tt002", "type": "series", "title": "B"},
        {"imdb_id": "tt003", "type": "movie", "title": "C"},
    ]
    _make_entry(hass, library)
    await async_setup_services(hass)

    result = await hass.services.async_call(
        DOMAIN,
        "get_library",
        {"type": "movie"},
        blocking=True,
        return_response=True,
    )

    assert result["count"] == 2
    assert all(i["type"] == "movie" for i in result["items"])


@pytest.mark.asyncio
async def test_get_library_paginates(hass) -> None:
    library = [
        {"imdb_id": f"tt{i:03d}", "type": "movie", "title": f"M{i}"} for i in range(25)
    ]
    _make_entry(hass, library)
    await async_setup_services(hass)

    page1 = await hass.services.async_call(
        DOMAIN,
        "get_library",
        {"skip": 0, "limit": 10},
        blocking=True,
        return_response=True,
    )
    page2 = await hass.services.async_call(
        DOMAIN,
        "get_library",
        {"skip": 10, "limit": 10},
        blocking=True,
        return_response=True,
    )

    assert page1["count"] == 10
    assert page2["count"] == 10
    assert page1["items"][0]["imdb_id"] != page2["items"][0]["imdb_id"]
```

- [ ] **Step 4: Run to confirm failure**

```bash
pytest tests/test_get_library_service.py -v
```

Expected: `ServiceNotFound: Service stremio.get_library not found`.

- [ ] **Step 5: Add the service handler in `services.py`**

Open `custom_components/stremio/services.py`. Update imports:

```python
from .const import (
    # ... existing imports ...
    SERVICE_GET_LIBRARY,
)
```

Define the schema near other `vol.Schema` definitions:

```python
GET_LIBRARY_SCHEMA = vol.Schema(
    {
        vol.Optional("type", default="all"): vol.In(["movie", "series", "all"]),
        vol.Optional(ATTR_SKIP, default=0): cv.positive_int,
        vol.Optional(ATTR_LIMIT, default=100): cv.positive_int,
    }
)
```

Add the handler near other `handle_*` functions:

```python
async def handle_get_library(call: ServiceCall) -> ServiceResponse:  # type: ignore[return-value]
    """Return the user's Stremio library (paginated, optionally filtered)."""
    coordinator, _client, _entry_id = _get_entry_data(hass)
    items = coordinator.get_library_items(
        media_type=call.data["type"],
        skip=call.data[ATTR_SKIP],
        limit=call.data[ATTR_LIMIT],
    )
    return {"items": items, "count": len(items)}
```

In `async_setup_services`, register the service:

```python
hass.services.async_register(
    DOMAIN,
    SERVICE_GET_LIBRARY,
    handle_get_library,
    schema=GET_LIBRARY_SCHEMA,
    supports_response=SupportsResponse.ONLY,
)
```

And in `async_unload_services`:

```python
hass.services.async_remove(DOMAIN, SERVICE_GET_LIBRARY)
```

- [ ] **Step 6: Add the service to `services.yaml`**

Open `custom_components/stremio/services.yaml`. Add at the bottom:

```yaml
get_library:
  name: Get library
  description: >
    Return the user's Stremio library, optionally filtered by content type
    and paginated. Designed for external clients (e.g. Zentiahome) to
    discover what's in the library without inferring it from search.
  fields:
    type:
      name: Content type
      description: Filter to movies, series, or both.
      required: false
      default: "all"
      example: "movie"
      selector:
        select:
          options:
            - all
            - movie
            - series
    skip:
      name: Skip
      description: Pagination offset.
      required: false
      default: 0
      example: 0
      selector:
        number:
          min: 0
          mode: box
    limit:
      name: Limit
      description: Maximum items to return.
      required: false
      default: 100
      example: 100
      selector:
        number:
          min: 1
          max: 1000
          mode: box
```

- [ ] **Step 7: Run to confirm passes**

```bash
pytest tests/test_get_library_service.py -v
```

Expected: 3 tests pass.

- [ ] **Step 8: Format + commit**

```bash
black custom_components/stremio/const.py \
      custom_components/stremio/coordinator.py \
      custom_components/stremio/services.py \
      tests/test_get_library_service.py
git add custom_components/stremio/const.py \
        custom_components/stremio/coordinator.py \
        custom_components/stremio/services.py \
        custom_components/stremio/services.yaml \
        tests/test_get_library_service.py
git commit -m "feat(services): add stremio.get_library

Clean, explicit, paginated library access for external clients
(Zentiahome). Avoids the implicit empty-query-matches-everything trick
required to coax full library out of stremio.search_library today.
Type filter accepts 'movie', 'series', or 'all'. Skip/limit pagination."
```

---

## Task 7: Compat shim for `stremio.handover_to_apple_tv`

Existing user automations call `stremio.handover_to_apple_tv`. We deprecate the service rather than break those automations. The compat shim calls `play_stream` internally and emits a single deprecation warning per HA start.

**Files:**
- Modify: `custom_components/stremio/services.py`
- Modify: `custom_components/stremio/services.yaml`
- Create: `tests/test_handover_compat_shim.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_handover_compat_shim.py`:

```python
"""Tests for the deprecated stremio.handover_to_apple_tv compat shim."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.stremio.const import DOMAIN
from custom_components.stremio.services import async_setup_services


def _setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={"email": "x", "password": "y"},
        options={},
        entry_id="test_handover",
    )
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.register_playback_session = MagicMock()
    client = AsyncMock()
    hass.data[DOMAIN] = {entry.entry_id: {"coordinator": coordinator, "client": client}}
    return entry


@pytest.mark.asyncio
async def test_handover_to_apple_tv_dispatches_via_play_stream(hass) -> None:
    """The compat shim plays via the same pipeline as stremio.play_stream."""
    _setup_entry(hass)
    hass.states.async_set("media_player.apple_tv", "idle")
    await async_setup_services(hass)

    with patch("custom_components.stremio.services.PlaybackManager") as mock_pm_class:
        mock_pm = MagicMock()
        mock_pm.play = AsyncMock(return_value=None)
        mock_pm_class.return_value = mock_pm

        await hass.services.async_call(
            DOMAIN,
            "handover_to_apple_tv",
            {
                "stream_url": "https://debrid/x.mp4",
                "entity_id": "media_player.apple_tv",
                "media_id": "tt001",
                "media_type": "movie",
            },
            blocking=True,
        )

        mock_pm.play.assert_awaited_once()


@pytest.mark.asyncio
async def test_handover_to_apple_tv_logs_deprecation_warning_once(
    hass, caplog: pytest.LogCaptureFixture
) -> None:
    """Deprecation warning emits exactly once per HA start, not per call."""
    from custom_components.stremio import services as services_module

    _setup_entry(hass)
    hass.states.async_set("media_player.apple_tv", "idle")
    await async_setup_services(hass)
    # Reset the module-level "warned" flag so the test sees the first warning
    services_module._HANDOVER_DEPRECATION_WARNED = False

    with patch("custom_components.stremio.services.PlaybackManager") as mock_pm_class:
        mock_pm = MagicMock()
        mock_pm.play = AsyncMock(return_value=None)
        mock_pm_class.return_value = mock_pm

        with caplog.at_level(logging.WARNING):
            await hass.services.async_call(
                DOMAIN,
                "handover_to_apple_tv",
                {
                    "stream_url": "https://debrid/x.mp4",
                    "entity_id": "media_player.apple_tv",
                    "media_id": "tt001",
                    "media_type": "movie",
                },
                blocking=True,
            )
            await hass.services.async_call(
                DOMAIN,
                "handover_to_apple_tv",
                {
                    "stream_url": "https://debrid/x.mp4",
                    "entity_id": "media_player.apple_tv",
                    "media_id": "tt001",
                    "media_type": "movie",
                },
                blocking=True,
            )

    deprecation_warnings = [
        r for r in caplog.records if "deprecated" in r.message.lower()
    ]
    assert len(deprecation_warnings) == 1
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_handover_compat_shim.py -v
```

Expected: first test fails (or the existing handler runs and behaves differently from `play_stream`).

- [ ] **Step 3: Replace `handle_handover_to_apple_tv`**

Open `custom_components/stremio/services.py`. Find the existing `handle_handover_to_apple_tv` function (probably around the other handle_* functions; may import from `apple_tv_handover.HandoverManager`).

**Delete the existing implementation entirely**, including any related `HANDOVER_SCHEMA`. Replace with the compat shim:

At the module top level (alongside other module-level state), add:

```python
_HANDOVER_DEPRECATION_WARNED = False
```

Define the new schema (which mirrors `PLAY_STREAM_SCHEMA`):

```python
HANDOVER_COMPAT_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_STREAM_URL, default=""): cv.string,
        vol.Optional(ATTR_INFO_HASH, default=""): cv.string,
        vol.Optional(ATTR_FILE_IDX, default=0): cv.positive_int,
        vol.Required("entity_id"): cv.entity_id,
        vol.Required(ATTR_MEDIA_ID): cv.string,
        vol.Required(ATTR_MEDIA_TYPE): vol.In(["movie", "series"]),
        vol.Optional(ATTR_SEASON): cv.positive_int,
        vol.Optional(ATTR_EPISODE): cv.positive_int,
    }
)
```

Inside `async_setup_services`, define the handler **as a closure** so it can reach `hass` and the already-defined `handle_play_stream`:

```python
async def handle_handover_to_apple_tv(call: ServiceCall) -> None:
    """Deprecated compat shim — delegates to stremio.play_stream.

    Maintains backward compatibility for user automations from the
    Apple-TV-era of the integration. Logs a one-time deprecation warning
    per HA start.
    """
    global _HANDOVER_DEPRECATION_WARNED
    if not _HANDOVER_DEPRECATION_WARNED:
        _LOGGER.warning(
            "stremio.handover_to_apple_tv is deprecated; use "
            "stremio.play_stream instead. Will be removed in a future "
            "version."
        )
        _HANDOVER_DEPRECATION_WARNED = True
    await handle_play_stream(call)
```

Replace the existing `hass.services.async_register(DOMAIN, SERVICE_HANDOVER_TO_APPLE_TV, ..., schema=...)` call to use the new schema:

```python
hass.services.async_register(
    DOMAIN,
    SERVICE_HANDOVER_TO_APPLE_TV,
    handle_handover_to_apple_tv,
    schema=HANDOVER_COMPAT_SCHEMA,
)
```

(`SERVICE_HANDOVER_TO_APPLE_TV` already exists in `const.py` from before.)

The unregistration in `async_unload_services` already exists; leave it.

- [ ] **Step 4: Remove the import of `HandoverManager`**

At the top of `services.py`, remove:

```python
from .apple_tv_handover import HandoverError, HandoverManager
```

The module will be deleted in Task 8; we drop the import now so this commit is self-contained.

- [ ] **Step 5: Run to confirm passes**

```bash
pytest tests/test_handover_compat_shim.py -v
```

Expected: 2 tests pass.

- [ ] **Step 6: Update `services.yaml` to mark the service deprecated**

Open `custom_components/stremio/services.yaml`. Find the existing `handover_to_apple_tv:` block. Replace its description with:

```yaml
handover_to_apple_tv:
  name: Handover to Apple TV (DEPRECATED)
  description: >
    DEPRECATED — use stremio.play_stream instead. This service is now a
    thin compatibility shim that calls play_stream internally. Will be
    removed in a future version.
  fields:
    stream_url:
      name: Stream URL
      description: Direct HTTP(S) stream URL.
      required: false
      example: "https://debrid.example.com/movie.mp4"
      selector:
        text:
    info_hash:
      name: Torrent info hash
      description: BitTorrent infoHash for torrent-only streams.
      required: false
      example: "abc123..."
      selector:
        text:
    file_idx:
      name: Torrent file index
      description: File index inside the torrent (defaults to 0).
      required: false
      default: 0
      example: 0
      selector:
        number:
          min: 0
          max: 999
          mode: box
    entity_id:
      name: Target media player
      description: HA media_player entity to play to.
      required: true
      example: "media_player.apple_tv_living_room"
      selector:
        entity:
          domain: media_player
    media_id:
      name: Media ID
      description: IMDb-style ID.
      required: true
      example: "tt1375666"
      selector:
        text:
    media_type:
      name: Media type
      description: movie or series.
      required: true
      example: "movie"
      selector:
        select:
          options:
            - movie
            - series
```

(Existing block may have different fields like `device_name`, `method`, etc. — replace entirely with the schema above. The compat shim only honors the modern fields.)

- [ ] **Step 7: Whole-suite regression check**

```bash
pytest tests/ -q 2>&1 | tail -8
```

Some existing tests in `test_services.py` that exercised the old handover handler will now fail. Delete those tests (they test removed behavior). Tests that exercise the new compat shim through play_stream still work.

- [ ] **Step 8: Format + commit**

```bash
black custom_components/stremio/services.py tests/test_handover_compat_shim.py
git add custom_components/stremio/services.py \
        custom_components/stremio/services.yaml \
        tests/test_handover_compat_shim.py \
        tests/test_services.py
git commit -m "refactor(services): deprecate handover_to_apple_tv as play_stream shim

The Apple-TV-specific service is replaced by a thin closure inside
async_setup_services that delegates to handle_play_stream. Schema
matches the modern play_stream surface (stream_url + info_hash +
file_idx + entity_id + media_id + media_type). One INFO-level
deprecation warning per HA start; the call still works so existing
user automations don't break.

services.yaml marks the service DEPRECATED in its description so the
HA Developer Tools UI shows it clearly. Drops the import of
HandoverManager from apple_tv_handover.py — the module itself is
removed in the next task."
```

---

## Task 8: Delete `apple_tv_handover.py` + remove `pyatv`

The module has no remaining consumers after Task 7. Delete it. Also drop `pyatv` from `manifest.json` since the only user of it was `apple_tv_handover.py`.

**Files:**
- Delete: `custom_components/stremio/apple_tv_handover.py`
- Modify: `custom_components/stremio/manifest.json`
- Modify: `custom_components/stremio/__init__.py` (drop import if present)

- [ ] **Step 1: Confirm no remaining importers**

```bash
grep -rn "apple_tv_handover\|HandoverManager\|HandoverError\|HandoverMethod\|StreamFormat\b" \
    custom_components/stremio/ tests/ \
    --include="*.py" 2>&1 | grep -v "^Binary" | head
```

Expected: no results (other than possibly the file itself, which we're about to delete). If anything shows up, fix the importing file before deleting.

- [ ] **Step 2: Delete the file**

```bash
git rm custom_components/stremio/apple_tv_handover.py
```

- [ ] **Step 3: Remove `pyatv` from `manifest.json`**

Open `custom_components/stremio/manifest.json`. Change:

```json
"requirements": [
  "pyatv>=0.16.0"
]
```

to:

```json
"requirements": []
```

(Keep the empty list — HACS validation expects the key to be present.)

- [ ] **Step 4: Verify HA still loads the integration**

```bash
source .venv/bin/activate
python -c "from custom_components.stremio import __init__"
```

Expected: no errors. If there's an ImportError for `apple_tv_handover`, find the offending file and remove the import.

- [ ] **Step 5: Whole-suite regression check**

```bash
pytest tests/ -q 2>&1 | tail -5
```

Expected: same as baseline. If a test now fails due to a missing module, find and delete the offending test.

- [ ] **Step 6: Commit**

```bash
git add custom_components/stremio/manifest.json
git commit -m "chore: delete apple_tv_handover.py + drop pyatv dependency

The 990-line module that did pyatv-based AirPlay discovery and
URL-scheme handoff has no remaining consumers after the
handover_to_apple_tv service was reduced to a play_stream shim.
HA's stock apple_tv integration handles AirPlay natively for
Apple TV media_player entities. Removes pyatv>=0.16.0 from the
integration's requirements."
```

---

## Task 9: Strip Apple-TV options from `config_flow.py`

Remove Apple-TV-related fields from the options flow. Existing entries are already migrated to drop these (Task 1); now we drop them from the schema so newly-opened options dialogs don't show them.

**Files:**
- Modify: `custom_components/stremio/config_flow.py`
- Modify: `tests/test_config_flow.py`

- [ ] **Step 1: Audit the current schema**

```bash
grep -n "apple_tv\|handover_method\|enable_apple_tv_handover\|CONF_APPLE_TV" custom_components/stremio/config_flow.py | head -20
```

Note every place that references Apple-TV config. The constants list to remove from the schema:

- `CONF_ENABLE_APPLE_TV_HANDOVER`
- `CONF_HANDOVER_METHOD`
- `CONF_APPLE_TV_DEVICE`
- `CONF_APPLE_TV_ENTITY_ID`
- `CONF_APPLE_TV_CREDENTIALS`
- `CONF_APPLE_TV_IDENTIFIER`

- [ ] **Step 2: Remove from the OptionsFlow schema**

Open `custom_components/stremio/config_flow.py`. In the options-flow step that builds `vol.Schema({...})`, **delete** the lines for each `CONF_APPLE_TV_*` and `CONF_ENABLE_APPLE_TV_HANDOVER` and `CONF_HANDOVER_METHOD` entry.

Preserve all other fields (player_scan_interval, library_scan_interval, polling_gate_entities, addon_stream_order, stream_quality_preference, show_copy_url, default_catalog_source, torrent_server_url, progress_sync_enabled).

If there's a multi-step options flow (e.g., a separate Apple TV step), delete the whole step's method (`async_step_apple_tv` or similar) and remove any conditional branching that routed to it.

- [ ] **Step 3: Remove imports**

At the top of `config_flow.py`, remove imports of the deleted constants. Run flake8 to verify:

```bash
flake8 custom_components/stremio/config_flow.py --max-line-length=120 --ignore=E501,W503 | grep "F401"
```

Delete any reported unused imports.

- [ ] **Step 4: Delete tests for the removed options**

Open `tests/test_config_flow.py`. Find tests with names like:
- `test_options_flow_apple_tv*`
- `test_apple_tv_*`
- `test_options_flow_handover*`

Delete those tests entirely — they test removed behavior. Keep tests for the still-present options (player_scan_interval, torrent_server_url auto-detect, progress_sync_enabled, etc.).

- [ ] **Step 5: Verify the options flow still loads**

```bash
pytest tests/test_config_flow.py -v
```

Expected: all remaining tests pass. If any test imports a removed CONF, that import was missed in Step 4 — fix the test or delete it.

- [ ] **Step 6: Format + commit**

```bash
black custom_components/stremio/config_flow.py tests/test_config_flow.py
git add custom_components/stremio/config_flow.py tests/test_config_flow.py
git commit -m "refactor(config_flow): drop Apple-TV options from options flow

Removes schema fields for CONF_ENABLE_APPLE_TV_HANDOVER, CONF_HANDOVER_METHOD,
CONF_APPLE_TV_DEVICE, CONF_APPLE_TV_ENTITY_ID, CONF_APPLE_TV_CREDENTIALS,
CONF_APPLE_TV_IDENTIFIER. Existing entries that carry these options have
already been silently migrated in Task 1; newly-opened options dialogs no
longer show these fields. Surviving options (torrent_server_url,
progress_sync_enabled, polling gate, addon order, quality preference,
copy URL, default catalog) all preserved."
```

---

## Task 10: Remove Apple-TV constants from `const.py`

After Tasks 7-9, nothing references these constants. Delete them. The migration in Task 1 deliberately used string literals so it keeps working without them.

**Files:**
- Modify: `custom_components/stremio/const.py`

- [ ] **Step 1: Confirm no remaining references**

```bash
grep -rn "CONF_APPLE_TV\|CONF_ENABLE_APPLE_TV_HANDOVER\|CONF_HANDOVER_METHOD\|HANDOVER_METHOD_\|DEFAULT_APPLE_TV\|DEFAULT_ENABLE_APPLE_TV_HANDOVER\|DEFAULT_HANDOVER_METHOD\|HANDOVER_METHODS" \
    custom_components/stremio/ tests/ \
    --include="*.py" 2>&1 | head
```

Expected: only matches inside `const.py` itself (the definitions to delete). Anything else is a missed cleanup — fix that file first before deleting the constant.

- [ ] **Step 2: Delete the constants from `const.py`**

Open `custom_components/stremio/const.py`. Find and **delete** these definitions:

```python
CONF_ENABLE_APPLE_TV_HANDOVER
CONF_HANDOVER_METHOD
CONF_APPLE_TV_DEVICE
CONF_APPLE_TV_ENTITY_ID
CONF_APPLE_TV_CREDENTIALS
CONF_APPLE_TV_IDENTIFIER
DEFAULT_ENABLE_APPLE_TV_HANDOVER
DEFAULT_HANDOVER_METHOD
DEFAULT_APPLE_TV_DEVICE
DEFAULT_APPLE_TV_ENTITY_ID
HANDOVER_METHOD_AUTO
HANDOVER_METHOD_AIRPLAY
HANDOVER_METHOD_VLC
HANDOVER_METHOD_DIRECT
HANDOVER_METHODS
```

(Some may not exist in your branch — delete the ones that do.)

- [ ] **Step 3: Verify the module imports cleanly**

```bash
source .venv/bin/activate
python -c "from custom_components.stremio import const; print('OK')"
```

Expected: `OK`.

- [ ] **Step 4: Whole-suite check**

```bash
pytest tests/ -q 2>&1 | tail -5
```

Expected: baseline only.

- [ ] **Step 5: Format + commit**

```bash
black custom_components/stremio/const.py
git add custom_components/stremio/const.py
git commit -m "chore(const): delete Apple-TV-era constants

All in-code references were removed by Tasks 7-9. The Task 1 migration
uses string literals for these key names so it continues working after
deletion (migrating existing user entries off them remains supported)."
```

---

## Task 11: Remove the picker dialog (frontend)

Delete `frontend/stremio-stream-dialog.js`. Update the bundle to drop the import and the `customCards.push` entry. Update `stremio-media-details-card.js` to remove the "View Streams" button that opened the dialog.

**Files:**
- Delete: `custom_components/stremio/frontend/stremio-stream-dialog.js`
- Modify: `custom_components/stremio/frontend/stremio-card-bundle.js`
- Modify: `custom_components/stremio/frontend/stremio-media-details-card.js`
- Modify: `custom_components/stremio/manifest.json` (bump version)

- [ ] **Step 1: Delete the dialog file**

```bash
git rm custom_components/stremio/frontend/stremio-stream-dialog.js
```

- [ ] **Step 2: Remove the import + customCards entry from the bundle**

Open `custom_components/stremio/frontend/stremio-card-bundle.js`. Delete the line:

```javascript
import './stremio-stream-dialog.js';
```

Find the `window.customCards.push({...})` block for the stream dialog (the one with `type: 'stremio-stream-dialog'`). Delete that whole `customCards.push(...)` call.

Verify the bundle still parses:

```bash
node --check custom_components/stremio/frontend/stremio-card-bundle.js
```

Expected: no output.

- [ ] **Step 3: Remove the View Streams button from media-details-card**

Open `custom_components/stremio/frontend/stremio-media-details-card.js`. Find:
- The button rendered with text like "View Streams" / "Streams" / similar
- The handler that calls `StremioStreamDialog.show(...)` or `_openStreamDialog`
- The `season`/`episode` plumbing added in v1 Task 15

Delete all three: the button HTML in the template, the handler method, and the now-unused state for forwarding season/episode to the dialog.

Verify the file still parses:

```bash
node --check custom_components/stremio/frontend/stremio-media-details-card.js
```

Expected: no output.

- [ ] **Step 4: Bump the manifest version**

Open `custom_components/stremio/manifest.json`. Bump `"version"` from `"0.6.1"` to `"0.7.0"`.

(Bumping the major-minor signals the breaking-ish change of removing the picker, and forces Lovelace to refresh the bundle URL so users don't see stale cached JS.)

- [ ] **Step 5: Verify the whole suite still passes**

```bash
pytest tests/ -q 2>&1 | tail -5
```

Expected: baseline. No Python tests touched the dialog directly (it's JS), so nothing should break.

- [ ] **Step 6: Commit**

```bash
git add custom_components/stremio/frontend/stremio-card-bundle.js \
        custom_components/stremio/frontend/stremio-media-details-card.js \
        custom_components/stremio/manifest.json
git commit -m "refactor(frontend): remove picker dialog (stremio-stream-dialog)

In v2 the Lovelace picker is no longer one of the primary playback
surfaces — Zentiahome and HA's media browser are. The picker was source
of recurring bugs (Lovelace resource cache pinning, infoHash forwarding,
device dropdown initialization races). Delete the file (810 LOC), drop
the import from stremio-card-bundle.js, drop the customCards.push entry,
and remove the 'View Streams' button from stremio-media-details-card.js
that was its primary entry point. The other six cards (library, browse,
continue-watching, recommendations, media-details, player) remain.
Bump manifest to 0.7.0 to cache-bust the bundle."
```

---

## Task 12: Strip Apple-TV config from `dashboard_helper.py`

The auto-generated testing dashboard had Apple-TV-specific blocks. Remove them.

**Files:**
- Modify: `custom_components/stremio/dashboard_helper.py`

- [ ] **Step 1: Find references**

```bash
grep -n "apple_tv\|handover\|Apple TV" custom_components/stremio/dashboard_helper.py
```

Note every block of dashboard YAML/dict that mentions Apple TV.

- [ ] **Step 2: Delete those blocks**

Open `custom_components/stremio/dashboard_helper.py`. For each block found in Step 1:

- If the block is a whole card (`{"type": "...", ...}` that's Apple-TV-specific), delete the dict entry.
- If the block is a sub-field on an otherwise-generic card (e.g., a configuration knob mentioning Apple TV), delete just that field.

Preserve the surrounding structure (rows, columns, layout) and the non-Apple-TV cards.

- [ ] **Step 3: Verify YAML/dict structure is intact**

```bash
source .venv/bin/activate
python -c "from custom_components.stremio import dashboard_helper; print('OK')"
```

Expected: `OK`. If syntax error, fix it.

- [ ] **Step 4: Format + commit**

```bash
black custom_components/stremio/dashboard_helper.py
git add custom_components/stremio/dashboard_helper.py
git commit -m "chore(dashboard): strip Apple-TV-specific blocks from testing dashboard

The auto-generated testing dashboard had per-card configuration that
referenced Apple TV (handover toggles, device-specific layouts). Drop
those blocks. The dashboard's remaining cards (library, browse,
continue-watching, recommendations, media-details, player) work
against any media_player entity."
```

---

## Task 13: Full test suite green + lint clean

Catch any regressions from the cleanup. The point isn't to chase pre-existing lint warnings in files we didn't touch — only to fix new issues from v2 changes.

- [ ] **Step 1: Run the whole suite**

```bash
source .venv/bin/activate
./scripts/run_tests.sh --quick
```

Expected: all tests pass except the known pre-existing `test_coordinator_fetch_data_auth_failure`.

If new failures appear, they're from:
- A v2 task that missed a reference (most likely)
- A test that exercised removed behavior and wasn't cleaned up in its parent task

For each failure: re-read the failing test. If it tests removed behavior → delete it with `chore(test): remove test for deleted behavior`. If it tests current behavior incorrectly → fix it with `fix(test): ...`. Commit each fix individually.

- [ ] **Step 2: Lint check (Black + Flake8)**

```bash
./scripts/run_tests.sh
```

(Drops the `--quick` flag so the script also runs black + flake8.)

Expected: Black clean for all files modified in Tasks 1-12. Flake8 should be clean for those files. Pre-existing warnings in files NOT touched in v2 (dashboard_helper original lines, stremio_client.py F841, etc. that were there before) are acceptable — leave them.

If a v2-touched file has new Black violations: `black <file>` then commit with `style: black-format <file>`.

If a v2-touched file has new flake8 violations (typically F401 unused imports): fix and commit with `style: drop unused import in <file>`.

- [ ] **Step 3: Confirm net LOC delta**

```bash
git diff --stat 497cc73..HEAD | tail -1
```

Expected: large negative number on the `-` side. The spec estimated ~-1,900 LOC net. Roughly that order.

(`497cc73` was the last v1-era commit. If the v1-era HEAD has moved, substitute its SHA.)

---

## Task 14: CHANGELOG + final version bump

Document v2 for users. Bumping `manifest.json` was done in Task 11; this task just updates the CHANGELOG.

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Append a v2 entry to `CHANGELOG.md`**

Open `CHANGELOG.md`. Add a new section at the top (under `# Changelog`, above the most recent existing entry):

```markdown
## 0.7.0 — 2026-05-18

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
  these from existing config entries on first v2 load.
- Removed the custom Lovelace stream-dialog picker
  (`stremio-stream-dialog.js`). Dashboards that referenced
  `custom:stremio-stream-dialog` will need to be updated.

### Added

- New service `stremio.get_library` — clean, paginated, type-filtered
  library access. Designed for external clients (Zentiahome) that want
  to render their own library UI.
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
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog for v2 (device-first refactor)"
```

---

## Self-review checklist

Run before declaring the branch ready:

- [ ] `pytest tests/ -q` → only the known baseline error
- [ ] `./scripts/run_tests.sh` → no new lint warnings in v2-touched files
- [ ] `git log --oneline 497cc73..HEAD` → ~14 atomic commits, all Conventional Commits format
- [ ] `git diff --stat 497cc73..HEAD` → net negative LOC delta on the order of -1,900
- [ ] Every spec section maps to one or more tasks (verify by re-reading the spec table-of-contents)
- [ ] No `apple_tv_handover` imports remain anywhere
- [ ] `pyatv` is gone from `manifest.json:requirements`
- [ ] Existing config entries with Apple-TV options can load without errors (covered by `test_config_migration.py`)
- [ ] `stremio.handover_to_apple_tv` still works (covered by `test_handover_compat_shim.py`)
- [ ] HA media browser → infoHash stream → torrent server path works (covered by `test_media_source_resolves_infohash.py`)
- [ ] Progress sync via media browser flow works (covered by `test_progress_sync_pending_sessions.py`)
- [ ] `stremio.get_library` works (covered by `test_get_library_service.py`)
- [ ] `media_player.stremio` rejects play_media with translation key `stremio_entity_not_a_player` (covered by `test_media_player_status_only.py`)

## Manual end-to-end smoke (deferred to release verification)

Run on a real Pi (HAOS) or HAOS-in-VM, not part of the implementation:

1. **Upgrade scenario.** Existing user with a v1 config entry that has Apple-TV options → upgrade integration → restart HA → check logs for the migration INFO message → confirm options dialog no longer shows Apple-TV fields → confirm integration loads.
2. **HA media browser flow.** Open Media → Stremio → pick a movie → pick a Torrentio stream → "Send to device" shows real devices (Chromecast, smart TV) but NOT `media_player.stremio` → pick a device → playback starts → progress eventually shows up in Stremio's mobile app.
3. **infoHash stream via media browser.** Same as above with an infoHash-only stream and the torrent server add-on (or local Docker) running → playback starts via the constructed torrent-server URL.
4. **Zentiahome (or simulated via Developer Tools).** Call `stremio.get_library {type: "movie"}` → returns the user's movies. Call `stremio.play_stream` with a stream + entity_id → playback starts.
5. **Legacy automation compatibility.** Old automation calling `stremio.handover_to_apple_tv` → playback starts; HA log shows one deprecation warning.
6. **media_player.stremio rejection.** Try calling `media_player.play_media` on `media_player.stremio` from Developer Tools → see translated `ServiceValidationError` message in the UI.
