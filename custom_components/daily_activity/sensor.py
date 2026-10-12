from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, STATE_OFF, STATE_ON
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_point_in_time,
    async_track_state_change_event,
)
from homeassistant.helpers.storage import Store

from .const import (
    CONF_ENTITIES,
    CONF_SOURCE_MODE,
    CONF_SOURCE_ID,
    SOURCE_BINARY_SENSORS,
    SOURCE_REPORTED_INTERVALS,
    CONF_TRACK_DURATIONS,
    CONF_RETENTION_DAYS,
    CONF_TIME_ZONE,
    CONF_TRANSITION_DIRECTION,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_TRANSITION_DIRECTION,
    DOMAIN,
    TRANSITION_OFF_TO_ON,
)

from .durations import add_duration

STORAGE_VERSION = 1


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    cfg = {**entry.data, **entry.options}
    cls = (ReportedActivitySensor if cfg.get(CONF_SOURCE_MODE, SOURCE_BINARY_SENSORS)
           == SOURCE_REPORTED_INTERVALS else DailyActivitySensor)
    async_add_entities([cls(hass, entry)])


class DailyActivitySensor(SensorEntity):
    _attr_icon = "mdi:calendar-heat-map"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry

        cfg = {**entry.data, **entry.options}
        self._entities = list(cfg.get(CONF_ENTITIES, []))
        self._transition_direction = cfg.get(
            CONF_TRANSITION_DIRECTION, DEFAULT_TRANSITION_DIRECTION
        )
        self._tz_name = cfg[CONF_TIME_ZONE]
        self._tz = ZoneInfo(self._tz_name)
        self._retention_days = int(
            cfg.get(CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS)
        )

        self._history: dict[str, int] = {}
        self._track_durations = cfg.get(CONF_TRACK_DURATIONS, False)
        self._durations: dict[str, dict[str, float]] = {}
        self._source_states: dict[str, str | None] = {}
        self._duration_state = "unknown"
        self._duration_since: datetime | None = None
        self._unsub_duration = None

        # Most recent matching transition.
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

        durations = {day: dict(values) for day, values in self._durations.items()}
        if self._track_durations:
            self._accumulate_durations(datetime.now(timezone.utc), durations)
        days = self._history.keys() | durations.keys()
        return {
            "data": [
                {"date": day, "count": self._history.get(day, 0),
                 **durations.get(day, {})}
                for day in sorted(days)
            ],
            "source_entities": self._entities,
            "transition_direction": self._transition_direction,
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

        if stored:
            self._durations = {
                day: dict(values) for day, values in stored.get("durations", {}).items()
            }
        if self._track_durations:
            # Never extend a persisted interval through an unobserved restart gap.
            self._source_states = {
                entity: self._observed_state(self.hass.states.get(entity))
                for entity in self._entities
            }
            self._duration_state = self._combined_state()
            self._duration_since = datetime.now(timezone.utc)
            self._schedule_duration_checkpoint()
            self.async_on_remove(self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STOP, self._async_stop_durations
            ))

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

        if self._unsub_duration is not None:
            self._unsub_duration()
            self._unsub_duration = None
        if self._track_durations:
            self._flush_durations(datetime.now(timezone.utc))
            self._duration_since = None
            await self._save()
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

        self._durations = {
            day: values for day, values in self._durations.items() if day >= cutoff
        }

    @staticmethod
    def _observed_state(state) -> str | None:
        if state is None or state.attributes.get("restored"):
            return None
        return state.state

    def _combined_state(self) -> str:
        states = [self._source_states.get(entity) for entity in self._entities]
        if STATE_ON in states:
            return STATE_ON
        if states and all(state == STATE_OFF for state in states):
            return STATE_OFF
        return "unknown"

    def _accumulate_durations(self, now: datetime, totals: dict) -> None:
        start = self._duration_since
        if start is None:
            return
        add_duration(totals, self._duration_state, start, now, self._tz)

    def _flush_durations(self, now: datetime) -> None:
        now = now.astimezone(timezone.utc)
        self._accumulate_durations(now, self._durations)
        # A backwards clock adjustment must not double count elapsed time.
        if self._duration_since is None or now >= self._duration_since:
            self._duration_since = now
        self._prune()

    async def _async_stop_durations(self, _event) -> None:
        if self._unsub_duration is not None:
            self._unsub_duration()
            self._unsub_duration = None
        self._flush_durations(datetime.now(timezone.utc))
        self._duration_since = None
        await self._save()

    def _schedule_duration_checkpoint(self) -> None:
        self._unsub_duration = async_track_point_in_time(
            self.hass, self._async_duration_checkpoint,
            datetime.now(timezone.utc) + timedelta(minutes=1),
        )

    @callback
    def _async_duration_checkpoint(self, _now) -> None:
        self._flush_durations(datetime.now(timezone.utc))
        self.async_write_ha_state()
        self.hass.async_create_task(self._save())
        self._schedule_duration_checkpoint()

    async def _save(self) -> None:
        await self._store.async_save(
            {
                "history": dict(self._history),
                "durations": {day: dict(values) for day, values in self._durations.items()},
                "last_activity_entity": self._last_activity_entity,
                "last_activity_name": self._last_activity_name,
                "last_activity": self._last_activity,
            }
        )

    @callback
    def _async_source_changed(self, event: Event) -> None:
        if self._track_durations:
            self._flush_durations(datetime.now(timezone.utc))
            new = event.data.get("new_state")
            old = event.data.get("old_state")
            entity = event.data.get("entity_id") or (
                new.entity_id if new is not None else old.entity_id if old is not None else None
            )
            if entity in self._entities:
                self._source_states[entity] = self._observed_state(new)
            self._duration_state = self._combined_state()
            self.async_write_ha_state()
            self.hass.async_create_task(self._save())

        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")

        if old_state is None or new_state is None:
            return

        # Restored states are placeholders, not observed activity.
        if (
            old_state.attributes.get("restored")
            or new_state.attributes.get("restored")
        ):
            return

        # Require both sides of the configured transition to be known states.
        from_state, to_state = (
            (STATE_OFF, STATE_ON)
            if self._transition_direction == TRANSITION_OFF_TO_ON
            else (STATE_ON, STATE_OFF)
        )
        if old_state.state != from_state or new_state.state != to_state:
            return

        # Record the latest activity.
        self._last_activity_entity = new_state.entity_id
        self._last_activity_name = new_state.name
        now = datetime.now(self._tz)
        self._last_activity = now.isoformat()

        day = now.date().isoformat()
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
        if self._track_durations:
            self._flush_durations(datetime.now(timezone.utc))
            self.hass.async_create_task(self._save())
        self._attr_native_value = self._history.get(self._today(), 0)
        self.async_write_ha_state()
        self._schedule_next_midnight()


