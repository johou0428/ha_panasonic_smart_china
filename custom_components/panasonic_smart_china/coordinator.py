"""Shared polling coordinator for Panasonic Smart China devices.

One coordinator per device is shared by every platform (sensor, switch, ...)
so a device is polled on a single interval no matter how many entities it
exposes.
"""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import PanasonicApiAuthError, PanasonicApiClient, PanasonicApiError
from .const import CONF_USR_ID, DOMAIN

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(seconds=60)


class PanasonicDeviceCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll one device once and share its status with every entity."""

    def __init__(self, hass, client, profile, usr_id, device_id, token) -> None:
        self.client = client
        self.profile = profile
        self.usr_id = usr_id
        self.device_id = device_id
        self.token = token
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{device_id}",
            update_interval=UPDATE_INTERVAL,
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


def async_get_device_coordinator(hass, entry, client, profile, device_id, token):
    """Return the shared coordinator for a device, creating it on first use."""
    runtime = hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})
    coordinators = runtime.setdefault("coordinators", {})
    coordinator = coordinators.get(device_id)
    if coordinator is None:
        coordinator = PanasonicDeviceCoordinator(
            hass,
            client,
            profile,
            entry.data[CONF_USR_ID],
            device_id,
            token,
        )
        coordinators[device_id] = coordinator
    return coordinator
