from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_point_in_time,
    async_track_state_change_event,
)
from homeassistant.helpers.storage import Store

from .const import (
    CONF_ENTITIES,
    CONF_RETENTION_DAYS,
    CONF_TIME_ZONE,
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
)

STORAGE_VERSION = 1


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    async_add_entities([DailyActivitySensor(hass, entry)])


class DailyActivitySensor(SensorEntity):
    _attr_icon = "mdi:calendar-heat-map"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry

        cfg = {**entry.data, **entry.options}
        self._entities = list(cfg[CONF_ENTITIES])
        self._tz_name = cfg[CONF_TIME_ZONE]
        self._tz = ZoneInfo(self._tz_name)
        self._retention_days = int(
            cfg.get(CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS)
        )

        self._history: dict[str, int] = {}

        # Most recent OFF -> ON transition.
        self._last_activity_entity: str | None = None
        self._last_activity_name: str | None = None
        self._last_activity: str | None = None

        self._attr_name = entry.title
        self._attr_unique_id = entry.entry_id
        self._attr_native_value = 0

        self._store = Store(
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}.{entry.entry_id}",
        )

        self._unsub_midnight = None

    @property
    def extra_state_attributes(self):
        if self._last_activity:
            last_dt = datetime.fromisoformat(self._last_activity)
            last_activity_display = (
                f"{self._last_activity_name} — "
                f"{last_dt.strftime('%b %-d, %-I:%M %p')}"
            )
        else:
            last_activity_display = "No activity recorded yet"

        return {
            "data": [
                {"date": day, "count": count}
                for day, count in sorted(self._history.items())
            ],
            "source_entities": self._entities,
            "last_activity_entity": self._last_activity_entity,
            "last_activity_name": self._last_activity_name,
            "last_activity": self._last_activity,
            "last_activity_display": last_activity_display,
            "time_zone": self._tz_name,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        stored = await self._store.async_load()

        if stored and isinstance(stored.get("history"), dict):
            self._history = {
                str(day): int(count)
                for day, count in stored["history"].items()
            }

        if stored:
            self._last_activity_entity = stored.get(
                "last_activity_entity"
            )
            self._last_activity_name = stored.get(
                "last_activity_name"
            )
            self._last_activity = stored.get(
                "last_activity"
            )

        self._prune()

        self._attr_native_value = self._history.get(
            self._today(),
            0,
        )

        self.async_write_ha_state()

        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                self._entities,
                self._async_source_changed,
            )
        )

        self._schedule_next_midnight()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_midnight is not None:
            self._unsub_midnight()
            self._unsub_midnight = None

        await super().async_will_remove_from_hass()

    def _today(self) -> str:
        return datetime.now(self._tz).date().isoformat()

    def _prune(self) -> None:
        cutoff = (
            datetime.now(self._tz).date()
            - timedelta(days=self._retention_days)
        ).isoformat()

        self._history = {
            day: count
            for day, count in self._history.items()
            if day >= cutoff
        }

    async def _save(self) -> None:
        await self._store.async_save(
            {
                "history": self._history,
                "last_activity_entity": self._last_activity_entity,
                "last_activity_name": self._last_activity_name,
                "last_activity": self._last_activity,
            }
        )

    @callback
    def _async_source_changed(self, event: Event) -> None:
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")

        if old_state is None or new_state is None:
            return

        # Count only transitions into ON.
        if old_state.state == STATE_ON or new_state.state != STATE_ON:
            return

        # Record the latest activity.
        self._last_activity_entity = new_state.entity_id
        self._last_activity_name = new_state.name
        self._last_activity = datetime.now(self._tz).isoformat()

        day = self._today()
        self._history[day] = self._history.get(day, 0) + 1
        self._attr_native_value = self._history[day]

        self._prune()
        self.async_write_ha_state()

        self.hass.async_create_task(self._save())

    def _schedule_next_midnight(self) -> None:
        now_local = datetime.now(self._tz)
        tomorrow = now_local.date() + timedelta(days=1)

        next_local_midnight = datetime.combine(
            tomorrow,
            datetime.min.time(),
            self._tz,
        )

        next_utc = next_local_midnight.astimezone(
            timezone.utc
        )

        self._unsub_midnight = async_track_point_in_time(
            self.hass,
            self._async_midnight,
            next_utc,
        )

    @callback
    def _async_midnight(self, _now) -> None:
        self._attr_native_value = 0
        self.async_write_ha_state()
        self._schedule_next_midnight()