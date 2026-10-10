"""Switch platform for Panasonic Smart China air cleaner devices.

Only fields verified as writable on real hardware are exposed here:
nanoe, childLock, lcdSwitch, autoCareRun and sensiOxy.

Read-only sensor values (dust, gas, oxyLevel) are deliberately NOT exposed as
switches even though the cloud accepts the write.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import PanasonicApiAuthError, PanasonicApiClient, PanasonicApiError
from .const import (
    CONF_CATEGORY,
    CONF_CONTROLLER_MODEL,
    CONF_DEVICE_MODEL,
    CONF_DEVICE_NAME,
    CONF_DEVICES,
    CONF_ENABLED,
    CONF_PROFILE_ID,
    CONF_SSID,
    CONF_TOKEN,
    DOMAIN,
)
from .coordinator import async_get_device_coordinator
from .models import ENTITY_KIND_AIRCLEANER, PLATFORM_SWITCH
from .profiles import find_profile_for_device_config

_LOGGER = logging.getLogger(__name__)


def _as_int(value: Any) -> int | None:
    """Best-effort int conversion for Panasonic status fields."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


AIRCLEANER_SWITCHES: tuple[SwitchEntityDescription, ...] = (
    SwitchEntityDescription(key="nanoe", name="nanoeX", icon="mdi:atom"),
    SwitchEntityDescription(key="childLock", name="童锁", icon="mdi:lock"),
    SwitchEntityDescription(key="lcdSwitch", name="显示屏", icon="mdi:monitor"),
    SwitchEntityDescription(
        key="autoCareRun", name="自动托管", icon="mdi:auto-fix"
    ),
    SwitchEntityDescription(
        key="sensiOxy", name="氧气灵敏度", icon="mdi:molecule-oxygen"
    ),
)

SWITCH_DESCRIPTIONS: dict[str, tuple[SwitchEntityDescription, ...]] = {
    ENTITY_KIND_AIRCLEANER: AIRCLEANER_SWITCHES,
}


class PanasonicSwitch(CoordinatorEntity, SwitchEntity):
    """A writable option of a Panasonic air cleaner."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        description: SwitchEntityDescription,
        device_id: str,
        device_name: str,
        model: str | None,
        account_device_id: str | None,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = (
            f"panasonic_smart_china_{device_id}_switch_{description.key}"
        )
        device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=device_name,
            manufacturer="Panasonic",
            model=model,
        )
        if account_device_id:
            device_info["via_device_id"] = account_device_id
        self._attr_device_info = device_info

    @property
    def is_on(self) -> bool | None:
        """Return True when the underlying status field is 1."""
        data = self.coordinator.data or {}
        value = _as_int(data.get(self.entity_description.key))
        if value is None:
            return None
        return value == 1

    async def async_turn_on(self, **kwargs) -> None:
        await self._async_set_value(1)

    async def async_turn_off(self, **kwargs) -> None:
        await self._async_set_value(0)

    async def _async_set_value(self, value: int) -> None:
        """Send the change using the same Read-Modify-Write flow as fan.py."""
        coordinator = self.coordinator
        key = self.entity_description.key

        current = dict(coordinator.data or {})
        current[key] = value
        safe_keys = coordinator.profile.safe_status_keys
        payload = {k: v for k, v in current.items() if k in safe_keys}
        # Never let the safe-key filter drop the value we intend to change.
        payload[key] = value

        try:
            await coordinator.client.set_device_status(
                coordinator.profile,
                coordinator.usr_id,
                coordinator.device_id,
                coordinator.token,
                payload,
            )
        except PanasonicApiAuthError as err:
            raise ConfigEntryAuthFailed(
                "Panasonic Smart China session expired"
            ) from err
        except PanasonicApiError as err:
            _LOGGER.error(
                "Set %s failed for %s: %s", key, coordinator.device_id, err
            )
            return

        # Refresh immediately so the UI shows the real device state.
        await coordinator.async_refresh()


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities):
    """Create switch entities for enabled devices under an account entry."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    client = runtime.get("client") or PanasonicApiClient(
        hass, entry.data.get(CONF_SSID)
    )
    account_device_id = runtime.get("account_device_id")
    devices = entry.data.get(CONF_DEVICES, {})

    entities: list[PanasonicSwitch] = []
    for device_id, device_config in devices.items():
        if not device_config.get(CONF_ENABLED, True):
            continue

        profile = find_profile_for_device_config(
            profile_id=device_config.get(CONF_PROFILE_ID),
            controller_model=device_config.get(CONF_CONTROLLER_MODEL),
            category_id=device_config.get(CONF_CATEGORY),
        )
        if not profile:
            continue
        if PLATFORM_SWITCH not in profile.ha_platforms:
            continue

        descriptions = SWITCH_DESCRIPTIONS.get(profile.entity_kind)
        if not descriptions:
            continue

        coordinator = async_get_device_coordinator(
            hass, entry, client, profile, device_id, device_config[CONF_TOKEN]
        )
        if coordinator.data is None:
            await coordinator.async_refresh()

        device_name = device_config.get(CONF_DEVICE_NAME, device_id)
        model = (
            device_config.get(CONF_DEVICE_MODEL)
            or device_config.get(CONF_CONTROLLER_MODEL)
        )
        entities.extend(
            PanasonicSwitch(
                coordinator,
                description,
                device_id,
                device_name,
                model,
                account_device_id,
            )
            for description in descriptions
        )

    async_add_entities(entities)
