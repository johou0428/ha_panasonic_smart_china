"""Sensor platform for Panasonic Smart China air cleaner devices.

Sensors share the per-device coordinator from coordinator.py, so adding more
sensors does not add more polling.
"""

from __future__ import annotations

from dataclasses import dataclass
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
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

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
from .models import ENTITY_KIND_AIRCLEANER, PLATFORM_SENSOR
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


# Air quality levels reported by the device (matches the app wording):
# 0 = detected, 1 = medium, 2 = good ("优").
LEVEL_MAP = {0: "检出", 1: "中", 2: "优"}
ON_OFF_MAP = {0: "关", 1: "开"}
FILTER_REMIND_MAP = {0: "正常", 1: "需要更换"}


@dataclass(frozen=True)
class PanasonicSensorEntityDescription(SensorEntityDescription):
    """Sensor description with an optional mapping to a readable value."""

    value_map: dict[int, str] | None = None
    fallback_prefix: str | None = None


AIRCLEANER_SENSORS: tuple[PanasonicSensorEntityDescription, ...] = (
    PanasonicSensorEntityDescription(
        key="pm25C",
        name="PM2.5 浓度",
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        device_class=SensorDeviceClass.PM25,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:air-filter",
    ),
    PanasonicSensorEntityDescription(
        key="pm25Level",
        name="PM2.5 等级",
        icon="mdi:air-filter",
        value_map=LEVEL_MAP,
    ),
    PanasonicSensorEntityDescription(
        key="allergenC",
        name="过敏原",
        icon="mdi:flower-pollen",
        value_map=LEVEL_MAP,
    ),
    PanasonicSensorEntityDescription(
        key="gasC",
        name="异味",
        icon="mdi:smog",
        value_map=LEVEL_MAP,
    ),
    # "dust" is NOT a dust reading: it is the dust sensitivity setting
    # (0=低 1=中 2=高) and is exposed by the select platform instead.
    PanasonicSensorEntityDescription(
        key="oxyC",
        name="氧气含量",
        icon="mdi:molecule-oxygen",
    ),
    PanasonicSensorEntityDescription(
        key="oxyLevel",
        name="氧气等级",
        icon="mdi:molecule-oxygen",
        value_map=LEVEL_MAP,
    ),
    PanasonicSensorEntityDescription(
        key="filterTL",
        name="滤网寿命",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:progress-clock",
    ),
    PanasonicSensorEntityDescription(
        key="fliterRemind",
        name="滤网提醒",
        icon="mdi:filter-variant-remove",
        value_map=FILTER_REMIND_MAP,
        fallback_prefix="状态",
    ),
    PanasonicSensorEntityDescription(
        key="nanoe",
        name="nanoeX",
        icon="mdi:atom",
        value_map=ON_OFF_MAP,
        fallback_prefix="状态",
    ),
    PanasonicSensorEntityDescription(
        key="autoCare",
        name="自动托管",
        icon="mdi:auto-fix",
        value_map=ON_OFF_MAP,
        fallback_prefix="状态",
    ),
    PanasonicSensorEntityDescription(
        key="childLock",
        name="童锁",
        icon="mdi:lock",
        value_map=ON_OFF_MAP,
        fallback_prefix="状态",
    ),
)

SENSOR_DESCRIPTIONS: dict[str, tuple[PanasonicSensorEntityDescription, ...]] = {
    ENTITY_KIND_AIRCLEANER: AIRCLEANER_SENSORS,
}


class PanasonicSensor(CoordinatorEntity, SensorEntity):
    """A single measurement exposed by a Panasonic device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        description: PanasonicSensorEntityDescription,
        device_id: str,
        device_name: str,
        model: str | None,
        account_device_id: str | None,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"panasonic_smart_china_{device_id}_{description.key}"
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
    def native_value(self) -> int | str | None:
        """Return the value, translated to text when a mapping is defined."""
        data = self.coordinator.data or {}
        raw = _as_int(data.get(self.entity_description.key))
        if raw is None:
            return None
        value_map = self.entity_description.value_map
        if value_map is None:
            return raw
        if raw in value_map:
            return value_map[raw]
        prefix = self.entity_description.fallback_prefix or "等级"
        return f"{prefix} {raw}"


async def async_setup_entry(hass, entry, async_add_entities):
    """Create sensor entities for enabled devices under an account entry."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    client = runtime.get("client")
    account_device_id = runtime.get("account_device_id")
    if client is None:
        from .api import PanasonicApiClient

        client = PanasonicApiClient(hass, entry.data.get(CONF_SSID))

    devices = entry.data.get(CONF_DEVICES, {})
    entities: list[PanasonicSensor] = []

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
            PanasonicSensor(
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
