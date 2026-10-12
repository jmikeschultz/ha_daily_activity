"""Historical accounting, durable acknowledgements, and concurrent delivery."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.support import integration
from tests.reported_support import NOW, ReportClock, interval, reported_entry, reported_hass

r = integration.reported


class ReportedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.hass = reported_hass(self.temp.name)
        ReportClock.instant = NOW
        self.clock = patch.object(r, 'datetime', ReportClock)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.entry, self.token = reported_entry()
        self.source = r.ReportedSource(self.hass, self.entry)
        await self.source.async_load()

    async def ingest(self, *events):
        return await self.source.async_ingest(list(events), self.token)

    async def test_isolated_interval_duration_without_assumed_transition(self):
        result = await self.ingest(interval())
        self.assertEqual(result[0]['status'], 'accepted')
        self.assertEqual(self.source.snapshot['history'], {})
        self.assertEqual(self.source.snapshot['durations']['2026-10-12'], {
            'on_seconds': 0, 'off_seconds': 5400, 'unknown_seconds': 0,
        })
        self.assertIsNone(self.source.snapshot['last_activity'])
        disk = json.loads(Path(self.source.store.path).read_text())['data']
        self.assertEqual(disk, self.source.snapshot)
        self.assertNotIn(self.token, json.dumps(disk))

    async def test_duplicate_and_conflicting_reuse(self):
        await self.ingest(interval())
        before = deepcopy(self.source.snapshot)
        results = await self.ingest(interval(), interval(state='on'))
        self.assertEqual([item['status'] for item in results], ['duplicate', 'rejected'])
        self.assertEqual(results[1]['error'], 'event_id_conflict')
        self.assertEqual(self.source.snapshot, before)
        # Equivalent timestamps with different offsets are identical content.
        same = interval(start='2026-10-12T14:00:00Z', end='2026-10-12T15:30:00+00:00')
        self.assertEqual((await self.ingest(same))[0]['status'], 'duplicate')

    async def test_out_of_order_recalculates_transition_day_and_latest(self):
        await self.ingest(interval('later', 'on'))
        self.assertEqual(self.source.snapshot['history'], {})
        await self.ingest(interval('earlier', 'off', '2026-10-12T09:00:00-04:00', '2026-10-12T10:00:00-04:00'))
        self.assertEqual(self.source.snapshot['history'], {'2026-10-12': 1})
        self.assertEqual(self.source.snapshot['last_activity'], '2026-10-12T10:00:00-04:00')
        await self.ingest(interval('old', 'on', '2026-10-11T00:00:00Z', '2026-10-11T01:00:00Z'))
        self.assertEqual(self.source.snapshot['history'], {'2026-10-12': 1})
        self.assertIn('2026-10-10', self.source.snapshot['durations'])

    async def test_both_directions_adjacent_same_state_unknown_and_gaps(self):
        events = [interval('a', 'off', '2026-10-12T00:00:00Z', '2026-10-12T01:00:00Z'),
                  interval('b', 'on', '2026-10-12T01:00:00Z', '2026-10-12T02:00:00Z'),
                  interval('c', 'on', '2026-10-12T02:00:00Z', '2026-10-12T03:00:00Z'),
                  interval('d', 'off', '2026-10-12T03:00:00Z', '2026-10-12T04:00:00Z'),
                  interval('e', 'unknown', '2026-10-12T04:00:00Z', '2026-10-12T05:00:00Z'),
                  interval('f', 'on', '2026-10-12T05:00:00Z', '2026-10-12T06:00:00Z'),
                  interval('g', 'off', '2026-10-12T07:00:00Z', '2026-10-12T08:00:00Z')]
        for direction in ('off_to_on', 'on_to_off'):
            entry, token = reported_entry(direction=direction, source=direction)
            source = r.ReportedSource(self.hass, entry)
            await source.async_load()
            results = await source.async_ingest([{**event, 'source': direction} for event in reversed(events)], token)
            self.assertTrue(all(item['status'] == 'accepted' for item in results))
            self.assertEqual(source.snapshot['history'], {'2026-10-11': 1})
            self.assertEqual(sum(v['unknown_seconds'] for v in source.snapshot['durations'].values()), 3600)
            self.assertEqual(sum(sum(v.values()) for v in source.snapshot['durations'].values()), 7*3600)

    async def test_overlap_rejected_including_identical_content_different_id(self):
        await self.ingest(interval())
        for event in (interval('other'), interval('inside', start='2026-10-12T10:30:00-04:00'),
                      interval('cover', start='2026-10-12T09:00:00-04:00', end='2026-10-12T12:00:00-04:00')):
            result = (await self.ingest(event))[0]
            self.assertEqual(result['error'], 'interval_overlap')
        self.assertEqual(len(self.source.snapshot['events']), 1)

    async def test_partial_batch_valid_events_committed_in_request_order(self):
        bad = interval('bad', state='invalid')
        results = await self.ingest(interval(), bad, interval('conflict'),
                                    interval('next', 'on', '2026-10-12T11:30:00-04:00', '2026-10-12T12:00:00-04:00'))
        self.assertEqual([item['status'] for item in results], ['accepted', 'rejected', 'rejected', 'accepted'])
        self.assertEqual(self.source.snapshot['history'], {'2026-10-12': 1})
        self.assertEqual(set(self.source.snapshot['events']), {'142', 'next'})

    async def test_midnight_split_and_transition_exactly_on_boundary(self):
        await self.ingest(interval('a', 'off', '2026-10-11T23:30:00-04:00', '2026-10-12T00:00:00-04:00'),
                          interval('b', 'on', '2026-10-12T00:00:00-04:00', '2026-10-12T00:30:00-04:00'))
        self.assertEqual(self.source.snapshot['history'], {'2026-10-12': 1})
        self.assertEqual(self.source.snapshot['durations']['2026-10-11']['off_seconds'], 1800)
        self.assertEqual(self.source.snapshot['durations']['2026-10-12']['on_seconds'], 1800)
        results = await self.ingest(interval('c', 'unknown', '2026-10-10T23:30:00-04:00', '2026-10-11T00:30:00-04:00'))
        self.assertEqual(results[0]['status'], 'accepted')
        self.assertEqual(self.source.snapshot['durations']['2026-10-10']['unknown_seconds'], 1800)
        self.assertEqual(self.source.snapshot['durations']['2026-10-11']['unknown_seconds'], 1800)

    async def test_dst_spring_and_fall_elapsed_seconds(self):
        ReportClock.instant = datetime(2026, 11, 3, tzinfo=timezone.utc)
        for event, day, hours in (
            (interval('spring', 'off', '2026-03-08T00:00:00-05:00', '2026-03-09T00:00:00-04:00'), '2026-03-08', 23),
            (interval('fall', 'on', '2026-11-01T00:00:00-04:00', '2026-11-02T00:00:00-05:00'), '2026-11-01', 25),
        ):
            await self.ingest(event)
            self.assertEqual(sum(self.source.snapshot['durations'][day].values()), hours*3600)
        await self.ingest(interval('fold', 'unknown', '2026-11-01T01:00:00-04:00', '2026-11-01T01:00:00-05:00'))
        # Above contradicts the previously observed ON interval.
        self.assertNotIn('fold', self.source.snapshot['events'])

    async def test_retention_boundary_witness_clip_and_pruned_ids(self):
        self.source.retention = 30
        await self.ingest(interval('anchor', 'off', '2026-09-11T23:00:00-04:00', '2026-09-12T00:00:00-04:00'))
        # Expired new intervals cannot establish history, including the boundary witness.
        self.assertNotIn('anchor', self.source.snapshot['events'])
        self.source.snapshot['events']['anchor'] = r.parse_event(
            interval('anchor', 'off', '2026-09-11T23:00:00-04:00', '2026-09-12T00:00:00-04:00'), self.source.source_id, NOW)
        await self.ingest(interval('boundary', 'on', '2026-09-12T00:00:00-04:00', '2026-09-12T01:00:00-04:00'))
        self.assertEqual(self.source.snapshot['history'], {'2026-09-12': 1})
        await self.ingest(interval('crossing', 'unknown', '2026-09-11T23:30:00-04:00', '2026-09-12T01:30:00-04:00'))
        self.assertNotIn('crossing', self.source.snapshot['events'])  # overlap
        ReportClock.instant += timedelta(days=2)
        await self.source.async_prune()
        self.assertEqual(self.source.snapshot['events'], {})
        self.assertEqual(self.source.snapshot['durations'], {})
        self.assertEqual(self.source.snapshot['last_activity'], '2026-09-12T00:00:00-04:00')
        self.assertEqual((await self.ingest(interval('boundary', 'unknown')))[0]['status'], 'accepted')

    async def test_retention_clips_crossing_interval_without_backfill(self):
        self.source.retention = 30
        await self.ingest(interval('crossing', 'off', '2026-09-11T23:30:00-04:00', '2026-09-12T00:30:00-04:00'))
        self.assertEqual(list(self.source.snapshot['durations']), ['2026-09-12'])
        self.assertEqual(self.source.snapshot['durations']['2026-09-12']['off_seconds'], 1800)

    async def test_restart_preserves_tokens_ids_aggregates_no_unreported_time(self):
        await self.ingest(interval())
        snapshot = deepcopy(self.source.snapshot)
        await self.source.async_close()
        self.assertFalse(self.source.verifies(self.token))
        ReportClock.instant += timedelta(hours=1)
        restarted = r.ReportedSource(self.hass, self.entry)
        await restarted.async_load()
        self.assertEqual(restarted.snapshot, snapshot)
        self.assertTrue(restarted.verifies(self.token))
        self.assertEqual((await restarted.async_ingest([interval()], self.token))[0]['status'], 'duplicate')

    async def test_rotation_revocation_survive_restart_and_invalidates_old_token(self):
        fresh = await self.source.async_credential()
        self.assertNotEqual(self.token, fresh)
        self.assertFalse(self.source.verifies(self.token))
        self.assertTrue(self.source.verifies(fresh))
        with self.assertRaises(r.InvalidCredential):
            await self.ingest(interval())
        await self.source.async_credential(revoke=True)
        restarted = r.ReportedSource(self.hass, self.entry)
        await restarted.async_load()
        self.assertFalse(restarted.verifies(self.token))
        self.assertFalse(restarted.verifies(fresh))
        self.assertIsNone(restarted.snapshot['token_hash'])

    async def test_save_failure_rolls_back_events_and_credentials(self):
        before = deepcopy(self.source.snapshot)
        self.source.store.suppress_write = True
        with self.assertRaises(OSError):
            await self.ingest(interval())
        self.assertEqual(self.source.snapshot, before)
        with self.assertRaises(OSError):
            await self.source.async_credential()
        self.assertTrue(self.source.verifies(self.token))
        self.source.store.suppress_write = False
        self.assertEqual((await self.ingest(interval()))[0]['status'], 'accepted')

    async def test_concurrent_requests_accept_only_once(self):
        results = await asyncio.gather(*(self.ingest(interval()) for _ in range(12)))
        self.assertEqual(sum(result[0]['status'] == 'accepted' for result in results), 1)
        self.assertEqual(sum(result[0]['status'] == 'duplicate' for result in results), 11)
        self.assertEqual(len(self.source.snapshot['events']), 1)
        self.assertEqual(self.source.snapshot['durations']['2026-10-12']['off_seconds'], 5400)

    async def test_cancelled_write_keeps_lock_until_commit_completes(self):
        started, release = asyncio.Event(), asyncio.Event()
        original = self.source.store.async_save
        async def slow(data):
            started.set()
            await release.wait()
            await original(data)
        with patch.object(self.source.store, 'async_save', side_effect=slow):
            first = asyncio.create_task(self.ingest(interval()))
            await started.wait()
            first.cancel()
            second = asyncio.create_task(self.ingest(interval()))
            await asyncio.sleep(0)
            self.assertTrue(self.source.lock.locked())
            self.assertFalse(second.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await first
            self.assertEqual((await second)[0]['status'], 'duplicate')

    async def test_options_rebuild_retained_history_in_new_timezone_and_direction(self):
        await self.ingest(interval('off', 'off', '2026-10-12T00:00:00Z', '2026-10-12T01:00:00Z'),
                          interval('on', 'on', '2026-10-12T01:00:00Z', '2026-10-12T02:00:00Z'),
                          interval('off2', 'off', '2026-10-12T02:00:00Z', '2026-10-12T03:00:00Z'))
        self.assertEqual(self.source.snapshot['history'], {'2026-10-11': 1})
        self.entry.options = {'time_zone': 'UTC', 'transition_direction': 'on_to_off'}
        reloaded = r.ReportedSource(self.hass, self.entry)
        await reloaded.async_load()
        self.assertEqual(reloaded.snapshot['history'], {'2026-10-12': 1})
        self.assertEqual(reloaded.snapshot['last_activity'], '2026-10-12T02:00:00+00:00')

    async def test_capacity_limit_and_multiple_independent_sources(self):
        with patch.object(r, 'MAX_RECORDS', 1):
            await self.ingest(interval())
            result = await self.ingest(interval('new', 'on', '2026-10-12T11:30:00-04:00', '2026-10-12T12:00:00-04:00'))
            self.assertEqual(result[0]['error'], 'source_capacity')
        entry, token = reported_entry('other')
        other = r.ReportedSource(self.hass, entry)
        await other.async_load()
        self.assertFalse(other.verifies(self.token))
        self.assertEqual((await other.async_ingest([interval(source='other')], token))[0]['status'], 'accepted')
        self.assertEqual(len(self.source.snapshot['events']), 1)

    async def test_validation_limits_and_invalid_timestamps(self):
        changes = [dict(state='unavailable'), dict(event_id=''), dict(event_id=142),
                   dict(start='2026-10-12T10:00:00'), dict(start='2026-10-12'),
                   dict(start='2026-10-12T10:00:00+00:99'), dict(start='2026-02-30T10:00:00Z'),
                   dict(end='2026-10-12T10:00:00-04:00'), dict(end='2026-10-12T09:00:00-04:00'),
                   dict(end='2026-10-13T10:00:00Z'),
                   dict(start='2026-09-01T00:00:00Z'), dict(extra=True)]
        for change in changes:
            with self.subTest(change=change):
                result = await self.ingest({**interval(), **change})
                self.assertEqual(result[0]['status'], 'rejected')
        self.assertEqual(self.source.snapshot['events'], {})
