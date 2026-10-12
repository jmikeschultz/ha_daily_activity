"""Failure detection when HA Store logs or defers writes without raising."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.support import integration
from tests.reported_support import reported_entry, reported_hass


class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.hass = reported_hass(self.temp.name)
        self.store = integration.storage.DurableStore(self.hass, 'daily_activity.test.reported')

    async def test_save_is_verified_against_disk_and_fsynced(self):
        data = {'events': {}, 'token_hash': None}
        with patch.object(integration.storage.os, 'fsync', wraps=integration.storage.os.fsync) as sync:
            await self.store.async_save(data)
            self.assertEqual(sync.call_count, 2)
        self.assertEqual(await self.store.async_load(), data)
        self.store.suppress_write = True
        with self.assertRaisesRegex(OSError, 'write_failed'):
            await self.store.async_save({'events': {'new': {}}, 'token_hash': None})
        self.assertEqual(await self.store.async_load(), data)

    async def test_missing_write_and_disk_sync_failure_not_acknowledged(self):
        self.store.suppress_write = True
        with self.assertRaises(OSError):
            await self.store.async_save({'events': {}})
        self.store.suppress_write = False
        with patch.object(integration.storage.os, 'fsync', side_effect=OSError('disk unavailable')):
            with self.assertRaisesRegex(OSError, 'sync_failed'):
                await self.store.async_save({'events': {}})

    async def test_corrupt_json_is_not_reset_or_overwritten(self):
        path = Path(self.store.path)
        path.parent.mkdir(parents=True)
        path.write_text('not json')
        with self.assertRaisesRegex(OSError, 'read_failed'):
            await self.store.async_load()
        self.assertEqual(path.read_text(), 'not json')

    async def test_missing_initialized_ledger_fails_closed(self):
        entry, token = reported_entry(reported_initialized=True)
        source = integration.reported.ReportedSource(self.hass, entry)
        with self.assertRaisesRegex(OSError, 'ledger_missing'):
            await source.async_load()
        self.assertFalse(source.verifies(token))
        self.assertFalse(Path(source.store.path).exists())
