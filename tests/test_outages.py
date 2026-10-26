"""Individual outages retain UTC elapsed time independently of daily buckets."""
import asyncio
from datetime import datetime, timedelta, timezone

import unittest
from tests import test_durations

Clock, event = test_durations.Clock, test_durations.event
from tests.support import integration


class OutageTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_durations.DurationTests.asyncSetUp
    asyncTearDown = test_durations.DurationTests.asyncTearDown
    sensor = test_durations.DurationTests.sensor
    start = test_durations.DurationTests.start
    async def test_completed_outage_crosses_midnight_and_survives_restart(self):
        sensor = await self.start()
        sensor._async_source_changed(event('on', 'off'))
        start = Clock.instant.astimezone(timezone.utc).isoformat()
        Clock.instant += timedelta(seconds=30)
        self.assertEqual(sensor.extra_state_attributes['last_outage']['seconds'], 30)
        Clock.instant += timedelta(minutes=2)
        sensor._async_midnight(Clock.instant)
        sensor._async_source_changed(event('off', 'on'))
        record = sensor.extra_state_attributes['last_outage']
        self.assertEqual(record['start'], start)
        self.assertEqual(record['status'], 'completed')
        self.assertEqual(record['seconds'], 150)
        Clock.instant += timedelta(days=1)
        self.assertEqual(sensor.extra_state_attributes['last_outage'], record)
        await asyncio.gather(*self.hass.tasks)
        restored = self.sensor(options={'track_durations': True})
        restored._store.async_load.return_value = sensor._store.async_save.call_args.args[0]
        await restored.async_added_to_hass()
        self.assertEqual(restored.extra_state_attributes['last_outage'], record)

    async def test_no_backfill_or_guessed_start_and_next_outage_replaces(self):
        sensor = await self.start('off')
        self.assertIsNone(sensor.extra_state_attributes['last_outage'])
        sensor._async_source_changed(event('off', 'on'))
        self.assertIsNone(sensor.extra_state_attributes['last_outage'])
        sensor._async_source_changed(event('on', 'off'))
        Clock.instant += timedelta(seconds=20)
        sensor._async_source_changed(event('off', 'on'))
        self.assertEqual(sensor.extra_state_attributes['last_outage']['seconds'], 20)
        Clock.instant += timedelta(minutes=1)
        sensor._async_source_changed(event('on', 'off'))
        self.assertEqual(sensor.extra_state_attributes['last_outage']['seconds'], 0)
        self.assertEqual(sensor.extra_state_attributes['last_outage']['status'], 'ongoing')

    async def test_unknown_and_restart_interrupt_instead_of_inventing_duration(self):
        for interruption in ('unavailable', 'restart', 'stop'):
            with self.subTest(interruption=interruption):
                sensor = await self.start()
                sensor._async_source_changed(event('on', 'off'))
                Clock.instant += timedelta(seconds=20)
                if interruption == 'unavailable':
                    sensor._async_source_changed(event('off', 'unavailable'))
                elif interruption == 'stop':
                    await sensor._async_stop_durations(None)
                else:
                    await sensor._save()
                    saved = sensor._store.async_save.call_args.args[0]
                    Clock.instant += timedelta(hours=2)
                    sensor = self.sensor(options={'track_durations': True})
                    sensor._store.async_load.return_value = saved
                    await sensor.async_added_to_hass()
                record = sensor.extra_state_attributes['last_outage']
                self.assertEqual(record['status'], 'interrupted')
                self.assertIsNone(record['seconds'])
                self.assertIsNone(record['end'])

    async def test_dst_elapsed_time_uses_utc(self):
        Clock.instant = datetime(2026, 11, 1, 5, 59, tzinfo=timezone.utc)
        sensor = await self.start()
        sensor._async_source_changed(event('on', 'off'))
        Clock.instant += timedelta(minutes=2)
        sensor._async_source_changed(event('off', 'on'))
        self.assertEqual(sensor.extra_state_attributes['last_outage']['seconds'], 120)

    def test_reported_outage_joins_off_intervals_across_midnight(self):
        from zoneinfo import ZoneInfo
        records = {}
        def add(key, state, start, end):
            records[key] = dict(state=state, start=start, end=end)
        add('on', 'on', '2026-10-11T23:55:00+00:00', '2026-10-11T23:58:00+00:00')
        add('off1', 'off', '2026-10-11T23:58:00+00:00', '2026-10-12T00:00:00+00:00')
        add('off2', 'off', '2026-10-12T00:00:00+00:00', '2026-10-12T00:03:00+00:00')
        def outage():
            return integration.reported.rebuild(records, ZoneInfo('UTC'), 'on_to_off', datetime(2026, 10, 1, tzinfo=timezone.utc))['last_outage']
        self.assertEqual(outage()['status'], 'awaiting_report')
        self.assertIsNone(outage()['seconds'])
        add('recovery', 'on', '2026-10-12T00:03:00+00:00', '2026-10-12T00:04:00+00:00')
        self.assertEqual(outage()['seconds'], 300)
        self.assertEqual(outage()['status'], 'completed')
        records['recovery']['start'] = '2026-10-12T00:03:01+00:00'
        self.assertEqual(outage()['status'], 'interrupted')
        self.assertIsNone(outage()['seconds'])
        del records['on']
        self.assertIsNone(outage())
