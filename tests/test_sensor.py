"""Transition counting, storage compatibility, and local midnight behavior."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from tests.support import integration, make_entry, make_hass, make_store

sensor_module = integration.sensor


class Clock(datetime):
    instant = datetime(2026, 10, 11, 23, 59, tzinfo=ZoneInfo('America/New_York'))

    @classmethod
    def now(cls, tz=None):
        return cls.instant.astimezone(tz) if tz else cls.instant.replace(tzinfo=None)


def state(value, entity='binary_sensor.internet', name='Internet', restored=False):
    if value is None:
        return None
    return SimpleNamespace(
        state=value, entity_id=entity, name=name,
        attributes={'restored': True} if restored else {},
    )


def event(old, new, **kwargs):
    return SimpleNamespace(data={
        'old_state': state(old), 'new_state': state(new, **kwargs),
    })


class SensorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        Clock.instant = datetime(2026, 10, 11, 23, 59, tzinfo=ZoneInfo('America/New_York'))
        self.clock = patch.object(sensor_module, 'datetime', Clock)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.hass = make_hass()

    async def asyncTearDown(self):
        await asyncio.gather(*self.hass.tasks)

    def sensor(self, direction=None, options=None, entities=None):
        entry = make_entry(direction, options)
        if entities is not None:
            entry.data['entities'] = entities
        instance = sensor_module.DailyActivitySensor(
            self.hass, entry,
        )
        instance._store = make_store()
        instance.async_write_ha_state = MagicMock()
        return instance

    async def test_both_directions_and_latest_matching_activity(self):
        for direction, source, target in (
            ('off_to_on', 'off', 'on'), ('on_to_off', 'on', 'off'),
        ):
            with self.subTest(direction=direction):
                sensor = self.sensor(direction, entities=[
                    'binary_sensor.internet', 'binary_sensor.other',
                ])
                sensor._async_source_changed(event(source, target))
                first = sensor.extra_state_attributes.copy()
                self.assertEqual(sensor._attr_native_value, 1)
                self.assertEqual(first['last_activity'], Clock.instant.isoformat())
                self.assertEqual(first['last_activity_entity'], 'binary_sensor.internet')
                self.assertEqual(first['last_activity_name'], 'Internet')
                Clock.instant += timedelta(minutes=1)
                sensor._async_source_changed(event(target, source, name='Recovery'))
                self.assertEqual(sensor.extra_state_attributes, first)
                sensor._async_source_changed(event(
                    source, target, entity='binary_sensor.other', name='Other sensor',
                ))
                latest = sensor.extra_state_attributes
                self.assertEqual(latest['last_activity'], Clock.instant.isoformat())
                self.assertEqual(latest['last_activity_entity'], 'binary_sensor.other')
                self.assertEqual(latest['last_activity_name'], 'Other sensor')
                self.assertEqual(sum(sensor._history.values()), 2)
                await asyncio.gather(*self.hass.tasks)
                self.assertEqual(sensor._store.async_save.call_args.args[0]['last_activity'], latest['last_activity'])

    async def test_all_invalid_initial_same_state_and_reverse_events(self):
        values = (None, 'unknown', 'unavailable', 'off', 'on', 'invalid')
        for direction, match in (
            ('off_to_on', ('off', 'on')), ('on_to_off', ('on', 'off')),
        ):
            sensor = self.sensor(direction)
            for old in values:
                for new in values:
                    if (old, new) == match:
                        continue
                    with self.subTest(direction=direction, old=old, new=new):
                        sensor._async_source_changed(event(old, new))
                        self.assertEqual(sensor._history, {})
                        self.assertIsNone(sensor.extra_state_attributes['last_activity'])
                        self.assertIsNone(sensor.extra_state_attributes['last_activity_entity'])
                        self.assertIsNone(sensor.extra_state_attributes['last_activity_name'])
            sensor.async_write_ha_state.assert_not_called()
            sensor._store.async_save.assert_not_called()
        self.assertEqual(self.hass.tasks, [])

    async def test_restoration_on_either_side_is_ignored(self):
        for direction, source, target in (
            ('off_to_on', 'off', 'on'), ('on_to_off', 'on', 'off'),
        ):
            sensor = self.sensor(direction)
            for side in ('old_state', 'new_state'):
                change = event(source, target)
                change.data[side].attributes['restored'] = True
                sensor._async_source_changed(change)
                self.assertEqual(sensor._history, {})
                self.assertIsNone(sensor.extra_state_attributes['last_activity'])
            sensor._async_source_changed(event(source, target))
            self.assertEqual(sensor._attr_native_value, 1)

    async def test_invalid_events_preserve_existing_latest_activity(self):
        for direction, source, target in (
            ('off_to_on', 'off', 'on'), ('on_to_off', 'on', 'off'),
        ):
            sensor = self.sensor(direction)
            sensor._async_source_changed(event(source, target))
            latest = sensor.extra_state_attributes.copy()
            for old, new in (
                (None, target), ('unknown', target), ('unavailable', target),
                (source, 'unknown'), (source, 'unavailable'), (target, target),
            ):
                sensor._async_source_changed(event(old, new, name='Ignored'))
                self.assertEqual(sensor.extra_state_attributes, latest)
            sensor._async_source_changed(event(source, target, restored=True))
            self.assertEqual(sensor.extra_state_attributes, latest)

    async def test_legacy_config_restores_history_and_last_activity(self):
        sensor = self.sensor()
        saved = {
            'history': {'2026-10-10': 7, '2026-10-11': 3},
            'last_activity_entity': 'binary_sensor.internet',
            'last_activity_name': 'Internet',
            'last_activity': '2026-10-11T10:00:00-04:00',
        }
        sensor._store.async_load.return_value = saved
        await sensor.async_added_to_hass()
        self.assertEqual(sensor._attr_native_value, 3)
        self.assertEqual(sensor._history, saved['history'])
        self.assertEqual(sensor.extra_state_attributes['last_activity'], saved['last_activity'])
        self.assertEqual(sensor.extra_state_attributes['transition_direction'], 'off_to_on')
        self.assertEqual(sensor._store.async_load.await_count, 1)
        sensor._async_source_changed(event('on', 'off'))
        self.assertEqual(sensor._attr_native_value, 3)
        sensor._async_source_changed(event('off', 'on'))
        await asyncio.gather(*self.hass.tasks)
        self.assertEqual(sensor._store.async_save.call_args.args[0]['history'], {'2026-10-10': 7, '2026-10-11': 4})
        with patch.object(sensor_module, 'Store', return_value=make_store()) as store:
            self.sensor()
            store.assert_called_once_with(self.hass, 1, 'daily_activity.existing-entry')

    async def test_options_override_data_and_retain_history_after_reload(self):
        first = self.sensor('off_to_on')
        first._async_source_changed(event('off', 'on'))
        await asyncio.gather(*self.hass.tasks)
        saved = first._store.async_save.call_args.args[0]
        second = self.sensor('off_to_on', {'transition_direction': 'on_to_off'})
        second._store.async_load.return_value = saved
        await second.async_added_to_hass()
        self.assertEqual(second._attr_native_value, 1)
        second._async_source_changed(event('off', 'on'))
        self.assertEqual(second._attr_native_value, 1)
        second._async_source_changed(event('on', 'off'))
        self.assertEqual(second._attr_native_value, 2)

    async def test_midnight_does_not_count_sustained_state(self):
        for direction, source, target in (
            ('off_to_on', 'off', 'on'), ('on_to_off', 'on', 'off'),
        ):
            Clock.instant = datetime(2026, 10, 11, 23, 59, tzinfo=ZoneInfo('America/New_York'))
            sensor = self.sensor(direction)
            sensor._async_source_changed(event(source, target))
            latest = sensor.extra_state_attributes['last_activity']
            await asyncio.gather(*self.hass.tasks)
            sensor._store.async_save.reset_mock()
            Clock.instant = datetime(2026, 10, 12, 0, 0, tzinfo=ZoneInfo('America/New_York'))
            sensor._async_midnight(Clock.instant.astimezone(timezone.utc))
            sensor._async_source_changed(event(target, target))
            self.assertEqual(sensor._attr_native_value, 0)
            self.assertEqual(sensor._history, {'2026-10-11': 1})
            self.assertEqual(sensor.extra_state_attributes['last_activity'], latest)
            sensor._store.async_save.assert_not_called()
            sensor._async_source_changed(event(target, source))
            sensor._async_source_changed(event(source, target))
            self.assertEqual(sensor._history, {'2026-10-11': 1, '2026-10-12': 1})

    async def test_transition_before_midnight_callback_keeps_new_day_count(self):
        sensor = self.sensor('on_to_off')
        Clock.instant = datetime(2026, 10, 12, 0, 0, tzinfo=ZoneInfo('America/New_York'))
        sensor._async_source_changed(event('on', 'off'))
        sensor._async_midnight(Clock.instant.astimezone(timezone.utc))
        self.assertEqual(sensor._attr_native_value, 1)
        self.assertEqual(sensor._history, {'2026-10-12': 1})

    async def test_local_day_and_dst_midnight_scheduling(self):
        sensor = self.sensor('on_to_off')
        # UTC date differs from the configured local date.
        Clock.instant = datetime(2026, 10, 12, 2, 0, tzinfo=timezone.utc)
        sensor._async_source_changed(event('on', 'off'))
        self.assertEqual(sensor._history, {'2026-10-11': 1})
        for instant, expected in (
            (datetime(2026, 3, 8, 0, 0, tzinfo=sensor._tz), datetime(2026, 3, 9, 4, 0, tzinfo=timezone.utc)),
            (datetime(2026, 11, 1, 0, 0, tzinfo=sensor._tz), datetime(2026, 11, 2, 5, 0, tzinfo=timezone.utc)),
        ):
            Clock.instant = instant
            with patch.object(sensor_module, 'async_track_point_in_time') as track:
                sensor._schedule_next_midnight()
                self.assertEqual(track.call_args.args[2], expected)
