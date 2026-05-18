"""The Stremio integration for Home Assistant.

This integration connects to Stremio and provides:
- Media player control
- Library sensors
- Continue watching tracking
- Apple TV handover support
- Custom Lovelace cards
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import CoreState, EVENT_HOMEASSISTANT_STARTED, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import StremioDataUpdateCoordinator
from .frontend import JSModuleRegistration
from .services import async_setup_services, async_unload_services
from .stremio_client import StremioAuthError, StremioClient, StremioConnectionError

_LOGGER = logging.getLogger(__name__)

# This integration is config entry only - no YAML configuration
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.MEDIA_PLAYER,
]


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Register frontend modules after Home Assistant startup.

    Args:
        hass: Home Assistant instance
    """
    module_register = JSModuleRegistration(hass)
    await module_register.async_register()
    _LOGGER.info("Stremio frontend resources registered")


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Stremio component.

    Args:
        hass: Home Assistant instance
        config: Configuration dict

    Returns:
        True if setup was successful
    """
    hass.data.setdefault(DOMAIN, {})

    async def _setup_frontend(_event: object = None) -> None:
        """Set up frontend after HA is started."""
        await _async_register_frontend(hass)

    # If HA is already running, register immediately
    if hass.state == CoreState.running:
        await _setup_frontend()
    else:
        # Otherwise, wait for STARTED event
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _setup_frontend)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Stremio from a config entry.

    Args:
        hass: Home Assistant instance
        entry: Config entry to set up

    Returns:
        True if setup was successful

    Raises:
        ConfigEntryAuthFailed: When authentication fails
        ConfigEntryNotReady: When connection fails
    """
    _LOGGER.info("Setting up Stremio integration for %s", entry.unique_id)

    # Get credentials from config entry
    email = entry.data[CONF_EMAIL]
    password = entry.data[CONF_PASSWORD]

    # Get shared aiohttp session from Home Assistant
    session = async_get_clientsession(hass)

    # Initialize Stremio client with shared session
    client = StremioClient(email=email, password=password, session=session)

    try:
        # Test authentication
        await client.async_authenticate()
        _LOGGER.info("Successfully authenticated with Stremio")

    except StremioAuthError as err:
        _LOGGER.error("Authentication failed: %s", err)
        raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err

    except StremioConnectionError as err:
        _LOGGER.warning("Connection failed, will retry: %s", err)
        raise ConfigEntryNotReady(f"Connection failed: {err}") from err

    except Exception as err:
        _LOGGER.exception("Unexpected error during setup: %s", err)
        raise ConfigEntryNotReady(f"Unexpected error: {err}") from err

    # Create coordinator
    coordinator = StremioDataUpdateCoordinator(
        hass=hass,
        client=client,
        entry=entry,
    )

    # Perform initial data fetch
    await coordinator.async_config_entry_first_refresh()

    # Start progress sync listener (idempotent; respects options flag)
    coordinator.start_progress_sync()

    # Store coordinator and client in hass.data
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "client": client,
    }

    # Forward the entry to platform setup
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Set up services (only once for the first entry)
    if len(hass.data[DOMAIN]) == 1:
        await async_setup_services(hass)

    # Register update listener for options flow
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    _LOGGER.info("Stremio integration setup complete")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry.

    Args:
        hass: Home Assistant instance
        entry: Config entry to unload

    Returns:
        True if unload was successful
    """
    _LOGGER.info("Unloading Stremio integration for %s", entry.unique_id)

    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Remove data from hass.data and cleanup client
    if unload_ok:
        coordinator = hass.data[DOMAIN].get(entry.entry_id, {}).get("coordinator")
        if coordinator:
            coordinator.stop_progress_sync()
        data = hass.data[DOMAIN].pop(entry.entry_id)

        # Close the client to cleanup any resources
        client = data.get("client")
        if client:
            await client.async_close()

        # Unload services if no more entries
        if not hass.data[DOMAIN]:
            await async_unload_services(hass)

    return unload_ok


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
        "Migrating Stremio config entry %s to v2 "
        "(removing Apple-TV-specific options)",
        entry.entry_id,
    )

    legacy = _APPLE_TV_LEGACY_KEYS
    new_data = {k: v for k, v in entry.data.items() if k not in legacy}
    new_options = {k: v for k, v in entry.options.items() if k not in legacy}

    hass.config_entries.async_update_entry(
        entry, data=new_data, options=new_options, version=2
    )
    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update for config entry.

    This method is called when the user updates options through the UI.
    Instead of fully reloading, we update the coordinator with new options.

    Args:
        hass: Home Assistant instance
        entry: Config entry with updated options
    """
    _LOGGER.info("Updating Stremio integration options for %s", entry.unique_id)

    # Get the coordinator and update its options
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if data:
        coordinator = data.get("coordinator")
        if coordinator and hasattr(coordinator, "update_options"):
            coordinator.update_options(entry)
            _LOGGER.debug("Coordinator options updated successfully")
            return

    # If we couldn't update dynamically, fall back to full reload
    _LOGGER.debug("Falling back to full reload")
    await hass.config_entries.async_reload(entry.entry_id)
