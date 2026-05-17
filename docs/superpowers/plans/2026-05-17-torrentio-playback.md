# Torrentio Playback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the HACS Stremio integration so Torrentio streams play reliably on any HA `media_player` entity (Chromecast first), with progress synced back to Stremio's continue-watching state. Ship a companion HA Add-on (`stremio/server` in a container) so the torrent-to-HTTP fallback is one-click for HAOS users.

**Architecture:** Evolutionary extension of the existing single-coordinator architecture. Three new modules in the integration (`stream_resolver`, `playback_manager`, `progress_sync`) plus extensions to existing services/client/coordinator/config_flow/picker UI. A separate HA Add-on repo wraps the upstream `stremio-server` Node.js application in a Docker container with HA-native conventions.

**Tech Stack:** Python 3.12+ async, aiohttp, Home Assistant 2025.1+, `pytest-homeassistant-custom-component`, Lit (frontend), Docker (add-on), HA Supervisor base images.

**Spec:** `docs/superpowers/specs/2026-05-17-torrentio-playback-design.md`

**Target repos:**
- Integration: `https://github.com/Kirill23/hacs-stremio.git`
- Companion add-on: `https://github.com/Kirill23/stremio-link-conversion.git`

---

## Conventions used in this plan

- **TDD throughout:** every code task writes the failing test first, runs it to confirm failure, implements the minimum, runs it to confirm success, then commits.
- **Commits per task** — frequent, atomic, conventional-commits format (`feat:`, `fix:`, `docs:`, `test:`, `chore:`, etc.).
- **Exact file paths** — every step names the file. Line numbers are approximate (existing file shape may shift; rely on the surrounding code shown).
- **Run tests with** `pytest <file>::<test> -v` for single tests, `./scripts/run_tests.sh --quick` for the suite. The `--quick` flag skips linters; full check uses `./scripts/run_tests.sh`.
- **Windows note:** all test commands must run on macOS/Linux/WSL2/devcontainer. Tests auto-skip on native Windows (per `tests/conftest.py`).
- **Existing patterns to follow** (per `CLAUDE.md`):
  - Async everywhere; no blocking I/O.
  - Type hints + Google-style docstrings on public functions.
  - Add new constants to `const.py`, never sprinkle string literals.
  - `StremioAuthError` → `ConfigEntryAuthFailed`, `StremioConnectionError` → `ConfigEntryNotReady`/`UpdateFailed`.
  - All HA service exceptions use `ServiceValidationError` or `HomeAssistantError` with `translation_domain=DOMAIN` and `translation_key=...`.

---

## Task 1: Repo identity rebrand

Update manifest, README, and codeowner references to point at the new fork. Done first so subsequent commits land on a properly-attributed repo.

**Files:**
- Modify: `custom_components/stremio/manifest.json`
- Modify: `README.md`

- [ ] **Step 1: Update `manifest.json` to point at the new repo**

Replace the current value of `documentation`, `issue_tracker`, and `codeowners` so the file looks like:

```json
{
  "domain": "stremio",
  "name": "Stremio Integration",
  "codeowners": [
    "@Kirill23"
  ],
  "config_flow": true,
  "dependencies": [
    "frontend",
    "http",
    "lovelace",
    "media_source"
  ],
  "documentation": "https://github.com/Kirill23/hacs-stremio",
  "integration_type": "service",
  "iot_class": "cloud_polling",
  "issue_tracker": "https://github.com/Kirill23/hacs-stremio/issues",
  "requirements": [
    "pyatv>=0.16.0"
  ],
  "version": "0.5.37"
}
```

- [ ] **Step 2: Update `README.md` badges and URLs**

Find every occurrence of `tamaygz/hacs-stremio` and replace with `Kirill23/hacs-stremio`. Specifically the badge URLs at the top, the install instructions ("Add: `https://github.com/...`"), the credits link to the original work (keep `@AboveColin's stremio-ha` line; replace only `tamaygz` references), the support/issues links at the bottom.

Use `replace_all` for the substring `tamaygz/hacs-stremio` → `Kirill23/hacs-stremio`.

- [ ] **Step 3: Verify nothing else references the old slug**

```bash
grep -rn "tamaygz" --include="*.py" --include="*.md" --include="*.json" --include="*.yml" --include="*.yaml" .
```

Expected: zero results (or only inside `CHANGELOG.md` / historical commits, which are fine to leave). If there are stray references in `services.yaml`, `strings.json`, or `agentinstructions.md`, replace them too.

- [ ] **Step 4: Commit**

```bash
git add custom_components/stremio/manifest.json README.md
git commit -m "chore: rebrand to Kirill23/hacs-stremio fork

Update manifest documentation/issue_tracker URLs and README badges
to reference the new fork at github.com/Kirill23/hacs-stremio."
```

---

## Task 2: Enable CI on push and pull request

The existing `.github/workflows/test.yml` is `workflow_dispatch` only. Switch it to also run on `push` and `pull_request` so regressions are caught automatically.

**Files:**
- Modify: `.github/workflows/test.yml`

- [ ] **Step 1: Update the workflow trigger block**

Open `.github/workflows/test.yml`. Replace:

```yaml
on:
  workflow_dispatch: # Manual trigger only
```

with:

```yaml
on:
  workflow_dispatch:
  push:
    branches: [master, main]
  pull_request:
    branches: [master, main]
```

- [ ] **Step 2: Verify the file parses**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/test.yml'))"
```

Expected: no output, exit code 0.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/test.yml
git commit -m "ci: run tests on push and pull_request

Workflow was previously workflow_dispatch-only, meaning CI never ran
on incoming PRs. Switch to standard triggers so regressions surface
in PR reviews instead of being discovered after merge."
```

---

## Task 3: Add new constants to const.py

Centralize every new service name, config key, and tunable in `const.py`. Required by all subsequent tasks; do this before any code that references these constants.

**Files:**
- Modify: `custom_components/stremio/const.py`

- [ ] **Step 1: Append the new constants to `const.py`**

Open `custom_components/stremio/const.py`. After the existing config keys (around line 47, after `CONF_RESET_ADDON_ORDER`), add:

```python
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
WATCHED_THRESHOLD: Final = 0.9  # mark watched when position/duration >= this
```

Find the existing `SERVICE_*` constants block (around line 100+). Add to it:

```python
SERVICE_PLAY_STREAM: Final = "play_stream"
```

- [ ] **Step 2: Verify the module still imports cleanly**

```bash
source .venv/bin/activate
python -c "from custom_components.stremio import const; print(const.SERVICE_PLAY_STREAM, const.PROGRESS_SYNC_INTERVAL_SECONDS)"
```

Expected: `play_stream 30`

- [ ] **Step 3: Commit**

```bash
git add custom_components/stremio/const.py
git commit -m "feat(const): add constants for torrent server and progress sync

Introduces CONF_TORRENT_SERVER_URL, CONF_PROGRESS_SYNC_ENABLED,
SERVICE_PLAY_STREAM, and the tunables used by the upcoming
stream_resolver, playback_manager, and progress_sync modules."
```

---

## Task 4: Stream resolver module (pure, no HA deps)

The first piece of real logic. Pure function — takes a Stremio stream dict and an optional torrent server URL, returns a playable HTTP/HTTPS URL or raises. No HA state, no I/O. Easy to test exhaustively.

**Files:**
- Create: `custom_components/stremio/stream_resolver.py`
- Create: `tests/test_stream_resolver.py`

- [ ] **Step 1: Write the failing tests first**

Create `tests/test_stream_resolver.py` with the following content:

```python
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
    assert (
        resolve_stream_url(stream, server)
        == "https://debrid.example.com/file.mp4"
    )


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
    assert (
        is_stream_playable({"infoHash": "abc"}, "http://server:11470") is True
    )


def test_is_stream_playable_false_for_empty_dict() -> None:
    assert is_stream_playable({}) is False
```

- [ ] **Step 2: Run the tests to confirm they fail**

```bash
source .venv/bin/activate
pytest tests/test_stream_resolver.py -v
```

Expected: `ModuleNotFoundError: No module named 'custom_components.stremio.stream_resolver'`

- [ ] **Step 3: Implement `stream_resolver.py` to make tests pass**

Create `custom_components/stremio/stream_resolver.py`:

```python
"""Stream URL resolution for Stremio addons.

Translates stream entries from Stremio addons (which may have direct URLs,
torrent infoHashes, or both) into HTTP/HTTPS URLs that HA media_player
entities can play.

The two paths:
- Direct URL (debrid-cached or HTTP source) -> returned as-is.
- infoHash + configured torrent server -> URL constructed against the
  server using the same path format the official stremio-server uses:
  http://<server>/<infoHash>/<fileIdx>

Neither path available -> StreamUnplayableError. Callers surface this as
a user-facing error pointing at the two remediation paths (debrid in
Torrentio config; or install the companion add-on).
"""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


class StreamUnplayableError(Exception):
    """Raised when a stream cannot be converted to a playable URL.

    Means the stream has neither a direct HTTP(S) URL nor a usable
    infoHash + configured torrent server combination.
    """


def resolve_stream_url(
    stream: dict[str, Any],
    torrent_server_url: str | None = None,
) -> str:
    """Return a playable HTTP(S) URL for a Stremio stream entry.

    Args:
        stream: A stream dict from StremioClient.async_get_streams. Keys of
            interest: ``url`` (optional), ``infoHash`` (optional), ``fileIdx``
            (optional; defaults to 0).
        torrent_server_url: Base URL of a stremio-server instance, e.g.
            "http://homeassistant.local:11470". May be None or empty.

    Returns:
        An HTTP or HTTPS URL ready to hand to media_player.play_media.

    Raises:
        StreamUnplayableError: If neither a direct HTTP(S) URL nor an
            infoHash + torrent server combination is available.
    """
    direct_url = stream.get("url")
    if direct_url and isinstance(direct_url, str) and direct_url.startswith(
        ("http://", "https://")
    ):
        return direct_url

    info_hash = stream.get("infoHash")
    if info_hash and torrent_server_url:
        file_idx = stream.get("fileIdx", 0)
        base = torrent_server_url.rstrip("/")
        return f"{base}/{info_hash}/{file_idx}"

    raise StreamUnplayableError(
        f"Stream cannot be resolved to a playable URL: "
        f"has_direct_url={bool(direct_url)}, "
        f"has_infoHash={bool(info_hash)}, "
        f"torrent_server_configured={bool(torrent_server_url)}"
    )


def is_stream_playable(
    stream: dict[str, Any],
    torrent_server_url: str | None = None,
) -> bool:
    """Return True if the stream can be resolved to a playable URL.

    Used by ``stremio.get_streams`` to annotate each stream with a
    ``playable`` flag so callers can grey out unplayable rows in the picker.
    """
    try:
        resolve_stream_url(stream, torrent_server_url)
        return True
    except StreamUnplayableError:
        return False
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_stream_resolver.py -v
```

Expected: all 14 tests pass.

- [ ] **Step 5: Commit**

```bash
git add custom_components/stremio/stream_resolver.py tests/test_stream_resolver.py
git commit -m "feat(stream_resolver): add pure URL resolution module

Translates Stremio stream entries to playable HTTP(S) URLs. Two paths:
direct URL (debrid output) returned as-is, or infoHash + configured
torrent server -> URL constructed against the server. Raises
StreamUnplayableError when neither path works (the fix for the current
'no supported format' bug callers will surface with an actionable
message in a later task)."
```