class ReportedActivitySensor(DailyActivitySensor):
    """Expose the ledger through the existing daily attributes and unique ID."""

    def __init__(self, hass, entry):
        super().__init__(hass, entry)
        self._track_durations = False  # No live or unreported intervals.
        self._source = hass.data[DOMAIN]["reported_sources"][entry.data[CONF_SOURCE_ID]]

    @property
    def extra_state_attributes(self):
        return {**super().extra_state_attributes,
                "source_mode": SOURCE_REPORTED_INTERVALS,
                "source_id": self._source.source_id}

    def _sync_reported(self):
        snapshot = self._source.snapshot
        self._history = snapshot["history"]
        self._durations = snapshot["durations"]
        self._last_activity = snapshot["last_activity"]
        self._last_activity_entity = None
        self._last_activity_name = self.entry.title if self._last_activity else None
        self._attr_native_value = self._history.get(self._today(), 0)
        self.async_write_ha_state()

    async def async_added_to_hass(self):
        await SensorEntity.async_added_to_hass(self)
        self._source.listeners.add(self._sync_reported)
        self.async_on_remove(lambda: self._source.listeners.discard(self._sync_reported))
        self._sync_reported()
        self._schedule_next_midnight()

    @callback
    def _async_midnight(self, _now):
        self._attr_native_value = self._history.get(self._today(), 0)
        self.async_write_ha_state()
        self.hass.async_create_task(self._source.async_prune())
        self._schedule_next_midnight()
