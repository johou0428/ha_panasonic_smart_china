"""Sensor platform for Panasonic Smart China air cleaner devices.

All sensors of one device share a single DataUpdateCoordinator, so a device is
polled once per interval regardless of how many sensors it exposes.
"""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    PERCENTAGE,
)
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

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
from .models import ENTITY_KIND_AIRCLEANER, PLATFORM_SENSOR
from .profiles import find_profile_for_device_config

_LOGGER = logging.getLogger(__name__)

# Air quality values change slowly, so sensors refresh less often than the fan
# entity (the fan needs to react quickly to commands).
SENSOR_UPDATE_INTERVAL = timedelta(seconds=60)


def _as_int(value: Any) -> int | None:
    """Best-effort int conversion for Panasonic status fields."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


AIRCLEANER_SENSORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="pm25C",
        name="PM2.5 浓度",
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        device_class=SensorDeviceClass.PM25,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:air-filter",
    ),
    SensorEntityDescription(
        key="pm25Level",
        name="PM2.5 等级",
        icon="mdi:air-filter",
    ),
    SensorEntityDescription(
        key="filterTL",
        name="滤网寿命",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:progress-clock",
    ),
    SensorEntityDescription(
        key="fliterRemind",
        name="滤网提醒",
        icon="mdi:filter-variant-remove",
    ),
    SensorEntityDescription(
        key="oxyC",
        name="氧气含量",
        icon="mdi:molecule-oxygen",
    ),
    SensorEntityDescription(
        key="oxyLevel",
        name="氧气等级",
        icon="mdi:molecule-oxygen",
    ),
    SensorEntityDescription(
        key="gasC",
        name="异味",
        icon="mdi:smog",
    ),
    SensorEntityDescription(
        key="dust",
        name="灰尘",
        icon="mdi:weather-dust",
    ),
    SensorEntityDescription(
        key="allergenC",
        name="过敏原等级",
        icon="mdi:flower-pollen",
    ),
    SensorEntityDescription(
        key="nanoe",
        name="nanoeX",
        icon="mdi:atom",
    ),
    SensorEntityDescription(
        key="childLock",
        name="童锁",
        icon="mdi:lock",
    ),
    SensorEntityDescription(
        key="autoCare",
        name="自动托管",
        icon="mdi:auto-fix",
    ),
)

SENSOR_DESCRIPTIONS: dict[str, tuple[SensorEntityDescription, ...]] = {
    ENTITY_KIND_AIRCLEANER: AIRCLEANER_SENSORS,
}


class PanasonicDeviceCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll one device once and share its status with every sensor entity."""

    def __init__(
        self,
        hass,
        client: PanasonicApiClient,
        profile,
        usr_id: str,
        device_id: str,
        token: str,
    ) -> None:
        self.client = client
        self.profile = profile
        self.usr_id = usr_id
        self.device_id = device_id
        self.token = token
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{device_id}",
            update_interval=SENSOR_UPDATE_INTERVAL,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch the latest device status."""
        try:
            return await self.client.get_device_status(
                self.profile,
                self.usr_id,
                self.device_id,
                self.token,
            )
        except PanasonicApiAuthError as err:
            raise ConfigEntryAuthFailed(
                "Panasonic Smart China session expired"
            ) from err
        except PanasonicApiError as err:
            raise UpdateFailed(
                f"Error communicating with device {self.device_id}: {err}"
            ) from err


class PanasonicAirCleanerSensor(CoordinatorEntity, SensorEntity):
    """A single measurement exposed by a Panasonic air cleaner."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PanasonicDeviceCoordinator,
        description: SensorEntityDescription,
        device_id: str,
        device_name: str,
        model: str | None,
        account_device_id: str | None,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = (
            f"panasonic_smart_china_{device_id}_{description.key}"
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
    def native_value(self) -> int | None:
        """Return the current value of the mapped status field."""
        data = self.coordinator.data or {}
        return _as_int(data.get(self.entity_description.key))


async def async_setup_entry(hass, entry, async_add_entities):
    """Create sensor entities for enabled devices under an account entry."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    client = runtime.get("client") or PanasonicApiClient(
        hass, entry.data.get(CONF_SSID)
    )
    account_device_id = runtime.get("account_device_id")
    devices = entry.data.get(CONF_DEVICES, {})

    entities: list[PanasonicAirCleanerSensor] = []
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

        if PLATFORM_SENSOR not in profile.ha_platforms:
            continue

        descriptions = SENSOR_DESCRIPTIONS.get(profile.entity_kind)
        if not descriptions:
            _LOGGER.error(
                "No sensor descriptions for entity kind %s (%s).",
                profile.entity_kind,
                device_id,
            )
            continue

        coordinator = PanasonicDeviceCoordinator(
            hass,
            client,
            profile,
            entry.data[CONF_USR_ID],
            device_id,
            device_config[CONF_TOKEN],
        )
        # async_refresh keeps a polling failure from breaking platform setup.
        await coordinator.async_refresh()

        device_name = device_config.get(CONF_DEVICE_NAME, device_id)
        model = (
            device_config.get(CONF_DEVICE_MODEL)
            or device_config.get(CONF_CONTROLLER_MODEL)
        )
        entities.extend(
            PanasonicAirCleanerSensor(
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