---

## Task 5: Stremio client — progress write method

Add `async_update_library_progress` to `StremioClient`. Writes a `lastWatched`/`timeOffset` entry via `datastorePut` in the same shape Stremio's mobile apps use.

**Files:**
- Modify: `custom_components/stremio/stremio_client.py`
- Modify: `tests/test_stremio_client.py`

- [ ] **Step 1: Write the failing test**

Open `tests/test_stremio_client.py`. Add this test at the end of the file (adjust imports if needed):

```python
async def test_update_library_progress_writes_correct_payload(
    aioresponses_mock,
) -> None:
    """Progress update writes a datastorePut payload matching Stremio mobile."""
    from custom_components.stremio.stremio_client import (
        STREMIO_DATASTORE_PUT_URL,
        StremioClient,
    )
    from aiohttp import ClientSession

    captured: dict = {}

    def _capture(url: str, **kwargs) -> aioresponses_mock.CallbackResult:  # type: ignore
        captured["payload"] = kwargs.get("json")
        return aioresponses_mock.CallbackResult(
            payload={"success": True}, status=200
        )

    aioresponses_mock.post(
        STREMIO_DATASTORE_PUT_URL, callback=_capture, repeat=True
    )

    async with ClientSession() as session:
        client = StremioClient(
            email="e@x.com", password="p", session=session
        )
        client._auth_key = "fake-auth-key"  # bypass login for unit test

        await client.async_update_library_progress(
            media_id="tt1375666",
            media_type="movie",
            position_seconds=1234.5,
            duration_seconds=8400.0,
        )

    payload = captured["payload"]
    assert payload["authKey"] == "fake-auth-key"
    assert payload["collection"] == "libraryItem"
    # changes is a list with one entry
    assert isinstance(payload["changes"], list) and len(payload["changes"]) == 1
    change = payload["changes"][0]
    assert change["_id"] == "tt1375666"
    assert change["type"] == "movie"
    assert change["state"]["timeOffset"] == 1234.5
    assert change["state"]["duration"] == 8400.0
    # Below WATCHED_THRESHOLD -> not flagged watched
    assert change["state"]["flaggedWatched"] == 0


async def test_update_library_progress_flags_watched_at_threshold(
    aioresponses_mock,
) -> None:
    """When position/duration >= WATCHED_THRESHOLD, flaggedWatched is set."""
    from custom_components.stremio.stremio_client import (
        STREMIO_DATASTORE_PUT_URL,
        StremioClient,
    )
    from aiohttp import ClientSession

    captured: dict = {}

    def _capture(url: str, **kwargs) -> aioresponses_mock.CallbackResult:  # type: ignore
        captured["payload"] = kwargs.get("json")
        return aioresponses_mock.CallbackResult(
            payload={"success": True}, status=200
        )

    aioresponses_mock.post(
        STREMIO_DATASTORE_PUT_URL, callback=_capture, repeat=True
    )

    async with ClientSession() as session:
        client = StremioClient(
            email="e@x.com", password="p", session=session
        )
        client._auth_key = "fake-auth-key"

        # 95% through -> above WATCHED_THRESHOLD (0.9)
        await client.async_update_library_progress(
            media_id="tt1375666",
            media_type="movie",
            position_seconds=7980.0,
            duration_seconds=8400.0,
        )

    change = captured["payload"]["changes"][0]
    assert change["state"]["flaggedWatched"] == 1
```

If `aioresponses_mock` is not already a fixture in `tests/conftest.py`, add it:

```python
import pytest
from aioresponses import aioresponses


@pytest.fixture
def aioresponses_mock():
    """aioresponses context manager exposed as a fixture."""
    with aioresponses() as m:
        yield m
```

- [ ] **Step 2: Run the tests to confirm they fail**

```bash
pytest tests/test_stremio_client.py::test_update_library_progress_writes_correct_payload -v
```

Expected: `AttributeError: 'StremioClient' object has no attribute 'async_update_library_progress'`

- [ ] **Step 3: Implement `async_update_library_progress` in `stremio_client.py`**

Open `custom_components/stremio/stremio_client.py`. Find an appropriate location near the other datastore methods (after `async_get_library` or similar). Add:

```python
async def async_update_library_progress(
    self,
    media_id: str,
    media_type: str,
    position_seconds: float,
    duration_seconds: float,
) -> None:
    """Update Stremio's continue-watching state for a library item.

    Writes a libraryItem change via datastorePut in the same shape the
    Stremio mobile apps write. The Stremio web/mobile apps poll the
    datastore and pick up these updates so progress syncs across devices.

    Args:
        media_id: IMDb-style content ID (e.g. "tt1375666"). Used as _id.
        media_type: "movie" or "series".
        position_seconds: Current playback position in seconds.
        duration_seconds: Total content duration in seconds. May be 0
            if the player hasn't reported duration yet; the watched
            threshold check is skipped in that case.

    Raises:
        StremioAuthError: Authentication failed.
        StremioConnectionError: Network or API failure.
    """
    from .const import WATCHED_THRESHOLD  # local import to avoid cycle

    if not self._auth_key:
        raise StremioConnectionError("Client not authenticated")

    watched = (
        1
        if duration_seconds > 0
        and position_seconds / duration_seconds >= WATCHED_THRESHOLD
        else 0
    )

    payload = {
        "authKey": self._auth_key,
        "collection": COLLECTION_LIBRARY_ITEM,
        "changes": [
            {
                "_id": media_id,
                "type": media_type,
                "state": {
                    "timeOffset": position_seconds,
                    "duration": duration_seconds,
                    "lastWatched": _utc_iso_ms_z(),
                    "flaggedWatched": watched,
                },
                "_mtime": _utc_iso_ms_z(),
            }
        ],
    }

    try:
        async with self._session.post(
            STREMIO_DATASTORE_PUT_URL,
            json=payload,
            timeout=ClientTimeout(total=10),
        ) as response:
            if response.status == 401:
                raise StremioAuthError("Auth key rejected during progress write")
            response.raise_for_status()
            _LOGGER.debug(
                "Progress write OK: media_id=%s position=%.1f/%.1f watched=%d",
                media_id,
                position_seconds,
                duration_seconds,
                watched,
            )
    except ClientError as err:
        raise StremioConnectionError(
            f"Failed to write progress for {media_id}: {err}"
        ) from err
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_stremio_client.py::test_update_library_progress_writes_correct_payload tests/test_stremio_client.py::test_update_library_progress_flags_watched_at_threshold -v
```

Expected: both tests pass.

- [ ] **Step 5: Run the whole client test file to ensure no regression**

```bash
pytest tests/test_stremio_client.py -v
```

Expected: all tests pass (existing + 2 new).

- [ ] **Step 6: Commit**

```bash
git add custom_components/stremio/stremio_client.py tests/test_stremio_client.py tests/conftest.py
git commit -m "feat(client): add async_update_library_progress

Writes Stremio continue-watching state via datastorePut using the
same payload shape Stremio mobile apps use (timeOffset, duration,
lastWatched, flaggedWatched). Required for the progress_sync module
in the next task."
```

---

## Task 6: Playback manager (generalize handover to any media_player)

Encapsulates the `media_player.play_media` service call with proper content type, content ID, and metadata. Replaces the inline `play_media` calls in `apple_tv_handover.py` and gives `services.handle_play_stream` a clean target to call.

**Files:**
- Create: `custom_components/stremio/playback_manager.py`
- Create: `tests/test_playback_manager.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_playback_manager.py`:

```python
"""Tests for PlaybackManager."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.stremio.playback_manager import PlaybackManager


@pytest.fixture
def mock_hass() -> HomeAssistant:
    """Minimal HomeAssistant mock for play_media tests."""
    hass = MagicMock(spec=HomeAssistant)
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock(return_value=None)
    hass.states = MagicMock()
    return hass


def _entity_state(state: str = "idle"):
    s = MagicMock()
    s.state = state
    return s


async def test_play_calls_media_player_play_media(mock_hass) -> None:
    mock_hass.states.get.return_value = _entity_state("idle")
    mgr = PlaybackManager(mock_hass)

    await mgr.play(
        entity_id="media_player.living_room",
        stream_url="https://example.com/movie.mp4",
        media_info={"title": "Inception", "poster": "https://example.com/p.jpg"},
    )

    mock_hass.services.async_call.assert_awaited_once()
    args, kwargs = mock_hass.services.async_call.call_args
    assert args[0] == "media_player"
    assert args[1] == "play_media"
    payload = args[2]
    assert payload["entity_id"] == "media_player.living_room"
    assert payload["media_content_id"] == "https://example.com/movie.mp4"
    assert payload["media_content_type"] == "video"
    # Extra metadata passed via the ``extra`` key
    assert payload["extra"]["title"] == "Inception"
    assert payload["extra"]["thumb"] == "https://example.com/p.jpg"


async def test_play_raises_for_non_media_player_entity(mock_hass) -> None:
    mgr = PlaybackManager(mock_hass)
    with pytest.raises(ServiceValidationError):
        await mgr.play(
            entity_id="light.living_room",
            stream_url="https://example.com/movie.mp4",
            media_info={},
        )
    mock_hass.services.async_call.assert_not_awaited()


async def test_play_raises_when_entity_missing(mock_hass) -> None:
    mock_hass.states.get.return_value = None
    mgr = PlaybackManager(mock_hass)
    with pytest.raises(ServiceValidationError):
        await mgr.play(
            entity_id="media_player.nonexistent",
            stream_url="https://example.com/movie.mp4",
            media_info={},
        )


async def test_play_raises_when_entity_unavailable(mock_hass) -> None:
    mock_hass.states.get.return_value = _entity_state("unavailable")
    mgr = PlaybackManager(mock_hass)
    with pytest.raises(ServiceValidationError):
        await mgr.play(
            entity_id="media_player.unplugged_tv",
            stream_url="https://example.com/movie.mp4",
            media_info={},
        )


async def test_play_does_not_block_on_service_call(mock_hass) -> None:
    """play() returns after async_call resolves; it does not await playback start."""
    mock_hass.states.get.return_value = _entity_state("idle")
    mgr = PlaybackManager(mock_hass)
    await mgr.play(
        entity_id="media_player.tv",
        stream_url="https://example.com/x.mp4",
        media_info={},
    )
    # No assertion on blocking — passing without timeout is the assertion.
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_playback_manager.py -v
```

Expected: `ModuleNotFoundError: No module named 'custom_components.stremio.playback_manager'`

- [ ] **Step 3: Implement `playback_manager.py`**

Create `custom_components/stremio/playback_manager.py`:

