"""Config flow for Stremio integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol
from aiohttp import ClientError, ClientTimeout
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_ADDON_STREAM_ORDER,
    CONF_AUTH_KEY,
    CONF_DEFAULT_CATALOG_SOURCE,
    CONF_LIBRARY_SCAN_INTERVAL,
    CONF_PLAYER_SCAN_INTERVAL,
    CONF_POLLING_GATE_ENTITIES,
    CONF_PROGRESS_SYNC_ENABLED,
    CONF_RESET_ADDON_ORDER,
    CONF_SHOW_COPY_URL,
    CONF_STREAM_QUALITY_PREFERENCE,
    CONF_TORRENT_SERVER_URL,
    DEFAULT_ADDON_STREAM_ORDER,
    DEFAULT_CATALOG_SOURCE,
    DEFAULT_LIBRARY_SCAN_INTERVAL,
    DEFAULT_PLAYER_SCAN_INTERVAL,
    DEFAULT_POLLING_GATE_ENTITIES,
    DEFAULT_PROGRESS_SYNC_ENABLED,
    DEFAULT_SHOW_COPY_URL,
    DEFAULT_STREAM_QUALITY_PREFERENCE,
    DEFAULT_TORRENT_SERVER_URL,
    DOMAIN,
    STREMIO_SERVER_DEFAULT_PORT,
    STREMIO_SERVER_PROBE_HOSTS,
    STREMIO_SERVER_PROBE_TIMEOUT,
    STREAM_QUALITY_OPTIONS,
)
from .dashboard_helper import async_create_testing_dashboard
from .stremio_client import StremioAuthError, StremioClient, StremioConnectionError

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    email = data[CONF_EMAIL]
    password = data[CONF_PASSWORD]

    client = StremioClient(email, password)
    try:
        auth_key = await client.async_authenticate()

        if not auth_key:
            raise InvalidAuth("Authentication failed - no auth key received")

        # Validate the auth key by fetching user profile
        user = await client.async_get_user()

        if not user or not user.get("email"):
            raise InvalidAuth("Failed to fetch user profile")

        return {
            "title": user["email"],
            CONF_AUTH_KEY: auth_key,
            CONF_EMAIL: email,
        }

    except StremioAuthError as err:
        _LOGGER.error("Authentication failed: %s", err)
        raise InvalidAuth from err
    except StremioConnectionError as err:
        _LOGGER.error("Connection failed: %s", err)
        raise CannotConnect from err
    except Exception as err:
        _LOGGER.exception("Unexpected error during authentication: %s", err)
        raise CannotConnect from err
    finally:
        await client.async_close()


async def _probe_local_stremio_server(hass: HomeAssistant) -> str | None:
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
        except (ClientError, asyncio.TimeoutError, OSError):
            pass
        return None

    results = await asyncio.gather(*(_check(h) for h in STREMIO_SERVER_PROBE_HOSTS))
    for r in results:
        if r:
            return r
    return None


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
    """Handle a config flow for Stremio."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlowHandler:
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                # Set unique ID based on email to prevent duplicate entries
                await self.async_set_unique_id(user_input[CONF_EMAIL])
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=info["title"],
                    data={
                        CONF_AUTH_KEY: info[CONF_AUTH_KEY],
                        CONF_EMAIL: info[CONF_EMAIL],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Stremio integration."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        self._pending_options: dict[str, Any] = {}
        # Available addons fetched from API
        self._available_addons: list[dict[str, Any]] = []
        self._addon_names: list[str] = []
        # Track if user wants to create testing dashboard
        self._create_dashboard: bool = False

    async def _fetch_available_addons(self) -> None:
        """Fetch available addons from the Stremio API."""
        try:
            # Access the client from hass.data
            entry_data = self.hass.data.get(DOMAIN, {}).get(
                self._config_entry.entry_id, {}
            )
            client = entry_data.get("client")

            if client:
                # Get addon collection from client
                addon_collection = await client.async_get_addon_collection()
                if addon_collection:
                    self._available_addons = addon_collection
                    # Extract addon names (use transportName or manifest.name)
                    self._addon_names = []
                    for addon in addon_collection:
                        manifest = addon.get("manifest", {})
                        name = addon.get("transportName") or manifest.get("name", "")
                        if name:
                            self._addon_names.append(name)
                    _LOGGER.debug(
                        "Fetched %d addons: %s",
                        len(self._addon_names),
                        self._addon_names,
                    )
        except Exception as err:
            _LOGGER.warning("Failed to fetch addons: %s", err)
            self._available_addons = []
            self._addon_names = []

    def _build_addon_selector_options(
        self,
    ) -> list[selector.SelectOptionDict]:
        """Build options list for the addon selector."""
        options: list[selector.SelectOptionDict] = []
        seen: set[str] = set()

        for addon in self._available_addons:
            manifest = addon.get("manifest", {})
            addon_id = manifest.get("id", "")
            name = addon.get("transportName") or manifest.get("name", "")
            version = manifest.get("version", "")
            description = manifest.get("description", "")

            # Skip duplicates and empty names
            if not name or name in seen:
                continue
            seen.add(name)

            # Build display label
            label = name
            if version:
                label = f"{name} (v{version})"

            options.append(
                selector.SelectOptionDict(
                    value=name,
                    label=label,
                )
            )

        return options

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options - step 1: basic settings."""
        errors: dict[str, str] = {}

        # Fetch available addons if not already loaded
        if not self._available_addons:
            await self._fetch_available_addons()

        # Auto-detect local stremio server when URL not yet configured
        current_url = self._config_entry.options.get(
            CONF_TORRENT_SERVER_URL, DEFAULT_TORRENT_SERVER_URL
        )
        discovered_url: str | None = None
        if not current_url and user_input is None:
            discovered_url = await _probe_local_stremio_server(self.hass)
        torrent_default = current_url or discovered_url or DEFAULT_TORRENT_SERVER_URL
        progress_default = self._config_entry.options.get(
            CONF_PROGRESS_SYNC_ENABLED, DEFAULT_PROGRESS_SYNC_ENABLED
        )

        if user_input is not None:
            # Store the options for later
            self._pending_options = user_input

            # Handle reset addon order checkbox
            reset_order = user_input.pop(CONF_RESET_ADDON_ORDER, False)
            if reset_order:
                self._pending_options[CONF_ADDON_STREAM_ORDER] = []
            else:
                # Handle empty selection which may be omitted from user_input
                if CONF_ADDON_STREAM_ORDER not in user_input:
                    self._pending_options[CONF_ADDON_STREAM_ORDER] = []

            # Check if user wants to create testing dashboard
            self._create_dashboard = user_input.pop("create_testing_dashboard", False)

            return self._create_options_entry()

        # Build addon options for the selector
        addon_options = self._build_addon_selector_options()

        # Get current addon order preference
        current_order = self._config_entry.options.get(
            CONF_ADDON_STREAM_ORDER, DEFAULT_ADDON_STREAM_ORDER
        )
        # Handle legacy string format (convert to list if needed)
        if isinstance(current_order, str):
            current_order = [
                name.strip() for name in current_order.split("\n") if name.strip()
            ]

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_PLAYER_SCAN_INTERVAL,
                        default=self._config_entry.options.get(
                            CONF_PLAYER_SCAN_INTERVAL, DEFAULT_PLAYER_SCAN_INTERVAL
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=10, max=300)),
                    vol.Optional(
                        CONF_LIBRARY_SCAN_INTERVAL,
                        default=self._config_entry.options.get(
                            CONF_LIBRARY_SCAN_INTERVAL, DEFAULT_LIBRARY_SCAN_INTERVAL
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=60, max=3600)),
                    vol.Optional(
                        CONF_POLLING_GATE_ENTITIES,
                        default=self._config_entry.options.get(
                            CONF_POLLING_GATE_ENTITIES, DEFAULT_POLLING_GATE_ENTITIES
                        ),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain=[
                                "media_player",
                                "binary_sensor",
                                "switch",
                                "input_boolean",
                            ],
                            multiple=True,
                        ),
                    ),
                    vol.Optional(
                        CONF_SHOW_COPY_URL,
                        default=self._config_entry.options.get(
                            CONF_SHOW_COPY_URL, DEFAULT_SHOW_COPY_URL
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_DEFAULT_CATALOG_SOURCE,
                        default=self._config_entry.options.get(
                            CONF_DEFAULT_CATALOG_SOURCE, DEFAULT_CATALOG_SOURCE
                        ),
                    ): str,
                    vol.Optional(
                        CONF_ADDON_STREAM_ORDER,
                        default=current_order,
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=addon_options,
                            multiple=True,
                            mode=selector.SelectSelectorMode.LIST,
                            sort=False,  # Preserve user's selection order
                        ),
                    ),
                    vol.Optional(
                        CONF_RESET_ADDON_ORDER,
                        default=False,
                    ): bool,
                    vol.Optional(
                        CONF_STREAM_QUALITY_PREFERENCE,
                        default=self._config_entry.options.get(
                            CONF_STREAM_QUALITY_PREFERENCE,
                            DEFAULT_STREAM_QUALITY_PREFERENCE,
                        ),
                    ): vol.In(STREAM_QUALITY_OPTIONS),
                    vol.Optional(
                        "create_testing_dashboard",
                        default=False,
                    ): bool,
                    vol.Optional(
                        CONF_TORRENT_SERVER_URL,
                        default=torrent_default,
                    ): selector.TextSelector(),
                    vol.Optional(
                        CONF_PROGRESS_SYNC_ENABLED,
                        default=progress_default,
                    ): selector.BooleanSelector(),
                }
            ),
            errors=errors,
            description_placeholders={
                "dashboard_info": "Creates a comprehensive testing dashboard with all Stremio card types"
            },
        )

    def _create_options_entry(self) -> FlowResult:
        """Create the options entry with all collected data."""
        # If user requested dashboard creation, do it now
        if self._create_dashboard:
            # Try to find the media player entity
            try:
                from homeassistant.helpers import entity_registry as er

                entity_registry = er.async_get(self.hass)
                entity_id = None

                for entity in entity_registry.entities.values():
                    if (
                        entity.config_entry_id == self._config_entry.entry_id
                        and entity.domain == "media_player"
                    ):
                        entity_id = entity.entity_id
                        break

                if entity_id:
                    _LOGGER.info("Creating testing dashboard for entity: %s", entity_id)
                    # Schedule the dashboard creation
                    self.hass.async_create_task(
                        async_create_testing_dashboard(self.hass, entity_id)
                    )
                else:
                    _LOGGER.warning(
                        "Could not find media player entity ID for dashboard creation"
                    )
            except Exception as err:
                _LOGGER.error("Error setting up dashboard creation: %s", err)

        return self.async_create_entry(title="", data=self._pending_options)
