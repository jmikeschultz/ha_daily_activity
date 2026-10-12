"""Real aiohttp requests through the scoped-authentication view."""
import asyncio
from copy import deepcopy
import json
import tempfile
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from tests.support import integration
from tests.reported_support import NOW, ReportClock, interval, reported_entry, reported_hass


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.hass = reported_hass(self.temp.name)
        ReportClock.instant = NOW
        self.clock = patch.object(integration.reported, 'datetime', ReportClock)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.entry, self.token = reported_entry()
        self.source = integration.reported.ReportedSource(self.hass, self.entry)
        await self.source.async_load()
        self.sources = {self.source.source_id: self.source}
        self.view = integration.api.DailyActivityEventsView(self.sources)
        app = web.Application()
        app.router.add_post(self.view.url, self.view.post)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def post(self, events=None, token=None):
        return await self.client.post(self.view.url, json={'events': events or [interval()]},
                                      headers={'Authorization': f'Bearer {token or self.token}'})

    async def test_missing_invalid_and_general_tokens_do_not_reveal_sources(self):
        for header in (None, 'Bearer invalid', 'Bearer ' + 'x'*43, 'Basic ' + self.token):
            headers = {} if header is None else {'Authorization': header}
            response = await self.client.post(self.view.url, data='not json', headers=headers)
            self.assertEqual(response.status, 401)
            self.assertEqual(await response.json(), {'error': 'invalid_credentials'})
            self.assertNotIn(self.token, await response.text())
            self.assertEqual(response.headers['WWW-Authenticate'], 'Bearer')
        self.assertEqual(self.source.snapshot['events'], {})
        self.assertFalse(self.view.requires_auth)  # Custom bearer auth still mandatory above.

    async def test_cross_source_unregistered_and_mixed_batches_forbidden_atomically(self):
        other_entry, token = reported_entry('other')
        other = integration.reported.ReportedSource(self.hass, other_entry)
        await other.async_load()
        self.sources['other'] = other
        for target in ('other', 'not_registered'):
            response = await self.post([interval(source=target)])
            self.assertEqual(response.status, 403)
            self.assertEqual(await response.json(), {'error': 'unauthorized_source'})
        response = await self.post([interval(), interval(source='other')])
        self.assertEqual(response.status, 403)
        self.assertEqual(self.source.snapshot['events'], {})
        response = await self.post([interval(source='other')], token)
        self.assertEqual(response.status, 200)
        self.assertEqual(len(other.snapshot['events']), 1)

    async def test_accepted_duplicate_and_partial_conflict_responses(self):
        response = await self.post()
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.json(), {'results': [
            {'index': 0, 'event_id': '142', 'status': 'accepted', 'http_status': 201},
        ]})
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        response = await self.post([interval(), interval(state='on'), interval('bad', state='bad')])
        self.assertEqual(response.status, 207)
        results = (await response.json())['results']
        self.assertEqual(results[0]['status'], 'duplicate')
        self.assertEqual(results[1]['http_status'], 409)
        self.assertEqual(results[2]['http_status'], 422)

    async def test_envelope_json_and_content_type_errors(self):
        for body in ('{', '[]', '{"events":[]}', '{"events":null}', '{"events":[null]}',
                     '{"events":[],"extra":1}', '{"events":[],"events":[{}]}',
                     '{"events":[{"source":"example_source","state":NaN}]}'):
            response = await self.client.post(self.view.url, data=body,
                headers={'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json'})
            self.assertEqual(response.status, 400, body)
        response = await self.client.post(self.view.url, data='{}',
                headers={'Authorization': f'Bearer {self.token}'})
        self.assertEqual(response.status, 415)
        self.assertEqual(self.source.snapshot['events'], {})

    async def test_declared_and_chunked_payload_and_batch_limits(self):
        headers = {'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json'}
        response = await self.client.post(self.view.url, data=' '*65537, headers=headers)
        self.assertEqual(response.status, 413)
        async def chunks():
            for _ in range(9):
                yield b' ' * 8192
        response = await self.client.post(self.view.url, data=chunks(), headers=headers)
        self.assertEqual(response.status, 413)
        response = await self.post([interval(str(n)) for n in range(101)])
        self.assertEqual(response.status, 413)
        self.assertEqual(self.source.snapshot['events'], {})

    async def test_storage_failure_has_no_ack_or_memory_mutation(self):
        before = deepcopy(self.source.snapshot)
        self.source.store.suppress_write = True
        response = await self.post()
        self.assertEqual(response.status, 503)
        self.assertEqual(await response.json(), {'error': 'storage_error', 'retry': 'entire_batch'})
        self.assertEqual(self.source.snapshot, before)
        self.source.store.suppress_write = False
        self.assertEqual((await self.post()).status, 200)

    async def test_response_waits_for_disk_commit(self):
        started, release = asyncio.Event(), asyncio.Event()
        original = self.source.store.async_save
        async def delayed(snapshot):
            started.set()
            await release.wait()
            await original(snapshot)
        with patch.object(self.source.store, 'async_save', side_effect=delayed):
            task = asyncio.create_task(self.post())
            await started.wait()
            self.assertFalse(task.done())
            self.assertEqual(self.source.snapshot['events'], {})
            release.set()
            self.assertEqual((await task).status, 200)

    async def test_concurrent_requests_and_rotated_credentials(self):
        responses = await asyncio.gather(*(self.post() for _ in range(8)))
        results = [(await response.json())['results'][0]['status'] for response in responses]
        self.assertEqual(results.count('accepted'), 1)
        fresh = await self.source.async_credential()
        self.assertEqual((await self.post()).status, 401)
        self.assertEqual((await self.post(token=fresh)).status, 200)
        await self.source.async_credential(revoke=True)
        self.assertEqual((await self.post(token=fresh)).status, 401)

    async def test_queued_request_rechecks_authorization_under_source_lock(self):
        await self.source.lock.acquire()
        rotate = asyncio.create_task(self.source.async_credential())
        await asyncio.sleep(0)
        request = asyncio.create_task(self.post())
        await asyncio.sleep(0.01)
        self.source.lock.release()
        fresh = await rotate
        self.assertNotEqual(fresh, self.token)
        self.assertEqual((await request).status, 401)
        self.assertEqual(self.source.snapshot['events'], {})

    async def test_write_only_endpoint(self):
        response = await self.client.get(self.view.url,
            headers={'Authorization': f'Bearer {self.token}'})
        self.assertEqual(response.status, 405)
