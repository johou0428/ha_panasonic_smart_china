"""Fan platform for Panasonic Smart China air cleaner devices."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval

from .api import PanasonicApiAuthError, PanasonicApiClient, PanasonicApiError
from .const import (
    CONF_CATEGORY,
    CONF_CONTROLLER_MODEL,
    CONF_DEVICE_ID,
    CONF_DEVICE_MODEL,
    CONF_DEVICE_NAME,
    CONF_DEVICES,
    CONF_ENABLED,
    CONF_PROFILE_ID,
    CONF_SSID,
    CONF_TOKEN,
    CONF_USR_ID,
    DOMAIN,
)
from .models import ENTITY_KIND_AIRCLEANER, PLATFORM_FAN
from .profiles import find_profile_for_device_config

_LOGGER = logging.getLogger(__name__)

POLLING_INTERVAL = timedelta(seconds=15)


def _as_int(value, default=None):
    """Best-effort int conversion for Panasonic status fields."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


async def async_setup_entry(hass, entry, async_add_entities):
    """Create fan entities for enabled devices under an account entry."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    client = runtime.get("client") or PanasonicApiClient(hass, entry.data.get(CONF_SSID))
    account_device_id = runtime.get("account_device_id")
    devices = entry.data.get(CONF_DEVICES, {})

    entities = []
    for device_id, device_config in devices.items():
        if not device_config.get(CONF_ENABLED, True):
            continue

        profile = find_profile_for_device_config(
            profile_id=device_config.get(CONF_PROFILE_ID),
            controller_model=device_config.get(CONF_CONTROLLER_MODEL),
            category_id=device_config.get(CONF_CATEGORY),
        )
        if not profile:
            _LOGGER.error("Device profile not found for %s.", device_id)
            continue

        if PLATFORM_FAN not in profile.ha_platforms:
            continue

        entity_class = FAN_ENTITY_CLASSES.get(profile.entity_kind)
        if not entity_class:
            _LOGGER.error(
                "Fan entity kind %s not implemented for %s.",
                profile.entity_kind,
                device_id,
            )
            continue

        entity_config = {
            **entry.data,
            **device_config,
            CONF_DEVICE_ID: device_id,
        }
        entities.append(
            entity_class(
                hass,
                entry,
                entity_config,
                device_config.get(CONF_DEVICE_NAME, device_id),
                profile,
                client,
                account_device_id,
            )
        )

    async_add_entities(entities, update_before_add=True)


class PanasonicAirCleanerFan(FanEntity):
    """松下空气净化器实体 — 支持开关与风量档位（自动/静音/中速/高速）控制。"""

    def __init__(self, hass, entry, config, name, profile, client, account_device_id):
        self._hass = hass
        self._entry = entry
        self._usr_id = config[CONF_USR_ID]
        self._device_id = config[CONF_DEVICE_ID]
        self._token = config[CONF_TOKEN]
        self._model = config.get(CONF_DEVICE_MODEL) or config.get(CONF_CONTROLLER_MODEL)
        self._api = client
        self._account_device_id = account_device_id
        self._attr_name = name
        self._attr_unique_id = f"panasonic_smart_china_{self._device_id}_fan"

        # preset name -> airVo value reported by the cloud API
        self._profile = profile
        self._preset_map = profile.fan_mapping
        self._attr_preset_modes = list(profile.fan_mapping.keys())

        self._available = False
        self._is_on = False
        self._preset_mode = None
        self._attributes = {}
        self._last_params = {}
        self._unsub_polling = None

    # --- polling ---

    @property
    def should_poll(self):
        return False

    @property
    def available(self):
        return self._available

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._unsub_polling = async_track_time_interval(
            self._hass, self._async_update_interval_wrapper, POLLING_INTERVAL
        )

    async def async_will_remove_from_hass(self):
        if self._unsub_polling:
            self._unsub_polling()
            self._unsub_polling = None
        await super().async_will_remove_from_hass()

    async def _async_update_interval_wrapper(self, now):
        await self.async_update()
        self.async_write_ha_state()

    # --- entity info ---

    @property
    def device_info(self):
        device_info = DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=self._attr_name,
            manufacturer="Panasonic",
            model=self._model,
        )
        if self._account_device_id:
            device_info["via_device_id"] = self._account_device_id
        return device_info

    @property
    def supported_features(self):
        return (
            FanEntityFeature.TURN_ON
            | FanEntityFeature.TURN_OFF
            | FanEntityFeature.PRESET_MODE
        )

    # --- state ---

    @property
    def is_on(self):
        return self._is_on

    @property
    def preset_mode(self):
        return self._preset_mode

    @property
    def extra_state_attributes(self):
        return self._attributes

    async def async_update(self):
        await self._fetch_status()

    async def _fetch_status(self):
        """Fetch the latest cloud status for this air cleaner."""
        try:
            res = await self._api.get_device_status(
                self._profile,
                self._usr_id,
                self._device_id,
                self._token,
            )
        except PanasonicApiAuthError as err:
            self._available = False
            _LOGGER.error("Panasonic session expired for %s: %s", self._device_id, err)
            raise ConfigEntryAuthFailed("Panasonic Smart China session expired") from err
        except PanasonicApiError as err:
            self._available = False
            _LOGGER.debug("Fetch status failed for %s: %s", self._device_id, err)
            return None

        self._last_params = res.copy()
        self._available = True
        self._update_local_state(res)
        return res

    def _update_local_state(self, res):
        """Map the Panasonic status payload onto entity state."""
        self._is_on = _as_int(res.get("runSta")) == 1

        air_volume = _as_int(res.get("airVo"))
        self._preset_mode = None
        for preset, value in self._preset_map.items():
            if value == air_volume:
                self._preset_mode = preset
                break

        self._attributes = {
            "pm25": _as_int(res.get("pm25C")),
            "pm25_level": _as_int(res.get("pm25Level")),
            "oxygen_content": _as_int(res.get("oxyC")),
            "oxygen_level": _as_int(res.get("oxyLevel")),
            "gas_conc": _as_int(res.get("gasC")),
            "dust": _as_int(res.get("dust")),
            "nanoe": _as_int(res.get("nanoe")),
            "allergen_level": _as_int(res.get("allergenC")),
            "filter_life": _as_int(res.get("filterTL")),
            "filter_remind": _as_int(res.get("fliterRemind")),
            "child_lock": _as_int(res.get("childLock")),
            "auto_care": _as_int(res.get("autoCare")),
        }

    # --- commands ---

    async def _send_command(self, changes):
        """Read-Modify-Write a control change to the cloud."""
        latest_params = await self._fetch_status()
        if latest_params is None:
            _LOGGER.warning(
                "Could not fetch latest status for %s; aborting command %s.",
                self._device_id,
                changes,
            )
            return

        current_params = latest_params.copy()
        current_params.update(changes)
        safe_keys = self._profile.safe_status_keys
        params = {k: v for k, v in current_params.items() if k in safe_keys}
        # Never let the safe-key filter drop the values we intend to change.
        params.update(changes)

        try:
            await self._api.set_device_status(
                self._profile,
                self._usr_id,
                self._device_id,
                self._token,
                params,
            )
        except PanasonicApiAuthError as err:
            self._available = False
            _LOGGER.error(
                "Panasonic session expired while setting %s: %s", self._device_id, err
            )
            raise ConfigEntryAuthFailed("Panasonic Smart China session expired") from err
        except PanasonicApiError as err:
            _LOGGER.error("Set failed for %s: %s", self._device_id, err)
            return

        self._available = True
        self._last_params.update(params)
        self._update_local_state(self._last_params)
        self.async_write_ha_state()

    async def async_turn_on(self, percentage=None, preset_mode=None, **kwargs):
        """Turn the air cleaner on.

        NOTE: Home Assistant may invoke this positionally during service
        handling (async_turn_on(percentage, preset_mode)), so the parameters
        must be explicit rather than absorbed by **kwargs.
        """
        changes = {"runSta": 1}
        if preset_mode in self._preset_map:
            changes["airVo"] = self._preset_map[preset_mode]
        await self._send_command(changes)

    async def async_turn_off(self, **kwargs):
        await self._send_command({"runSta": 0})

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        value = self._preset_map.get(preset_mode)
        if value is None:
            _LOGGER.warning(
                "Unsupported preset mode %s for %s", preset_mode, self._device_id
            )
            return
        await self._send_command({"airVo": value, "runSta": 1})


FAN_ENTITY_CLASSES = {
    ENTITY_KIND_AIRCLEANER: PanasonicAirCleanerFan,
}