```python
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
    ) -> None:
        """Hand a stream URL to the named media_player entity.

        Args:
            entity_id: HA entity id (must start with "media_player.").
            stream_url: HTTP(S) URL the device will fetch and play.
            media_info: Optional metadata. Recognized keys: title, poster,
                year, type ("movie" or "series"), season, episode.

        Raises:
            ServiceValidationError: entity does not exist, is not a
                media_player, or is unavailable.
        """
        self._validate_entity(entity_id)

        title = media_info.get("title") or ""
        poster = media_info.get("poster") or ""
        media_type = (
            "tvshow" if media_info.get("type") == "series" else "video"
        )

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
            blocking=False,
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
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_playback_manager.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add custom_components/stremio/playback_manager.py tests/test_playback_manager.py
git commit -m "feat(playback): add PlaybackManager for generic media_player playback

Encapsulates media_player.play_media calls with correct content type,
content id, and metadata via 'extra'. Validates entity domain and
availability before dispatching. Will replace inline play_media calls
in apple_tv_handover (compat shim in a later task) and provides the
target for the new stremio.play_stream service."
```

---

## Task 7: Progress sync — session registry

ProgressSyncManager has two responsibilities; build them in two passes. First pass: the session registry (start/stop tracking sessions; no listeners yet). This is pure state management and easy to test in isolation.

**Files:**
- Create: `custom_components/stremio/progress_sync.py`
- Create: `tests/test_progress_sync.py`

- [ ] **Step 1: Write failing tests for the session registry**

Create `tests/test_progress_sync.py`:

```python
"""Tests for ProgressSyncManager."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.stremio.progress_sync import (
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


def test_register_session_records_entity_and_media(mock_hass, mock_client) -> None:
    mgr = ProgressSyncManager(mock_hass, mock_client)
    mgr.register_session(
        entity_id="media_player.living_room",
        media_id="tt1375666",
        media_type="movie",
        media_content_id="https://example.com/movie.mp4",
    )
    session = mgr.get_session("media_player.living_room")
    assert isinstance(session, PlaybackSession)
    assert session.media_id == "tt1375666"
    assert session.media_type == "movie"
    assert session.media_content_id == "https://example.com/movie.mp4"


def test_unregister_session_removes_entry(mock_hass, mock_client) -> None:
    mgr = ProgressSyncManager(mock_hass, mock_client)
    mgr.register_session(
        entity_id="media_player.living_room",
        media_id="tt1375666",
        media_type="movie",
        media_content_id="https://example.com/movie.mp4",
    )
    mgr.unregister_session("media_player.living_room")
    assert mgr.get_session("media_player.living_room") is None


def test_register_replaces_existing_session(mock_hass, mock_client) -> None:
    mgr = ProgressSyncManager(mock_hass, mock_client)
    mgr.register_session(
        entity_id="media_player.tv",
        media_id="tt001",
        media_type="movie",
        media_content_id="https://a/x.mp4",
    )
    mgr.register_session(
        entity_id="media_player.tv",
        media_id="tt002",
        media_type="movie",
        media_content_id="https://b/y.mp4",
    )
    s = mgr.get_session("media_player.tv")
    assert s.media_id == "tt002"


def test_active_entities_returns_currently_registered(
    mock_hass, mock_client
) -> None:
    mgr = ProgressSyncManager(mock_hass, mock_client)
    mgr.register_session(
        entity_id="media_player.a",
        media_id="tt1",
        media_type="movie",
        media_content_id="https://a/1.mp4",
    )
    mgr.register_session(
        entity_id="media_player.b",
        media_id="tt2",
        media_type="movie",
        media_content_id="https://b/2.mp4",
    )
    assert set(mgr.active_entities()) == {"media_player.a", "media_player.b"}
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_progress_sync.py -v
```

Expected: `ModuleNotFoundError: No module named 'custom_components.stremio.progress_sync'`

- [ ] **Step 3: Implement the session registry (no listeners yet)**

Create `custom_components/stremio/progress_sync.py`:

```python
"""Playback session registry and Stremio progress sync.

This module tracks active playback sessions (one per HA media_player
entity) that the integration initiated via stremio.play_stream. It
listens for state changes on those entities and writes throttled
progress updates to Stremio's datastore so the Stremio web/mobile apps
see correct continue-watching state.

This first version covers the registry. The state-change listener and
throttled writer are added in the next task.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant

if TYPE_CHECKING:
    from .stremio_client import StremioClient

_LOGGER = logging.getLogger(__name__)


@dataclass
class PlaybackSession:
    """State for one tracked playback session."""

    media_id: str
    media_type: str
    media_content_id: str  # the stream URL we handed to play_media
    started_at: float = field(default_factory=time.monotonic)
    last_synced_at: float = 0.0
    last_position: float = 0.0
    last_duration: float = 0.0


class ProgressSyncManager:
    """Tracks playback sessions and syncs progress to Stremio."""

    def __init__(
        self, hass: HomeAssistant, client: "StremioClient"
    ) -> None:
        self._hass = hass
        self._client = client
        self._sessions: dict[str, PlaybackSession] = {}

    def register_session(
        self,
        entity_id: str,
        media_id: str,
        media_type: str,
        media_content_id: str,
    ) -> None:
        """Start tracking playback on entity_id.

        Replaces any existing session for the same entity_id.
        """
        self._sessions[entity_id] = PlaybackSession(
            media_id=media_id,
            media_type=media_type,
            media_content_id=media_content_id,
        )
        _LOGGER.debug(
            "Registered session: entity=%s media=%s (%s)",
            entity_id,
            media_id,
            media_type,
        )

    def unregister_session(self, entity_id: str) -> None:
        """Stop tracking playback on entity_id. Safe to call twice."""
        if entity_id in self._sessions:
            del self._sessions[entity_id]
            _LOGGER.debug("Unregistered session: entity=%s", entity_id)

    def get_session(self, entity_id: str) -> PlaybackSession | None:
        return self._sessions.get(entity_id)

    def active_entities(self) -> list[str]:
        return list(self._sessions.keys())
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_progress_sync.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add custom_components/stremio/progress_sync.py tests/test_progress_sync.py
git commit -m "feat(progress_sync): add PlaybackSession registry

Tracks active Stremio-initiated playback sessions per media_player
entity. Registry-only in this commit; the state-change listener and
throttled Stremio writer come in the next task."
```

---

## Task 8: Progress sync — state listener and throttled writes

Second pass on `ProgressSyncManager`. Subscribe to HA state-change events. Throttle writes to Stremio (every `PROGRESS_SYNC_INTERVAL_SECONDS` while playing; immediate on pause/stop). Detect mismatched content and unregister cleanly.

**Files:**
- Modify: `custom_components/stremio/progress_sync.py`
- Modify: `tests/test_progress_sync.py`

- [ ] **Step 1: Add failing tests for the listener behavior**

Append to `tests/test_progress_sync.py`:

```python
def _make_state(
    state: str,
    media_content_id: str | None = None,
    media_position: float | None = None,
    media_duration: float | None = None,
) -> MagicMock:
    s = MagicMock()
    s.state = state
    s.attributes = {}
    if media_content_id is not None:
        s.attributes["media_content_id"] = media_content_id
    if media_position is not None:
        s.attributes["media_position"] = media_position
    if media_duration is not None:
        s.attributes["media_duration"] = media_duration
    return s


def _state_event(entity_id: str, new_state) -> MagicMock:
    e = MagicMock()
    e.data = {"entity_id": entity_id, "new_state": new_state}
    return e


async def test_state_change_to_playing_does_not_immediately_write(
    mock_hass, mock_client
) -> None:
    """First playing state shouldn't trigger an immediate datastore write."""
    mgr = ProgressSyncManager(mock_hass, mock_client)
    mgr.register_session(
        entity_id="media_player.tv",
        media_id="tt001",
        media_type="movie",
        media_content_id="https://a/x.mp4",
    )
    state = _make_state(
        "playing",
        media_content_id="https://a/x.mp4",
        media_position=15.0,
        media_duration=7200.0,
    )
    await mgr._handle_state_change(_state_event("media_player.tv", state))

    # Session updated but no write (within throttle interval)
    s = mgr.get_session("media_player.tv")
    assert s.last_position == 15.0
    assert s.last_duration == 7200.0
    mock_client.async_update_library_progress.assert_not_awaited()


async def test_state_change_after_throttle_interval_writes(
    mock_hass, mock_client
) -> None:
    from custom_components.stremio.const import (
        PROGRESS_SYNC_INTERVAL_SECONDS,
    )

    mgr = ProgressSyncManager(mock_hass, mock_client)
    mgr.register_session(
        entity_id="media_player.tv",
        media_id="tt001",
        media_type="movie",
        media_content_id="https://a/x.mp4",
    )
    # Pretend last sync happened > interval ago
    mgr._sessions["media_player.tv"].last_synced_at = (
        time.monotonic() - PROGRESS_SYNC_INTERVAL_SECONDS - 1
    )

    state = _make_state(
        "playing",
        media_content_id="https://a/x.mp4",
        media_position=300.0,
        media_duration=7200.0,
    )
    await mgr._handle_state_change(_state_event("media_player.tv", state))

    mock_client.async_update_library_progress.assert_awaited_once_with(
        media_id="tt001",
        media_type="movie",
        position_seconds=300.0,
        duration_seconds=7200.0,
    )


async def test_paused_triggers_immediate_write(mock_hass, mock_client) -> None:
    mgr = ProgressSyncManager(mock_hass, mock_client)
    mgr.register_session(
        entity_id="media_player.tv",
        media_id="tt001",
        media_type="movie",
        media_content_id="https://a/x.mp4",
    )
    # Within throttle window
    mgr._sessions["media_player.tv"].last_synced_at = time.monotonic()

    state = _make_state(
        "paused",
        media_content_id="https://a/x.mp4",
        media_position=42.0,
        media_duration=7200.0,
    )
    await mgr._handle_state_change(_state_event("media_player.tv", state))

    mock_client.async_update_library_progress.assert_awaited_once()


async def test_mismatched_content_id_unregisters_session(
    mock_hass, mock_client
) -> None:
    mgr = ProgressSyncManager(mock_hass, mock_client)
    mgr.register_session(
        entity_id="media_player.tv",
        media_id="tt001",
        media_type="movie",
        media_content_id="https://a/x.mp4",
    )

    # User cast something else to the same Chromecast
    state = _make_state(
        "playing",
        media_content_id="https://youtube.com/some-video",
        media_position=10.0,
        media_duration=600.0,
    )
    await mgr._handle_state_change(_state_event("media_player.tv", state))

    assert mgr.get_session("media_player.tv") is None
    mock_client.async_update_library_progress.assert_not_awaited()


async def test_state_change_for_unregistered_entity_is_ignored(
    mock_hass, mock_client
) -> None:
    mgr = ProgressSyncManager(mock_hass, mock_client)
    state = _make_state("playing", media_content_id="https://x/y.mp4")
    await mgr._handle_state_change(
        _state_event("media_player.not_tracked", state)
    )
    mock_client.async_update_library_progress.assert_not_awaited()


async def test_idle_with_high_position_writes_watched(
    mock_hass, mock_client
) -> None:
    """Going to idle past WATCHED_THRESHOLD triggers a final write and unregister."""
    mgr = ProgressSyncManager(mock_hass, mock_client)
    mgr.register_session(
        entity_id="media_player.tv",
        media_id="tt001",
        media_type="movie",
        media_content_id="https://a/x.mp4",
    )
    # Simulate that we last saw position near the end
    mgr._sessions["media_player.tv"].last_position = 7000.0
    mgr._sessions["media_player.tv"].last_duration = 7200.0

    state = _make_state("idle")  # no media_content_id (Chromecast cleared it)
    await mgr._handle_state_change(_state_event("media_player.tv", state))

    mock_client.async_update_library_progress.assert_awaited_once_with(
        media_id="tt001",
        media_type="movie",
        position_seconds=7000.0,
        duration_seconds=7200.0,
    )
    # Session is cleaned up after the final write
    assert mgr.get_session("media_player.tv") is None


async def test_sync_write_failure_keeps_session_alive(
    mock_hass, mock_client
) -> None:
    from custom_components.stremio.stremio_client import StremioConnectionError
    from custom_components.stremio.const import (
        PROGRESS_SYNC_INTERVAL_SECONDS,
    )

    mock_client.async_update_library_progress.side_effect = (
        StremioConnectionError("API down")
    )

    mgr = ProgressSyncManager(mock_hass, mock_client)
    mgr.register_session(
        entity_id="media_player.tv",
        media_id="tt001",
        media_type="movie",
        media_content_id="https://a/x.mp4",
    )
    mgr._sessions["media_player.tv"].last_synced_at = (
        time.monotonic() - PROGRESS_SYNC_INTERVAL_SECONDS - 1
    )

    state = _make_state(
        "playing",
        media_content_id="https://a/x.mp4",
        media_position=300.0,
        media_duration=7200.0,
    )
    # Should not raise
    await mgr._handle_state_change(_state_event("media_player.tv", state))

    # Session still registered
    assert mgr.get_session("media_player.tv") is not None
```

