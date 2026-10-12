"""Daily elapsed time without changing transition counts."""
import asyncio
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import MagicMock, patch

from tests import test_sensor

Clock = test_sensor.Clock
event = test_sensor.event
state = test_sensor.state
sensor_module = test_sensor.sensor_module


class DurationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_sensor.SensorTests.asyncSetUp
    asyncTearDown = test_sensor.SensorTests.asyncTearDown
    sensor = test_sensor.SensorTests.sensor
    async def start(self, initial='on', entities=None):
        sensor = self.sensor(options={'track_durations': True}, entities=entities)
        self.hass.states.get.side_effect = lambda entity: state(initial, entity=entity)
        await sensor.async_added_to_hass()
        return sensor

    def row(self, sensor, day='2026-10-11'):
        return next(row for row in sensor.extra_state_attributes['data'] if row['date'] == day)

    async def test_ongoing_projection_is_repeatable_and_checkpointed(self):
        sensor = await self.start()
        Clock.instant += timedelta(seconds=30)
        self.assertEqual(self.row(sensor)['on_seconds'], 30)
        self.assertEqual(self.row(sensor)['on_seconds'], 30)
        self.assertEqual(sensor._durations, {})
        sensor._async_duration_checkpoint(Clock.instant)
        await asyncio.gather(*self.hass.tasks)
        self.assertEqual(sensor._store.async_save.call_args.args[0]['durations']['2026-10-11']['on_seconds'], 30)
        Clock.instant += timedelta(seconds=15)
        sensor._async_source_changed(event('on', 'off'))
        Clock.instant += timedelta(seconds=10)
        self.assertEqual(self.row(sensor)['on_seconds'], 45)
        self.assertEqual(self.row(sensor)['off_seconds'], 10)
        self.assertEqual(sensor._history, {})

    async def test_multi_source_union_and_unknown(self):
        Clock.instant = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
        sensor = await self.start('off', ['binary_sensor.internet', 'binary_sensor.other'])
        for old, new, entity in (
            ('off', 'on', 'binary_sensor.internet'),
            ('off', 'on', 'binary_sensor.other'),
            ('on', 'off', 'binary_sensor.internet'),
            ('on', 'unavailable', 'binary_sensor.other'),
            ('off', 'unknown', 'binary_sensor.internet'),
            ('unavailable', 'off', 'binary_sensor.other'),
            ('unknown', 'off', 'binary_sensor.internet'),
        ):
            Clock.instant += timedelta(seconds=10)
            sensor._async_source_changed(event(old, new, entity=entity))
        Clock.instant += timedelta(seconds=10)
        row = self.row(sensor)
        self.assertEqual((row['on_seconds'], row['off_seconds'], row['unknown_seconds']), (30, 20, 30))
        self.assertEqual(sensor._history, {'2026-10-11': 2})

    async def test_unknown_initial_removed_and_restored_sources(self):
        sensor = await self.start(None)
        Clock.instant += timedelta(seconds=10)
        sensor._async_source_changed(event(None, 'on', restored=True))
        Clock.instant += timedelta(seconds=10)
        sensor._async_source_changed(event('on', 'off'))
        Clock.instant += timedelta(seconds=10)
        sensor._async_source_changed(event('off', None))
        Clock.instant += timedelta(seconds=10)
        rows = sensor.extra_state_attributes['data']
        self.assertEqual(sum(r['unknown_seconds'] for r in rows), 30)
        self.assertEqual(sum(r['off_seconds'] for r in rows), 10)
        self.assertEqual(sensor._history, {})

    async def test_midnight_and_delayed_callback_split_exactly(self):
        sensor = await self.start()
        Clock.instant += timedelta(minutes=2)
        sensor._async_midnight(Clock.instant)
        self.assertEqual(self.row(sensor)['on_seconds'], 60)
        self.assertEqual(self.row(sensor, '2026-10-12')['on_seconds'], 60)
        self.assertEqual(sensor._attr_native_value, 0)

    async def test_dst_days_are_23_and_25_hours(self):
        for month, day, expected in ((3, 8, 23 * 3600), (11, 1, 25 * 3600)):
            Clock.instant = datetime(2026, month, day, tzinfo=sensor_module.ZoneInfo('America/New_York'))
            sensor = await self.start()
            date = Clock.instant.date().isoformat()
            Clock.instant = datetime(2026, month, day + 1, tzinfo=sensor._tz)
            sensor._async_midnight(Clock.instant)
            self.assertEqual(self.row(sensor, date)['on_seconds'], expected)

    async def test_restart_preserves_totals_but_excludes_gap(self):
        first = await self.start()
        Clock.instant += timedelta(seconds=30)
        first._async_duration_checkpoint(Clock.instant)
        await asyncio.gather(*self.hass.tasks)
        saved = first._store.async_save.call_args.args[0]
        Clock.instant += timedelta(hours=3)
        second = self.sensor(options={'track_durations': True})
        second._store.async_load.return_value = saved
        await second.async_added_to_hass()
        Clock.instant += timedelta(seconds=20)
        self.assertEqual(sum(r['on_seconds'] for r in second.extra_state_attributes['data']), 50)
        await second.async_will_remove_from_hass()
        self.assertIsNone(second._duration_since)
        self.assertEqual(sum(r['on_seconds'] for r in second._durations.values()), 50)

    async def test_enabling_does_not_backfill_and_disabling_keeps_totals(self):
        sensor = self.sensor(options={'track_durations': True})
        sensor._store.async_load.return_value = {'history': {'2026-10-10': 7}}
        await sensor.async_added_to_hass()
        self.assertEqual(sensor.extra_state_attributes['data'], [{'date': '2026-10-10', 'count': 7}])
        Clock.instant += timedelta(seconds=10)
        await sensor.async_will_remove_from_hass()
        saved = sensor._store.async_save.call_args.args[0]
        disabled = self.sensor()
        disabled._store.async_load.return_value = saved
        await disabled.async_added_to_hass()
        before = disabled.extra_state_attributes['data']
        Clock.instant += timedelta(hours=1)
        self.assertEqual(disabled.extra_state_attributes['data'], before)
        self.assertEqual(disabled._attr_unique_id, sensor._attr_unique_id)

    async def test_checkpoint_timer_and_shutdown_cancel(self):
        with patch.object(sensor_module, 'async_track_point_in_time', side_effect=lambda *args: MagicMock()) as track:
            sensor = await self.start()
            self.assertEqual(track.call_args_list[0].args[2], Clock.instant.astimezone(timezone.utc) + timedelta(minutes=1))
            duration_cancel = sensor._unsub_duration
            midnight_cancel = sensor._unsub_midnight
            await sensor.async_will_remove_from_hass()
            duration_cancel.assert_called_once()
            midnight_cancel.assert_called_once()

    async def test_home_assistant_stop_saves_final_interval(self):
        sensor = await self.start()
        self.hass.bus.async_listen_once.assert_called_once_with(
            'homeassistant_stop', sensor._async_stop_durations,
        )
        Clock.instant += timedelta(seconds=25)
        await sensor._async_stop_durations(None)
        Clock.instant += timedelta(hours=1)
        self.assertEqual(self.row(sensor)['on_seconds'], 25)
        self.assertIsNone(sensor._duration_since)
        self.assertEqual(sensor._store.async_save.await_count, 1)
