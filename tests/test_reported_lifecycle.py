"""Integration registration, unload, source uniqueness, and sensor contract."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.support import integration, make_entry
from tests.reported_support import NOW, ReportClock, interval, reported_entry, reported_hass

life = integration.__init__


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.hass = reported_hass(self.temp.name)
        self.hass.services = SimpleNamespace(async_register=MagicMock())
        self.entry, self.token = reported_entry()
        self.entry.async_on_unload = MagicMock()
        self.entry.add_update_listener = MagicMock(return_value=MagicMock())
        self.hass.config_entries.async_entries = MagicMock(return_value=[self.entry])
        self.hass.config_entries.async_forward_entry_setups = AsyncMock()
        self.hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        def update(entry, **kwargs):
            for key, value in kwargs.items():
                setattr(entry, key, value)
        self.hass.config_entries.async_update_entry.side_effect = update
        ReportClock.instant = NOW
        self.clock = patch.object(integration.reported, 'datetime', ReportClock)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    async def test_view_registered_and_source_load_unload(self):
        await life.async_setup(self.hass, {})
        view = self.hass.http.register_view.call_args.args[0]
        self.assertEqual(view.url, '/api/daily_activity/events')
        await life.async_setup_entry(self.hass, self.entry)
        source = view.sources['example_source']
        self.assertTrue(source.verifies(self.token))
        self.assertTrue(self.entry.data['reported_initialized'])
        self.assertNotIn('ingestion_token_hash', self.entry.data)
        await life.async_unload_entry(self.hass, self.entry)
        self.assertEqual(view.sources, {})
        self.assertFalse(source.verifies(self.token))
        # The only authoritative hash survives in the ledger, including revocation.
        await life.async_setup_entry(self.hass, self.entry)
        source = view.sources['example_source']
        self.assertTrue(source.verifies(self.token))
        await source.async_credential(revoke=True)
        await life.async_unload_entry(self.hass, self.entry)
        await life.async_setup_entry(self.hass, self.entry)
        self.assertFalse(view.sources['example_source'].verifies(self.token))

    async def test_legacy_entry_uses_original_setup_and_no_ledger(self):
        entry = make_entry()
        entry.async_on_unload = MagicMock()
        entry.add_update_listener = MagicMock()
        before = deepcopy(entry.data)
        await life.async_setup_entry(self.hass, entry)
        self.assertEqual(entry.data, before)
        self.assertEqual(self.hass.data['daily_activity']['reported_sources'], {})
        self.assertFalse(Path(self.temp.name, '.storage').exists())

    async def test_duplicate_source_id_including_disabled_entry_rejected(self):
        other, _ = reported_entry()
        other.entry_id = 'different-entry'
        other.disabled_by = 'user'
        self.hass.config_entries.async_entries.return_value = [self.entry, other]
        with self.assertRaisesRegex(RuntimeError, 'Duplicate'):
            await life.async_setup_entry(self.hass, self.entry)
        self.assertEqual(self.hass.data['daily_activity']['reported_sources'], {})

    async def test_failed_platform_setup_removes_ingestion_route(self):
        self.hass.config_entries.async_forward_entry_setups.side_effect = RuntimeError('failed')
        with self.assertRaises(RuntimeError):
            await life.async_setup_entry(self.hass, self.entry)
        self.assertEqual(self.hass.data['daily_activity']['reported_sources'], {})

    async def test_failed_unload_keeps_loaded_source(self):
        await life.async_setup_entry(self.hass, self.entry)
        self.hass.config_entries.async_unload_platforms.return_value = False
        self.assertFalse(await life.async_unload_entry(self.hass, self.entry))
        self.assertTrue(self.hass.data['daily_activity']['reported_sources']['example_source'].verifies(self.token))

    async def test_reported_sensor_matches_daily_attributes_and_no_live_tracking(self):
        await life.async_setup_entry(self.hass, self.entry)
        added = MagicMock()
        await integration.sensor.async_setup_entry(self.hass, self.entry, added)
        sensor = added.call_args.args[0][0]
        self.assertIsInstance(sensor, integration.sensor.ReportedActivitySensor)
        with patch.object(integration.sensor, 'datetime', ReportClock):
            sensor.async_write_ha_state = MagicMock()
            integration.sensor.async_track_state_change_event.reset_mock()
            await sensor.async_added_to_hass()
            integration.sensor.async_track_state_change_event.assert_not_called()
            source = self.hass.data['daily_activity']['reported_sources']['example_source']
            await source.async_ingest([
                interval('off', 'off', '2026-10-12T09:00:00-04:00', '2026-10-12T10:00:00-04:00'),
                interval('on', 'on'),
            ], self.token)
            attributes = sensor.extra_state_attributes
            self.assertEqual(sensor._attr_unique_id, self.entry.entry_id)
            self.assertEqual(sensor._attr_native_value, 1)
            self.assertEqual(attributes['source_id'], 'example_source')
            self.assertEqual(attributes['source_entities'], [])
            self.assertEqual(attributes['last_activity_entity'], None)
            self.assertEqual(attributes['last_activity_name'], self.entry.title)
            self.assertEqual(attributes['last_activity'], '2026-10-12T10:00:00-04:00')
            self.assertEqual(attributes['data'][0]['count'], 1)
            self.assertNotIn(self.token, json.dumps(attributes))
            self.assertNotIn('token_hash', attributes)
            observed = deepcopy(attributes['data'])
            ReportClock.instant = datetime(2026, 10, 13, 5, tzinfo=timezone.utc)
            sensor._async_midnight(ReportClock.instant)
            await asyncio.gather(*self.hass.tasks)
            self.assertEqual(sensor._attr_native_value, 0)
            self.assertEqual(sensor.extra_state_attributes['data'], observed)
            await sensor.async_will_remove_from_hass()

    async def test_add_binary_source_service_rejects_reported_mode(self):
        await life.async_setup(self.hass, {})
        handler = self.hass.services.async_register.call_args.args[2]
        registry = SimpleNamespace(async_get=MagicMock(return_value=SimpleNamespace(config_entry_id=self.entry.entry_id)))
        self.hass.config_entries.async_get_entry = MagicMock(return_value=self.entry)
        with patch.object(life.er, 'async_get', return_value=registry):
            with self.assertRaisesRegex(RuntimeError, 'Reported sources'):
                await handler(SimpleNamespace(data={'daily_activity': 'sensor.activity', 'source_entity': 'binary_sensor.other'}))
