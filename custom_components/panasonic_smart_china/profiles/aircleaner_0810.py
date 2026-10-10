"""Verified profile for Panasonic Smart China 0810 air cleaner devices."""

from __future__ import annotations

from ..models import (
    ENTITY_KIND_AIRCLEANER,
    PLATFORM_FAN,
    PROTOCOL_AIRCLEANER,
    PanasonicEndpoint,
    PanasonicProfile,
)

# Preset names exposed by the fan entity.
PRESET_AUTO = "Auto"
PRESET_QUIET = "Quiet"
PRESET_MEDIUM = "Medium"
PRESET_HIGH = "High"

# airVo: 0-自动 1-静音 2-中速 3-高速
AIR_VOLUME_MAPPING = {
    PRESET_AUTO: 0,
    PRESET_QUIET: 1,
    PRESET_MEDIUM: 2,
    PRESET_HIGH: 3,
}

# Status keys that are safe to echo back to the cloud with a control command.
SAFE_STATUS_KEYS = frozenset(
    {
        "runSta",
        "airVo",
        "nanoe",
        "dust",
        "gas",
        "sensiOxy",
        "oxyC",
        "oxyLevel",
        "oxyM",
        "pm25C",
        "pm25Level",
        "pm25M",
        "gasC",
        "gasM",
        "allergenC",
        "allergenM",
        "filterTL",
        "filterKind",
        "childLock",
        "lcdSwitch",
        "fliterRemind",
        "autoCare",
        "autoCareRun",
        "ifOxyHigh",
        "ifOxyLow",
        "ifPm25High",
        "ifPm25Low",
        "alSta",
        "serverTime",
    }
)

AIRCLEANER_0810_PROFILE = PanasonicProfile(
    profile_id="aircleaner_0810",
    controller_model="F-93C0PJD",
    name="纳诺怡空气净化器 (F-93C0PJD)",
    category_ids=frozenset({"0810"}),
    model_ids=frozenset({"F-93C0PJD"}),
    ha_platforms=(PLATFORM_FAN,),
    entity_kind=ENTITY_KIND_AIRCLEANER,
    protocol=PROTOCOL_AIRCLEANER,
    status_endpoint=PanasonicEndpoint(
        path="ADevGetStatusJDNeedsAP",
        request_id=10,
        require_results=True,
        required_result_keys=frozenset({"runSta"}),
        params_wrapped=True,
    ),
    # Verified against a real F-93C0PJD: control commands are accepted with the
    # credentials and the control params sent together inside "params". The
    # server replies with {"results": {"todoId": <int>}} and delivers command
    # to the device asynchronously.
    set_endpoint=PanasonicEndpoint(
        path="ADevSetStatusJDNeedsAP",
        request_id=9,
        require_results=False,
        params_wrapped=True,
    ),
    fan_mapping=AIR_VOLUME_MAPPING,
    safe_status_keys=SAFE_STATUS_KEYS,
    cookie_required=True,
)