- [ ] **Step 2: Run new tests to confirm they fail**

```bash
pytest tests/test_progress_sync.py -v
```

Expected: AttributeError or NotImplementedError on `_handle_state_change` (method doesn't exist).

- [ ] **Step 3: Extend `progress_sync.py` with the listener and writer**

Update `custom_components/stremio/progress_sync.py` to:

```python
"""Playback session registry and Stremio progress sync.

Tracks active playback sessions (one per HA media_player entity) that the
integration initiated via stremio.play_stream. Subscribes to HA state
changes on those entities; throttle-writes progress back to Stremio so
the web/mobile apps see continue-watching state.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant

from .const import PROGRESS_SYNC_INTERVAL_SECONDS, WATCHED_THRESHOLD

if TYPE_CHECKING:
    from .stremio_client import StremioClient

_LOGGER = logging.getLogger(__name__)

_PLAYING_STATES = {"playing"}
_PAUSED_STATES = {"paused"}
_TERMINAL_STATES = {"idle", "off", "standby", "unavailable", "unknown"}


@dataclass
class PlaybackSession:
    """State for one tracked playback session."""

    media_id: str
    media_type: str
    media_content_id: str
    started_at: float = field(default_factory=time.monotonic)
    last_synced_at: float = 0.0
    last_position: float = 0.0
    last_duration: float = 0.0


class ProgressSyncManager:
    """Tracks playback sessions and syncs progress to Stremio."""

    def __init__(
        self, hass: HomeAssistant, client: "StremioClient"
    ) -> None:
        self._hass = hass
        self._client = client
        self._sessions: dict[str, PlaybackSession] = {}
        self._unsub_state_listener: Callable[[], None] | None = None

    def start(self) -> None:
        """Subscribe to HA state changes. Idempotent."""
        if self._unsub_state_listener is not None:
            return
        self._unsub_state_listener = self._hass.bus.async_listen(
            EVENT_STATE_CHANGED, self._handle_state_change
        )
        _LOGGER.debug("ProgressSyncManager started")

    def stop(self) -> None:
        if self._unsub_state_listener is not None:
            self._unsub_state_listener()
            self._unsub_state_listener = None
        self._sessions.clear()

    def register_session(
        self,
        entity_id: str,
        media_id: str,
        media_type: str,
        media_content_id: str,
    ) -> None:
        self._sessions[entity_id] = PlaybackSession(
            media_id=media_id,
            media_type=media_type,
            media_content_id=media_content_id,
        )
        _LOGGER.debug(
            "Registered session: entity=%s media=%s (%s)",
            entity_id, media_id, media_type,
        )

    def unregister_session(self, entity_id: str) -> None:
        if entity_id in self._sessions:
            del self._sessions[entity_id]
            _LOGGER.debug("Unregistered session: entity=%s", entity_id)

    def get_session(self, entity_id: str) -> PlaybackSession | None:
        return self._sessions.get(entity_id)

    def active_entities(self) -> list[str]:
        return list(self._sessions.keys())

    async def _handle_state_change(self, event: Event) -> None:
        """Process a state_changed event for any registered entity."""
        entity_id = event.data.get("entity_id")
        if entity_id not in self._sessions:
            return

        session = self._sessions[entity_id]
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        attrs = getattr(new_state, "attributes", {}) or {}
        current_content_id = attrs.get("media_content_id")
        state_value = new_state.state

        # User cast different content to this device -> our session is dead.
        if (
            current_content_id
            and current_content_id != session.media_content_id
            and state_value in _PLAYING_STATES | _PAUSED_STATES
        ):
            _LOGGER.debug(
                "Content mismatch on %s (expected %s, got %s); unregistering",
                entity_id, session.media_content_id, current_content_id,
            )
            self.unregister_session(entity_id)
            return

        # Update last-known position/duration from the player attributes.
        position = attrs.get("media_position")
        duration = attrs.get("media_duration")
        if position is not None:
            session.last_position = float(position)
        if duration is not None:
            session.last_duration = float(duration)

        # Terminal states: write a final update if we have meaningful state.
        if state_value in _TERMINAL_STATES:
            if session.last_position > 0 and session.last_duration > 0:
                await self._safe_write_progress(session)
            self.unregister_session(entity_id)
            return

        # Pause: immediate flush.
        if state_value in _PAUSED_STATES:
            await self._safe_write_progress(session)
            return

        # Playing: throttle.
        if state_value in _PLAYING_STATES:
            now = time.monotonic()
            if now - session.last_synced_at >= PROGRESS_SYNC_INTERVAL_SECONDS:
                await self._safe_write_progress(session)

    async def _safe_write_progress(self, session: PlaybackSession) -> None:
        """Write progress; swallow errors so listener stays healthy."""
        try:
            await self._client.async_update_library_progress(
                media_id=session.media_id,
                media_type=session.media_type,
                position_seconds=session.last_position,
                duration_seconds=session.last_duration,
            )
            session.last_synced_at = time.monotonic()
        except Exception as err:  # noqa: BLE001 — explicitly broad: sync must not crash
            _LOGGER.warning(
                "Progress sync write failed for %s: %s",
                session.media_id, err,
            )
```

- [ ] **Step 4: Run all tests to confirm they pass**

```bash
pytest tests/test_progress_sync.py -v
```

Expected: all 11 tests pass (4 from Task 7 + 7 new).

- [ ] **Step 5: Commit**

```bash
git add custom_components/stremio/progress_sync.py tests/test_progress_sync.py
git commit -m "feat(progress_sync): wire state listener and throttled writes

Adds the EVENT_STATE_CHANGED listener and throttled write logic.
Writes happen on pause/stop (immediate) and during playing
(every PROGRESS_SYNC_INTERVAL_SECONDS). Mismatched media_content_id
unregisters the session silently. Write failures are logged at WARNING
and do not crash the listener; the session is preserved for retry on
the next tick."
```

---

## Task 9: Coordinator owns the ProgressSyncManager

Instantiate `ProgressSyncManager` from the coordinator. Expose `register_playback_session` / `unregister_playback_session` for services to call. Start the listener after the first refresh; stop on unload.

**Files:**
- Modify: `custom_components/stremio/coordinator.py`
- Modify: `custom_components/stremio/__init__.py`

- [ ] **Step 1: Add ProgressSyncManager to the coordinator**

Open `custom_components/stremio/coordinator.py`. Add to the imports near the top (alongside the existing `.const` import):

```python
from .const import (
    # ... existing imports kept ...
    CONF_PROGRESS_SYNC_ENABLED,
    DEFAULT_PROGRESS_SYNC_ENABLED,
)
from .progress_sync import ProgressSyncManager
```

In `StremioDataUpdateCoordinator.__init__` (after the existing `self.client = client` line), add:

```python
self.progress_sync = ProgressSyncManager(hass, client)
self._progress_sync_started = False
```

Add a new method on the coordinator class:

```python
def start_progress_sync(self) -> None:
    """Start the progress-sync listener if enabled in options.

    Called from async_setup_entry after the first successful refresh.
    Idempotent.
    """
    if self._progress_sync_started:
        return
    if not self._progress_sync_enabled():
        _LOGGER.info("Progress sync disabled via options; not starting listener")
        return
    self.progress_sync.start()
    self._progress_sync_started = True

def stop_progress_sync(self) -> None:
    """Stop the listener (called on unload). Idempotent."""
    if self._progress_sync_started:
        self.progress_sync.stop()
        self._progress_sync_started = False

def _progress_sync_enabled(self) -> bool:
    entry = self._entry_param  # set in __init__, see below
    return bool(
        entry.options.get(
            CONF_PROGRESS_SYNC_ENABLED, DEFAULT_PROGRESS_SYNC_ENABLED
        )
    )

def register_playback_session(
    self,
    entity_id: str,
    media_id: str,
    media_type: str,
    media_content_id: str,
) -> None:
    """Public registration API; called from services.handle_play_stream."""
    self.progress_sync.register_session(
        entity_id=entity_id,
        media_id=media_id,
        media_type=media_type,
        media_content_id=media_content_id,
    )

def unregister_playback_session(self, entity_id: str) -> None:
    self.progress_sync.unregister_session(entity_id)
```

The coordinator already stores `self._entry_param = entry` per existing code (see `coordinator.py:65-66`); if for any reason that field is renamed in the file you're editing, use whatever name the existing code uses for the saved ConfigEntry.

- [ ] **Step 2: Start the listener from `__init__.async_setup_entry`**

Open `custom_components/stremio/__init__.py`. After the existing `await coordinator.async_config_entry_first_refresh()` line, add:

```python
coordinator.start_progress_sync()
```

In `async_unload_entry`, after the platforms are unloaded successfully (within the `if unload_ok:` block, before the existing `data = hass.data[DOMAIN].pop(entry.entry_id)` line), add:

```python
coordinator = hass.data[DOMAIN].get(entry.entry_id, {}).get("coordinator")
if coordinator:
    coordinator.stop_progress_sync()
```

- [ ] **Step 3: Add a smoke test for coordinator integration**

Open `tests/test_coordinator.py` (already exists). Add this test at the end:

```python
async def test_coordinator_owns_progress_sync_manager(hass) -> None:
    """Coordinator instantiates a ProgressSyncManager exposed as .progress_sync."""
    from unittest.mock import MagicMock
    from custom_components.stremio.coordinator import (
        StremioDataUpdateCoordinator,
    )
    from custom_components.stremio.progress_sync import ProgressSyncManager

    client = MagicMock()
    entry = MagicMock()
    entry.options = {}
    coord = StremioDataUpdateCoordinator(hass=hass, client=client, entry=entry)

    assert isinstance(coord.progress_sync, ProgressSyncManager)
    assert coord.register_playback_session  # callable
    assert coord.unregister_playback_session  # callable
```

- [ ] **Step 4: Run the new test**

```bash
pytest tests/test_coordinator.py::test_coordinator_owns_progress_sync_manager -v
```

Expected: PASS.

- [ ] **Step 5: Run the whole coordinator test file to check for regression**

```bash
pytest tests/test_coordinator.py -v
```

Expected: all tests pass (existing + 1 new).

- [ ] **Step 6: Commit**

```bash
git add custom_components/stremio/coordinator.py custom_components/stremio/__init__.py tests/test_coordinator.py
git commit -m "feat(coordinator): own ProgressSyncManager lifecycle

Coordinator instantiates a ProgressSyncManager and exposes register/
unregister_playback_session for services to call. The listener starts
after the first refresh (if enabled via options) and stops on entry
unload. Idempotent so reloads don't double-subscribe."
```

---

## Task 10: Services — `get_streams` returns `playable` flag

Extend the existing `stremio.get_streams` service response so each stream entry carries a `playable: bool` derived from the resolver. Pickers use this to grey out unplayable rows.

**Files:**
- Modify: `custom_components/stremio/services.py`
- Modify: `tests/test_services.py`

- [ ] **Step 1: Add the failing test**

Open `tests/test_services.py`. Add:

```python
async def test_get_streams_annotates_playable_flag(
    hass, mock_config_entry_setup
):
    """Each stream in the response gets a 'playable' bool."""
    from custom_components.stremio.const import DOMAIN
    from unittest.mock import AsyncMock, patch

    fake_streams = [
        {"name": "Direct URL", "url": "https://debrid/x.mp4"},
        {"name": "Magnet only", "infoHash": "abc123"},
    ]
    with patch(
        "custom_components.stremio.stremio_client.StremioClient.async_get_streams",
        new=AsyncMock(return_value=fake_streams),
    ):
        result = await hass.services.async_call(
            DOMAIN,
            "get_streams",
            {"media_id": "tt001", "media_type": "movie"},
            blocking=True,
            return_response=True,
        )

    streams = result["streams"]
    assert streams[0]["playable"] is True
    assert streams[1]["playable"] is False
```

Note: `mock_config_entry_setup` is assumed to be an existing fixture used by other tests in this file. If it doesn't exist, copy the pattern from any existing service test in the file.

- [ ] **Step 2: Run the test to confirm failure**

```bash
pytest tests/test_services.py::test_get_streams_annotates_playable_flag -v
```

Expected: KeyError or AssertionError on `playable`.

- [ ] **Step 3: Update `handle_get_streams` in `services.py`**

Open `custom_components/stremio/services.py`. Add to the imports at the top of the file:

```python
from .const import (
    # ... existing imports ...
    CONF_TORRENT_SERVER_URL,
)
from .stream_resolver import is_stream_playable
```

Find `handle_get_streams` (around line 271). Replace its return statement (currently `return {"streams": streams, "count": len(streams)}`) with:

```python
entry = hass.config_entries.async_get_entry(entry_id)
torrent_server_url = (
    entry.options.get(CONF_TORRENT_SERVER_URL) if entry else None
) or None

annotated = [
    {**stream, "playable": is_stream_playable(stream, torrent_server_url)}
    for stream in streams
]

return {
    "streams": annotated,
    "count": len(annotated),
}
```

- [ ] **Step 4: Run the test to confirm pass**

```bash
pytest tests/test_services.py::test_get_streams_annotates_playable_flag -v
```

Expected: PASS.

- [ ] **Step 5: Run the full services test file**

```bash
pytest tests/test_services.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add custom_components/stremio/services.py tests/test_services.py
git commit -m "feat(services): annotate get_streams response with playable flag

Each stream entry now carries a 'playable' bool derived from stream_resolver
+ the configured torrent_server_url. Picker UIs use this to grey out rows
that cannot be played given the current setup."
```

---

## Task 11: Services — new `stremio.play_stream` service

The headline service that callers (Lovelace card, Zentiahome) invoke after the user picks a stream and a device.

**Files:**
- Modify: `custom_components/stremio/services.py`
- Modify: `custom_components/stremio/services.yaml`
- Modify: `tests/test_services.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_services.py`:

```python
async def test_play_stream_resolves_and_dispatches(
    hass, mock_config_entry_setup
):
    """play_stream calls media_player.play_media and registers a session."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from custom_components.stremio.const import DOMAIN

    hass.services.async_call = AsyncMock(return_value=None)
    # Make sure the target entity exists in HA state.
    hass.states.async_set("media_player.tv", "idle")

    with patch(
        "custom_components.stremio.services._get_entry_data",
    ) as mock_get_entry:
        coordinator = MagicMock()
        coordinator.register_playback_session = MagicMock()
        client = MagicMock()
        mock_get_entry.return_value = (coordinator, client, "entry_id")

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


async def test_play_stream_raises_for_unplayable_stream(
    hass, mock_config_entry_setup
):
    """A stream entry with no URL and no torrent server -> ServiceValidationError."""
    from homeassistant.exceptions import ServiceValidationError
    from custom_components.stremio.const import DOMAIN

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "play_stream",
            {
                # No stream_url and no infoHash via the stream object means
                # we pass an empty stream_url which the service must reject.
                "stream_url": "",
                "entity_id": "media_player.tv",
                "media_id": "tt001",
                "media_type": "movie",
            },
            blocking=True,
        )
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_services.py::test_play_stream_resolves_and_dispatches -v
```

Expected: `ServiceNotFound` (service does not exist yet).

- [ ] **Step 3: Add the service handler in `services.py`**

Open `custom_components/stremio/services.py`. Add:

```python
from .const import (
    # ... existing imports ...
    SERVICE_PLAY_STREAM,
)
from .playback_manager import PlaybackManager
from .stream_resolver import StreamUnplayableError, resolve_stream_url
```

Define the service schema near the other `vol.Schema` definitions in the file:

```python
PLAY_STREAM_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_STREAM_URL): cv.string,
        vol.Required("entity_id"): cv.entity_id,
        vol.Required(ATTR_MEDIA_ID): cv.string,
        vol.Required(ATTR_MEDIA_TYPE): vol.In(["movie", "series"]),
        vol.Optional(ATTR_SEASON): cv.positive_int,
        vol.Optional(ATTR_EPISODE): cv.positive_int,
    }
)
```

Add the handler. Place near the other `handle_*` functions:

```python
async def handle_play_stream(call: ServiceCall) -> None:
    """Handle stremio.play_stream service call.

    Resolves the chosen stream URL (direct or via configured torrent
    server), dispatches media_player.play_media on the target entity,
    and registers a progress-sync session.
    """
    coordinator, _client, entry_id = _get_entry_data(hass)

    stream_url = call.data[ATTR_STREAM_URL]
    entity_id = call.data["entity_id"]
    media_id = call.data[ATTR_MEDIA_ID]
    media_type = call.data[ATTR_MEDIA_TYPE]

    # The picker hands us a URL it picked from get_streams; we still call
    # resolve_stream_url so a URL-less entry submitted by a buggy caller
    # gets a clear error instead of being silently mishandled downstream.
    entry = hass.config_entries.async_get_entry(entry_id)
    torrent_server_url = (
        entry.options.get(CONF_TORRENT_SERVER_URL) if entry else None
    ) or None

    try:
        playable_url = resolve_stream_url(
            {"url": stream_url}, torrent_server_url
        )
    except StreamUnplayableError as err:
        raise ServiceValidationError(
            str(err),
            translation_domain=DOMAIN,
            translation_key="stream_unplayable",
        ) from err

    # Build minimal media_info; the picker can pass more via attributes
    # in v2 if useful.
    media_info: dict[str, object] = {
        "type": media_type,
    }
    season = call.data.get(ATTR_SEASON)
    episode = call.data.get(ATTR_EPISODE)
    if season is not None:
        media_info["season"] = season
    if episode is not None:
        media_info["episode"] = episode

    playback_manager = PlaybackManager(hass)
    await playback_manager.play(
        entity_id=entity_id,
        stream_url=playable_url,
        media_info=media_info,
    )

    coordinator.register_playback_session(
        entity_id=entity_id,
        media_id=media_id,
        media_type=media_type,
        media_content_id=playable_url,
    )
```

In `async_setup_services` register the service. Find the existing `hass.services.async_register(DOMAIN, SERVICE_GET_STREAMS, handle_get_streams, ...)` call and add a parallel one:

```python
hass.services.async_register(
    DOMAIN,
    SERVICE_PLAY_STREAM,
    handle_play_stream,
    schema=PLAY_STREAM_SCHEMA,
)
```

And in `async_unload_services`, add:

```python
hass.services.async_remove(DOMAIN, SERVICE_PLAY_STREAM)
```

- [ ] **Step 4: Declare the service in `services.yaml`**

Open `custom_components/stremio/services.yaml`. Add to the bottom:

```yaml
play_stream:
  name: Play stream on device
  description: >
    Play a chosen Stremio stream on the named media_player entity. The
    caller (Lovelace card or external client like Zentiahome) is
    expected to have already fetched streams via get_streams and let
    the user pick one. Progress is automatically synced back to
    Stremio's continue-watching state (when enabled in options).
  fields:
    stream_url:
      name: Stream URL
      description: Direct stream URL chosen by the user (from get_streams).
      required: true
      example: "https://debrid.example.com/movie.mp4"
      selector:
        text:
    entity_id:
      name: Target media player
      description: HA media_player entity to send the stream to.
      required: true
      example: "media_player.living_room_chromecast"
      selector:
        entity:
          domain: media_player
    media_id:
      name: Media ID
      description: IMDb-style ID of the content (e.g., tt1375666).
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
    season:
      name: Season
      description: Season number (for series).
      required: false
      example: 1
      selector:
        number:
          min: 1
          max: 100
          mode: box
    episode:
      name: Episode
      description: Episode number (for series).
      required: false
      example: 1
      selector:
        number:
          min: 1
          max: 1000
          mode: box
```

- [ ] **Step 5: Run the new tests**

```bash
pytest tests/test_services.py::test_play_stream_resolves_and_dispatches tests/test_services.py::test_play_stream_raises_for_unplayable_stream -v
```

Expected: both PASS.

- [ ] **Step 6: Commit**

```bash
git add custom_components/stremio/services.py custom_components/stremio/services.yaml tests/test_services.py
git commit -m "feat(services): add stremio.play_stream service

New service the picker UI (Lovelace card / Zentiahome) calls after
the user has selected a stream and target device. Resolves the URL,
dispatches media_player.play_media, and registers a progress-sync
session on the coordinator. Validates the stream is playable; surfaces
StreamUnplayableError as ServiceValidationError with a translated
'stream_unplayable' key."
```

---

## Task 12: Translations for new error keys

Add the strings used by the new error translation_keys so HA shows them in the user's language (English fallback at minimum).

**Files:**
- Modify: `custom_components/stremio/strings.json`
- Modify: `custom_components/stremio/translations/en.json` (if it exists; check first)

- [ ] **Step 1: Check existing translation files**

```bash
ls custom_components/stremio/translations/
cat custom_components/stremio/strings.json | head -40
```

Note which files exist and which `exceptions:` or `errors:` block already lives in `strings.json`.

- [ ] **Step 2: Add the four new exception keys to `strings.json`**

Open `custom_components/stremio/strings.json`. Find the existing `exceptions:` block (or create one at the root of the JSON if it doesn't exist). Add:

```json
"exceptions": {
  "stream_unplayable": {
    "message": "This Stremio stream cannot be played. Either configure Real-Debrid (or another debrid service) in your Torrentio addon URL, or install the Stremio Server companion add-on. See the integration setup docs."
  },
  "entity_not_media_player": {
    "message": "Entity {entity_id} is not a media_player entity."
  },
  "entity_not_found": {
    "message": "Entity {entity_id} does not exist."
  },
  "entity_unavailable": {
    "message": "Entity {entity_id} is {state} and cannot be played to."
  }
}
```

If the file already has an `exceptions` object, merge these keys in. Preserve any existing keys.

- [ ] **Step 3: Mirror into `translations/en.json` if it exists**

If `custom_components/stremio/translations/en.json` exists, mirror the same `exceptions` block there. If it doesn't exist, this step is a no-op — HA will fall back to `strings.json`.

- [ ] **Step 4: Validate JSON**

```bash
python3 -c "import json; json.load(open('custom_components/stremio/strings.json'))"
python3 -c "import json, os; p='custom_components/stremio/translations/en.json'; json.load(open(p)) if os.path.exists(p) else None"
```

Expected: no output, exit 0.

- [ ] **Step 5: Commit**

```bash
git add custom_components/stremio/strings.json custom_components/stremio/translations/en.json
git commit -m "feat(i18n): add translation keys for playback errors

Adds strings for stream_unplayable, entity_not_media_player,
entity_not_found, and entity_unavailable. Used by the new
play_stream service and PlaybackManager validation."
```

(If `en.json` didn't exist, drop it from the add list.)

---

## Task 13: Apple TV handover — delegate inner `play_media` to PlaybackManager

Keep the public `stremio.handover_to_apple_tv` service intact. Internally, replace the direct `hass.services.async_call("media_player", "play_media", ...)` calls with `PlaybackManager.play()`. Verifies the existing Apple TV path keeps working through the new abstraction.

**Files:**
- Modify: `custom_components/stremio/apple_tv_handover.py`

- [ ] **Step 1: Identify the inline play_media calls to replace**

```bash
grep -n 'play_media' custom_components/stremio/apple_tv_handover.py
```

Note the lines (per `CLAUDE.md` exploration: roughly lines 539, 621). The two call sites construct service-call payloads and call `hass.services.async_call("media_player", "play_media", ...)`.

- [ ] **Step 2: Replace the inline call in the VLC handover path (~line 535-545)**

Locate the block that looks roughly like:

```python
await self.hass.services.async_call(
    "media_player",
    "play_media",
    {
        "entity_id": device_entity_id,
        "media_content_id": ...,
        "media_content_type": "video",
    },
)
```

Replace with:

```python
from .playback_manager import PlaybackManager  # local import to avoid cycle
playback = PlaybackManager(self.hass)
await playback.play(
    entity_id=device_entity_id,
    stream_url=stream_url,  # whatever variable holds the URL in this block
    media_info={"title": media_info.get("title", ""), "poster": media_info.get("poster", "")},
)
```

Preserve any surrounding logic (URL transformation for VLC scheme, etc.) before the `play` call.

- [ ] **Step 3: Replace the inline call in the AirPlay/direct handover path (~line 615-625)**

Same pattern; replace with `PlaybackManager(self.hass).play(...)`.

- [ ] **Step 4: Run the apple_tv_handover-related tests to check for regression**

The existing tests live in `tests/test_services.py` (search for `handover` and `apple_tv`). Run:

```bash
pytest tests/test_services.py -v -k "handover or apple_tv"
```

Expected: all pre-existing apple-tv handover tests still pass.

- [ ] **Step 5: Commit**

```bash
git add custom_components/stremio/apple_tv_handover.py
git commit -m "refactor(apple_tv): delegate inner play_media to PlaybackManager

Replace inline hass.services.async_call('media_player', 'play_media', ...)
calls with PlaybackManager.play(). Apple TV handover continues to do
its discovery + URL-scheme logic; only the final dispatch is delegated.
Behavior preserved; existing handover tests cover the path."
```

---

## Task 14: Config flow — torrent server URL option with auto-detect

Add the two new options (`torrent_server_url`, `progress_sync_enabled`). The options flow probes `homeassistant.local:11470` and `127.0.0.1:11470` when the URL field is empty and pre-fills it if a stremio-server responds.

**Files:**
- Modify: `custom_components/stremio/config_flow.py`
- Modify: `tests/test_config_flow.py`

- [ ] **Step 1: Add failing tests for auto-detect and option persistence**

Append to `tests/test_config_flow.py`:

```python
async def test_options_flow_includes_torrent_server_field(
    hass, mock_config_entry
) -> None:
    """The options flow surfaces torrent_server_url and progress_sync_enabled."""
    from homeassistant import config_entries
    from custom_components.stremio.const import (
        CONF_TORRENT_SERVER_URL,
        CONF_PROGRESS_SYNC_ENABLED,
    )

    result = await hass.config_entries.options.async_init(
        mock_config_entry.entry_id
    )
    schema_keys = set(result["data_schema"].schema.keys())
    field_names = {getattr(k, "schema", k) for k in schema_keys}
    assert CONF_TORRENT_SERVER_URL in field_names
    assert CONF_PROGRESS_SYNC_ENABLED in field_names


async def test_options_flow_auto_detects_local_stremio_server(
    hass, mock_config_entry, aioresponses_mock
) -> None:
    """When the URL is empty, probe finds localhost:11470 and pre-fills."""
    from custom_components.stremio.const import (
        CONF_TORRENT_SERVER_URL,
        STREMIO_SERVER_DEFAULT_PORT,
    )

    # Pretend homeassistant.local responds with 200 on the default port.
    aioresponses_mock.head(
        f"http://homeassistant.local:{STREMIO_SERVER_DEFAULT_PORT}/",
        status=200,
        repeat=True,
    )
    aioresponses_mock.head(
        f"http://127.0.0.1:{STREMIO_SERVER_DEFAULT_PORT}/",
        exception=ConnectionError(),
        repeat=True,
    )

    result = await hass.config_entries.options.async_init(
        mock_config_entry.entry_id
    )

    # The schema's default for torrent_server_url should be the discovered URL.
    schema = result["data_schema"].schema
    for key, _ in schema.items():
        if getattr(key, "schema", None) == CONF_TORRENT_SERVER_URL:
            assert key.default() == (
                f"http://homeassistant.local:{STREMIO_SERVER_DEFAULT_PORT}"
            )
            break
    else:
        pytest.fail("torrent_server_url field missing")
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_config_flow.py::test_options_flow_includes_torrent_server_field -v
```

Expected: FAIL (fields not in schema yet).

- [ ] **Step 3: Add the probe helper and extend the options flow**

Open `custom_components/stremio/config_flow.py`. Add to imports:

```python
import asyncio
from aiohttp import ClientError, ClientTimeout

from .const import (
    # ... existing imports ...
    CONF_PROGRESS_SYNC_ENABLED,
    CONF_TORRENT_SERVER_URL,
    DEFAULT_PROGRESS_SYNC_ENABLED,
    DEFAULT_TORRENT_SERVER_URL,
    STREMIO_SERVER_DEFAULT_PORT,
    STREMIO_SERVER_PROBE_HOSTS,
    STREMIO_SERVER_PROBE_TIMEOUT,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
```

Add a helper near the top of the module (after the existing helpers, before the flow classes):

```python
async def _probe_local_stremio_server(hass) -> str | None:
    """Probe well-known hosts for a running stremio-server on port 11470.

    Returns the first URL that responds with 2xx/3xx to a HEAD request,
    or None if none respond within STREMIO_SERVER_PROBE_TIMEOUT seconds.
    """
    session = async_get_clientsession(hass)
    timeout = ClientTimeout(total=STREMIO_SERVER_PROBE_TIMEOUT)

    async def _check(host: str) -> str | None:
        url = f"http://{host}:{STREMIO_SERVER_DEFAULT_PORT}"
        try:
            async with session.head(f"{url}/", timeout=timeout) as resp:
                if 200 <= resp.status < 400:
                    return url
        except (ClientError, asyncio.TimeoutError):
            pass
        return None

    results = await asyncio.gather(*(_check(h) for h in STREMIO_SERVER_PROBE_HOSTS))
    for r in results:
        if r:
            return r
    return None
```

Find the `OptionsFlow.async_step_init` (or whichever step builds the options form). Where the schema is constructed, replace/extend with:

```python
import voluptuous as vol
from homeassistant.helpers import selector

# Inside async_step_init, before building the schema:
current_url = self.config_entry.options.get(
    CONF_TORRENT_SERVER_URL, DEFAULT_TORRENT_SERVER_URL
)
discovered_url: str | None = None
if not current_url:
    discovered_url = await _probe_local_stremio_server(self.hass)

torrent_default = current_url or discovered_url or DEFAULT_TORRENT_SERVER_URL
progress_default = self.config_entry.options.get(
    CONF_PROGRESS_SYNC_ENABLED, DEFAULT_PROGRESS_SYNC_ENABLED
)

# Merge into the existing options schema dict so we don't drop other fields.
schema = vol.Schema(
    {
        # ... existing fields preserved ...
        vol.Optional(
            CONF_TORRENT_SERVER_URL, default=torrent_default
        ): selector.TextSelector(),
        vol.Optional(
            CONF_PROGRESS_SYNC_ENABLED, default=progress_default
        ): selector.BooleanSelector(),
    }
)
```

The exact merge depends on how the existing options flow builds its schema. **Preserve every existing field** — only add the two new ones.

- [ ] **Step 4: Run the new tests**

```bash
pytest tests/test_config_flow.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add custom_components/stremio/config_flow.py tests/test_config_flow.py
git commit -m "feat(config): add torrent_server_url and progress_sync_enabled options

torrent_server_url defaults to empty; when empty, the options flow
probes homeassistant.local:11470 and 127.0.0.1:11470 (with a 2s
timeout) and pre-fills the field if a stremio-server responds. The
probe runs only when the user opens the options flow with an empty
value, never at integration install or startup, so HA boot time is
unaffected."
```

---

## Task 15: Frontend picker — device selector + playable badge + new action

Extend `frontend/stremio-stream-dialog.js` so the picker shows a device dropdown, greys out unplayable rows, shows a "cached" badge when Torrentio marks streams as cached, and calls `stremio.play_stream` on submit.

No automated tests for frontend JS (the project doesn't have JS test infra). Verify manually.

**Files:**
- Modify: `custom_components/stremio/frontend/stremio-stream-dialog.js`
- Modify: `custom_components/stremio/frontend/stremio-card-bundle.js` (if it's the built artifact — check first)
- Modify: `custom_components/stremio/manifest.json` (bump version so cache busts)

- [ ] **Step 1: Read the current `stremio-stream-dialog.js`**

```bash
wc -l custom_components/stremio/frontend/stremio-stream-dialog.js
head -80 custom_components/stremio/frontend/stremio-stream-dialog.js
```

Identify (a) where the streams list is rendered, (b) where the "play" button click handler lives, (c) how/whether bundle.js gets regenerated.

- [ ] **Step 2: Add a device dropdown to the dialog state**

In the dialog's class state (Lit `@state` or `properties`), add:

```javascript
@state() _devices = [];
@state() _selectedDevice = null;
```

In `connectedCallback` (or `firstUpdated`), fetch the list of `media_player.*` entities from HA:

```javascript
async _loadDevices() {
  if (!this.hass) return;
  const entities = Object.keys(this.hass.states).filter(eid =>
    eid.startsWith("media_player.")
  );
  this._devices = entities.map(eid => ({
    entity_id: eid,
    name: this.hass.states[eid].attributes.friendly_name || eid,
  }));
  if (!this._selectedDevice && this._devices.length > 0) {
    this._selectedDevice = this._devices[0].entity_id;
  }
}
```

Call `_loadDevices()` when the dialog opens.

- [ ] **Step 3: Render the device dropdown in the template**

In the dialog's `render()` method, before the streams list (or alongside the title row), add:

```javascript
<div class="device-row">
  <label for="device-select">Play on:</label>
  <select
    id="device-select"
    .value=${this._selectedDevice || ""}
    @change=${(e) => (this._selectedDevice = e.target.value)}
  >
    ${this._devices.map(
      (d) => html`<option value=${d.entity_id}>${d.name}</option>`
    )}
  </select>
</div>
```

- [ ] **Step 4: Annotate each stream row with playable state and cached badge**

In the streams list rendering, change the row template so it shows:
- A `[CACHED]` badge if `stream.behaviorHints?.bingeGroup` or `stream.title?.toLowerCase().includes("cached")` (Torrentio convention varies; pattern-match `cached` in the title text for now).
- A muted style + "Needs torrent server" tooltip when `stream.playable === false`.

Example:

```javascript
${this._streams.map(
  (stream) => html`
    <div
      class="stream-row ${stream.playable ? "" : "unplayable"}"
      title=${stream.playable ? "" : "Needs Real-Debrid or the Stremio Server companion add-on"}
    >
      <span class="name">${stream.name || stream.title || "Stream"}</span>
      ${stream.title?.toLowerCase().includes("cached")
        ? html`<span class="badge cached">CACHED</span>`
        : ""}
      <button
        class="play-btn"
        ?disabled=${!stream.playable || !this._selectedDevice}
        @click=${() => this._handlePlay(stream)}
      >
        Play
      </button>
    </div>
  `
)}
```

Add CSS for `.stream-row.unplayable { opacity: 0.5; }` and `.badge.cached { background: #2e7d32; color: white; padding: 2px 6px; border-radius: 3px; font-size: 0.7em; }` in the dialog's existing style block.

- [ ] **Step 5: Wire `_handlePlay` to call the new service**

Add the handler method:

```javascript
async _handlePlay(stream) {
  if (!this._selectedDevice) return;
  try {
    await this.hass.callService("stremio", "play_stream", {
      stream_url: stream.url || "",
      entity_id: this._selectedDevice,
      media_id: this.mediaId,
      media_type: this.mediaType,
      ...(this.season ? { season: this.season } : {}),
      ...(this.episode ? { episode: this.episode } : {}),
    });
    this._closeDialog();
  } catch (err) {
    // Surface the error message HA returns (translated via strings.json)
    alert(`Could not play: ${err.message || err}`);
  }
}
```

`this.mediaId`, `this.mediaType`, `this.season`, `this.episode` should already be properties of the dialog component (they're how the caller tells the dialog what to fetch streams for). If they're named differently, use whatever convention the existing dialog uses.

- [ ] **Step 6: Rebuild the card bundle if needed**

Check whether `frontend/stremio-card-bundle.js` is hand-maintained or built. The existing `const.py` references only `stremio-card-bundle.js` as the registered JS module:

```bash
grep -n "card-bundle" custom_components/stremio/const.py custom_components/stremio/frontend/__init__.py
```

If `stremio-card-bundle.js` includes the dialog's source inline, copy the dialog changes into it as well (search for the same component definition inside the bundle). If the project has a build step (rollup/webpack config), run it.

- [ ] **Step 7: Bump `manifest.json` version so cache busts**

Edit `custom_components/stremio/manifest.json`, bump `version` from `0.5.37` to `0.6.0` (minor bump for new feature).

- [ ] **Step 8: Manual smoke test**

Open `docs/superpowers/specs/2026-05-17-torrentio-playback-design.md` and reread the play+sync flow. With dev HA running (`./scripts/start_homeassistant.sh`), open the dialog for a movie, confirm:

1. The device dropdown lists your media_player entities.
2. Streams without a URL show greyed out.
3. Clicking Play with a valid stream + device dispatches `stremio.play_stream` (visible in HA's developer tools → services log).

This is a manual verification step. No automated assertion possible without browser test infrastructure.

- [ ] **Step 9: Commit**

```bash
git add custom_components/stremio/frontend/stremio-stream-dialog.js \
        custom_components/stremio/frontend/stremio-card-bundle.js \
        custom_components/stremio/manifest.json
git commit -m "feat(frontend): device selector + playable/cached badges in picker

stream-dialog now: (a) loads media_player.* entities into a dropdown,
(b) greys out streams where playable=false with a helpful tooltip,
(c) shows a CACHED badge when Torrentio indicates the stream is
debrid-cached, (d) calls stremio.play_stream on click instead of
opening the URL in a browser tab. Bumps manifest version to 0.6.0
to force Lovelace resource cache busting."
```

---

## Task 16: End-to-end orchestration test

One light integration-style test that exercises `get_streams` → `play_stream` → progress sync write, all mocked. Verifies the new pieces compose correctly.

**Files:**
- Create: `tests/test_e2e_playback.py`

- [ ] **Step 1: Write the test**

Create `tests/test_e2e_playback.py`:

```python
"""End-to-end orchestration test for the new playback flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def fake_streams() -> list[dict]:
    return [
        {
            "name": "Real-Debrid 1080p",
            "title": "[CACHED] Inception.2010.1080p",
            "url": "https://debrid.example.com/inception.mp4",
        },
        {"name": "Torrentio P2P", "title": "Inception.2010.1080p", "infoHash": "abc123"},
    ]


async def test_full_playback_flow(hass, mock_config_entry_setup, fake_streams):
    """get_streams -> play_stream -> register session -> simulate state change -> progress write."""
    from custom_components.stremio.const import DOMAIN

    # Stub StremioClient.async_get_streams
    with patch(
        "custom_components.stremio.stremio_client.StremioClient.async_get_streams",
        new=AsyncMock(return_value=fake_streams),
    ):
        streams_result = await hass.services.async_call(
            DOMAIN,
            "get_streams",
            {"media_id": "tt1375666", "media_type": "movie"},
            blocking=True,
            return_response=True,
        )

    # Both annotated, one playable (direct URL), one not (no torrent server configured)
    assert streams_result["streams"][0]["playable"] is True
    assert streams_result["streams"][1]["playable"] is False

    # Now play the cached one on a fake Chromecast
    hass.services.async_call = AsyncMock(return_value=None)
    hass.states.async_set("media_player.tv", "idle")

    with patch(
        "custom_components.stremio.stremio_client.StremioClient.async_update_library_progress",
        new=AsyncMock(return_value=None),
    ) as mock_write:
        await hass.services.async_call(
            DOMAIN,
            "play_stream",
            {
                "stream_url": streams_result["streams"][0]["url"],
                "entity_id": "media_player.tv",
                "media_id": "tt1375666",
                "media_type": "movie",
            },
            blocking=True,
        )

        # play_media was dispatched
        media_player_call = [
            c for c in hass.services.async_call.call_args_list
            if c.args[:2] == ("media_player", "play_media")
        ]
        assert len(media_player_call) == 1

        # Simulate Chromecast going to paused after some seconds -> immediate write
        hass.states.async_set(
            "media_player.tv",
            "paused",
            {
                "media_content_id": "https://debrid.example.com/inception.mp4",
                "media_position": 600.0,
                "media_duration": 8400.0,
            },
        )
        # Allow the state-change event to be processed
        await hass.async_block_till_done()

    assert mock_write.await_count >= 1
    last_call = mock_write.await_args
    assert last_call.kwargs["media_id"] == "tt1375666"
    assert last_call.kwargs["position_seconds"] == 600.0
```

- [ ] **Step 2: Run the test**

```bash
pytest tests/test_e2e_playback.py -v
```

Expected: PASS. (May require adjusting fixture imports based on what `mock_config_entry_setup` looks like in the existing conftest.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_e2e_playback.py
git commit -m "test: add end-to-end playback flow test

Exercises get_streams -> play_stream -> register session -> state
change -> progress write with all external calls mocked. Catches
orchestration-level regressions the per-module tests can't see."
```

---

## Task 17: Run full test suite and fix any regressions

Before moving to the add-on repo, make sure the entire integration test suite is green.

- [ ] **Step 1: Run the whole suite**

```bash
./scripts/run_tests.sh --quick
```

Expected: all tests pass.

- [ ] **Step 2: If any tests fail**

Re-read the failure carefully. The most likely culprits:
- Existing services tests that depended on `get_streams` returning the old shape (no `playable` field) — update the test assertions to allow the extra key.
- Existing apple_tv_handover tests that mocked `hass.services.async_call` — now there's also a call from `PlaybackManager`. Adjust mocks.
- Tests that imported from `coordinator.py` and expected a specific `__init__` signature — verify nothing broke.

Fix in place, commit per regression with `fix: ...` prefix.

- [ ] **Step 3: Run linters**

```bash
./scripts/run_tests.sh
```

This runs black/flake8/mypy in addition to pytest. Fix any new lint findings in modules you touched. Do not chase lint warnings in files you did not modify.

- [ ] **Step 4: Commit any fixes from steps 2-3 individually with `fix: ...` or `style: ...` messages.**

---

## Task 18: Companion add-on — repo skeleton

Switch to the add-on repo. Initialize the directory structure HA Supervisor expects.

**Repo:** `https://github.com/Kirill23/stremio-link-conversion.git`

**Files (all new, in the add-on repo):**
- Create: `repository.yaml`
- Create: `stremio-server/config.yaml`
- Create: `stremio-server/CHANGELOG.md`
- Create: `stremio-server/README.md`
- Create: `README.md` (top-level)

- [ ] **Step 1: Clone or create the add-on repo locally**

```bash
cd ~/dev
git clone https://github.com/Kirill23/stremio-link-conversion.git
cd stremio-link-conversion
```

If the repo doesn't exist on GitHub yet:

```bash
mkdir -p ~/dev/stremio-link-conversion && cd ~/dev/stremio-link-conversion
git init
git remote add origin https://github.com/Kirill23/stremio-link-conversion.git
```

- [ ] **Step 2: Create `repository.yaml`**

```yaml
name: Kirill23 Stremio Add-ons
url: https://github.com/Kirill23/stremio-link-conversion
maintainer: Kirill <petropavlov@hotmail.com>
```

- [ ] **Step 3: Create `stremio-server/config.yaml`**

```yaml
name: Stremio Server
version: "1.0.0"
slug: stremio-server
description: >
  Runs the official stremio-server Node.js application — the same component
  Stremio's desktop app uses internally — so the hacs-stremio HACS integration
  can stream Torrentio content (and other torrent-source addons) to Chromecast,
  smart TVs, and other Home Assistant media_player entities.
arch:
  - aarch64
  - amd64
  - armv7
url: https://github.com/Kirill23/stremio-link-conversion
init: false
ports:
  "11470/tcp": 11470
ports_description:
  "11470/tcp": Stremio server HTTP API (used by hacs-stremio)
map:
  - share:rw
options:
  cache_size_gb: 4
  log_level: info
schema:
  cache_size_gb: int(1,200)
  log_level: list(debug|info|warn|error)
```

- [ ] **Step 4: Create `stremio-server/CHANGELOG.md`**

```markdown
# Changelog

## 1.0.0 — 2026-05-17

- Initial release: wraps the official stremio-server Node.js application
  as a Home Assistant Add-on. Exposes port 11470 for the hacs-stremio
  integration to use as a torrent-to-HTTP gateway.
```

- [ ] **Step 5: Create `stremio-server/README.md`**

```markdown
# Stremio Server Add-on

Runs [stremio-server](https://github.com/Stremio/stremio-shell) inside a
Home Assistant add-on container. Used by the
[hacs-stremio](https://github.com/Kirill23/hacs-stremio) integration to
convert torrent infoHashes into HTTP URLs that Chromecast and smart TVs
can play.

## Why you might need this

If your Torrentio addon is **not** configured with a debrid service
(Real-Debrid, AllDebrid, Premiumize), the streams it returns are
torrent magnets that no consumer media device can play directly. This
add-on runs a torrent-to-HTTP server locally so those streams become
playable.

If you **do** have a debrid service configured in Torrentio, you
likely don't need this add-on — debrid output is already HTTP and the
hacs-stremio integration plays it directly.

## Configuration

| Option | Default | Description |
|---|---|---|
| `cache_size_gb` | 4 | How much disk space to use for torrent piece cache. Higher = better seeking and rewatch performance, but uses more storage on the HA host. |
| `log_level` | info | One of `debug`, `info`, `warn`, `error`. |

## After installation

1. Start the add-on. Wait ~10 seconds for stremio-server to come up.
2. Open the **hacs-stremio** integration's **Configure** options.
3. The **Torrent server URL** field should be auto-detected as
   `http://homeassistant.local:11470`. If not, paste it in manually.
4. Try playing a Torrentio stream that previously failed.

## Legal note

This add-on runs a BitTorrent client. Depending on your jurisdiction
and what you stream, that may carry legal risk (DMCA notices, ISP
throttling, etc.). Use a VPN or stick to legally-distributed content
if you're unsure.
```

- [ ] **Step 6: Create top-level `README.md`**

```markdown
# Kirill23 Home Assistant Add-on Repository

Companion add-ons for the [hacs-stremio](https://github.com/Kirill23/hacs-stremio)
integration.

## Add-ons

- **[Stremio Server](stremio-server/)** — torrent-to-HTTP gateway for
  Torrentio streams.

## Adding this repository to Home Assistant

1. Home Assistant → **Settings** → **Add-ons** → **Add-on Store**.
2. Click the **⋮** menu → **Repositories**.
3. Paste: `https://github.com/Kirill23/stremio-link-conversion`
4. The add-ons in this repo now appear in the add-on store.
```

- [ ] **Step 7: Validate YAML**

```bash
python3 -c "import yaml; yaml.safe_load(open('stremio-server/config.yaml'))"
python3 -c "import yaml; yaml.safe_load(open('repository.yaml'))"
```

Expected: no output, exit 0.

- [ ] **Step 8: Commit and push the skeleton**

```bash
git add .
git commit -m "feat: initial add-on repo skeleton

Adds repository.yaml plus stremio-server/{config.yaml, README, CHANGELOG}.
Dockerfile, run.sh, and CI come in the next two tasks."
git push -u origin master  # or `main` depending on default branch
```

---

## Task 19: Companion add-on — Dockerfile and run.sh

The image actually has to start `stremio-server`. Multi-arch build (linux/amd64, linux/arm64, linux/arm/v7) is required for HAOS users on x86 PCs, Raspberry Pi 4/5, and older Pi 3 respectively.

**Files (in the add-on repo):**
- Create: `stremio-server/Dockerfile`
- Create: `stremio-server/run.sh`
- Create: `stremio-server/build.yaml`

- [ ] **Step 1: Create `stremio-server/build.yaml`**

This tells the HA build infrastructure which base images to use per architecture.

```yaml
build_from:
  aarch64: ghcr.io/home-assistant/aarch64-base:3.19
  amd64: ghcr.io/home-assistant/amd64-base:3.19
  armv7: ghcr.io/home-assistant/armv7-base:3.19
labels:
  org.opencontainers.image.title: "Stremio Server Add-on"
  org.opencontainers.image.source: "https://github.com/Kirill23/stremio-link-conversion"
  org.opencontainers.image.licenses: "MIT"
```

- [ ] **Step 2: Create `stremio-server/Dockerfile`**

```dockerfile
ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install Node.js (Alpine-based HA add-on bases)
RUN apk add --no-cache nodejs npm tini

# Install stremio-server from npm (official package by Stremio)
RUN npm install --global --production stremio-server

# Cache and config dirs (mapped to /share by config.yaml's map: share:rw)
RUN mkdir -p /share/stremio-server-cache

COPY run.sh /run.sh
RUN chmod +x /run.sh

# tini reaps zombie processes correctly when stremio-server crashes mid-stream
ENTRYPOINT ["/sbin/tini", "--"]
CMD ["/run.sh"]
```

- [ ] **Step 3: Create `stremio-server/run.sh`**

```bash
#!/usr/bin/with-contenv bashio

# Read options from the add-on's UI
CACHE_SIZE_GB=$(bashio::config 'cache_size_gb')
LOG_LEVEL=$(bashio::config 'log_level')

bashio::log.info "Starting stremio-server"
bashio::log.info "  cache size:  ${CACHE_SIZE_GB} GB"
bashio::log.info "  log level:   ${LOG_LEVEL}"
bashio::log.info "  cache dir:   /share/stremio-server-cache"
bashio::log.info "  listen port: 11470 (mapped by HA Supervisor)"

# Environment variables stremio-server respects
export APP_PATH=/share/stremio-server-cache
export NO_CORS=1
export CACHE_SIZE_BYTES=$((CACHE_SIZE_GB * 1024 * 1024 * 1024))

# Hand off PID 1 to stremio-server (tini handles signal forwarding)
exec stremio-server
```

Note: HA add-on base images ship `bashio` (config helper) and the `with-contenv` shebang via s6-overlay. If `init: false` in `config.yaml` skips s6, replace the shebang with `#!/bin/bash` and read options from `/data/options.json` using `jq` instead. The above works with the default HA bases.

- [ ] **Step 4: Validate Dockerfile builds locally (smoke test)**

If you have Docker installed locally and your host is amd64 or aarch64:

```bash
docker build \
  --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.19 \
  -t stremio-server-test \
  ./stremio-server
```

Expected: build succeeds. (If your host is a different arch, just rely on CI in the next task.)

Then a quick run smoke test:

```bash
docker run --rm -p 11470:11470 stremio-server-test &
sleep 5
curl -sI http://127.0.0.1:11470/
docker stop $(docker ps -q --filter ancestor=stremio-server-test)
```

Expected: HEAD returns `HTTP/1.1 200 OK` (or another 2xx/3xx).

- [ ] **Step 5: Commit**

```bash
git add stremio-server/Dockerfile stremio-server/run.sh stremio-server/build.yaml
git commit -m "feat: Dockerfile + run.sh for stremio-server add-on

Multi-arch build via build.yaml (aarch64 for Pi, amd64 for x86 HAOS,
armv7 for older Pi 3). Dockerfile installs Node.js + stremio-server
on the HA add-on base image. run.sh reads cache size and log level
from add-on UI options and execs stremio-server as PID 1 (via tini)."
```

---

## Task 20: Companion add-on — multi-arch CI

GitHub Actions builds the multi-arch image on tag pushes and publishes it. This is what makes the add-on installable on Raspberry Pi without users having to build the image themselves.

**Files (in the add-on repo):**
- Create: `.github/workflows/build.yml`

- [ ] **Step 1: Create the workflow**

`.github/workflows/build.yml`:

```yaml
name: Build add-on images

on:
  push:
    branches: [master, main]
    tags: ["v*"]
  pull_request:

jobs:
  build:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        arch: [aarch64, amd64, armv7]
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up QEMU
        uses: docker/setup-qemu-action@v3

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Build (no push for PRs)
        uses: home-assistant/builder@2024.03.5
        with:
          args: |
            --${{ matrix.arch }} \
            --target stremio-server \
            --test
```

(The `home-assistant/builder` action wraps `docker buildx` with HA's add-on conventions; `--test` builds without pushing. For tag-based publishing to a registry, extend with credentials later — out of scope for v1.)

- [ ] **Step 2: Commit and push**

```bash
git add .github/workflows/build.yml
mkdir -p .github/workflows  # safety in case parent dirs don't exist
git commit -m "ci: multi-arch add-on builds on push/PR

Uses home-assistant/builder action to build the stremio-server add-on
for aarch64, amd64, and armv7. Test-only (no push) for now; release
workflow with registry credentials is a follow-up."
git push
```

- [ ] **Step 3: Verify CI green**

After pushing, open `https://github.com/Kirill23/stremio-link-conversion/actions` and confirm the three matrix jobs all complete successfully.

- [ ] **Step 4: End-to-end manual verification on a real Raspberry Pi (or HAOS VM)**

This is a manual integration test for the whole feature:

1. On a real HAOS-on-Pi instance (or HAOS-in-VM), open Settings → Add-ons → Add-on Store → ⋮ → Repositories → paste `https://github.com/Kirill23/stremio-link-conversion` → Add.
2. Install the **Stremio Server** add-on. Start it.
3. Verify port 11470 responds: SSH into HAOS and `curl http://127.0.0.1:11470/` should return 200.
4. Install the hacs-stremio integration from `https://github.com/Kirill23/hacs-stremio`.
5. Configure it with a Stremio account that has Torrentio installed (without debrid).
6. Open the picker for any movie. Verify (a) the device dropdown lists your Chromecast, (b) Torrentio streams are no longer greyed out (because the integration auto-detected the local stremio-server), (c) pressing Play actually streams to the Chromecast.

This test verifies the full debrid-less flow end-to-end. If it works, both repos are ready for tagged releases.

---

## Self-review

After completing all tasks, the implementing engineer should run the spec coverage check:

- [ ] Every locked-in decision in the spec has a corresponding task above.
- [ ] No "TODO" / "TBD" left in any plan step.
- [ ] All test code in this plan was actually copy-pasted into the relevant test files (not paraphrased).
- [ ] Frequent commits — each task ends with a commit; nothing was bundled.
- [ ] Manual verification was actually performed for Task 15 (frontend) and Task 20 (end-to-end on Pi).

If anything fails this check, file a follow-up task and update the plan rather than soldiering on. The plan is a living document for this feature; keep it honest.
