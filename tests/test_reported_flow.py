"""Stable IDs, one-time credential UI, and legacy defaults."""
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.support import integration, make_entry
from tests.test_config_flow import prepare
from tests.reported_support import NOW, ReportClock, reported_entry, reported_hass

flow_module = integration.config_flow


def settings():
    return {'name': 'Example activity', 'source_id': 'example_source',
            'time_zone': 'America/New_York', 'transition_direction': 'on_to_off',
            'retention_days': 400}


class ReportedFlowTests(unittest.IsolatedAsyncioTestCase):
    def initial_flow(self):
        flow = prepare(flow_module.DailyActivityConfigFlow())
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()
        flow._async_current_entries = MagicMock(return_value=[])
        flow.async_abort = MagicMock(side_effect=lambda **kwargs: kwargs)
        return flow

    async def test_mode_selection_and_legacy_defaults(self):
        flow = self.initial_flow()
        form = await flow.async_step_user()
        self.assertEqual(form['data_schema']({})['source_mode'], 'binary_sensors')
        self.assertEqual((await flow.async_step_user({'source_mode': 'binary_sensors'}))['step_id'], 'binary_sensors')
        self.assertEqual((await flow.async_step_user({'source_mode': 'reported_intervals'}))['step_id'], 'reported_intervals')
        self.assertIn('source_mode', (await flow.async_step_user({'source_mode': 'invalid'}))['errors'])
        self.assertNotIn('source_mode', make_entry().data)

    async def test_creation_shows_token_only_at_issuance_stores_hash(self):
        flow = self.initial_flow()
        form = await flow.async_step_reported_intervals(settings())
        self.assertEqual(form['step_id'], 'credentials')
        token = form['description_placeholders']['token']
        self.assertGreaterEqual(len(token), 43)
        self.assertNotIn(token, str(flow._reported_settings))
        result = await flow.async_step_credentials({})
        self.assertEqual(result['data']['ingestion_token_hash'], integration.reported.token_hash(token))
        self.assertEqual(result['data']['source_id'], 'example_source')
        self.assertEqual(result['data']['source_mode'], 'reported_intervals')
        self.assertNotIn('entities', result['data'])
        self.assertIsNone(flow._new_token)
        flow.async_set_unique_id.assert_awaited_once_with('example_source')
        flow._abort_if_unique_id_configured.assert_called_once()

    async def test_unique_source_id_and_validation(self):
        flow = self.initial_flow()
        flow._async_current_entries.return_value = [SimpleNamespace(data={'source_id': 'example_source'})]
        result = await flow.async_step_reported_intervals(settings())
        self.assertEqual(result['reason'], 'already_configured')
        for change in ({'source_id': 'spaces invalid'}, {'source_id': ''}, {'retention_days': 29},
                       {'retention_days': 30.5}, {'time_zone': 'bad/zone'}, {'transition_direction': 'bad'}):
            result = await flow.async_step_reported_intervals({**settings(), **change})
            self.assertTrue(result['errors'])
        schema = flow_module._schema('UTC', reported=True)
        values = schema({key: value for key, value in settings().items() if key != 'transition_direction'})
        self.assertEqual(values['transition_direction'], 'off_to_on')

    async def make_options(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        flow = prepare(flow_module.DailyActivityOptionsFlow())
        flow.hass = reported_hass(temp.name)
        entry, token = reported_entry()
        flow.config_entry = entry
        source = integration.reported.ReportedSource(flow.hass, entry)
        await source.async_load()
        flow.hass.data['daily_activity'] = {'reported_sources': {entry.data['source_id']: source}}
        return flow, source, token

    async def test_options_rename_keeps_id_and_no_token_display(self):
        flow, source, token = await self.make_options()
        form = await flow.async_step_init()
        self.assertEqual(form['step_id'], 'reported_options')
        self.assertNotIn('token', form.get('description_placeholders', {}))
        values = form['data_schema']({'name': 'Renamed', 'time_zone': 'UTC'})
        self.assertNotIn('source_id', values)
        result = await flow.async_step_init({**values, 'source_id': 'attempted_change'})
        self.assertNotIn('source_id', result['data'])
        self.assertEqual(flow.config_entry.data['source_id'], 'example_source')
        self.assertTrue(source.verifies(token))
        flow.hass.config_entries.async_update_entry.assert_called_once_with(flow.config_entry, title='Renamed')

    async def test_options_rotation_and_revocation(self):
        flow, source, old = await self.make_options()
        values = {**settings(), 'credential_action': 'rotate'}
        form = await flow.async_step_init(values)
        fresh = form['description_placeholders']['token']
        self.assertTrue(source.verifies(fresh))
        self.assertFalse(source.verifies(old))
        result = await flow.async_step_credentials({})
        self.assertNotIn(fresh, str(result))
        self.assertNotIn('credential_action', result['data'])
        result = await flow.async_step_init({**settings(), 'credential_action': 'revoke'})
        self.assertFalse(source.verifies(fresh))
        self.assertNotIn('description_placeholders', result)

    async def test_token_failure_or_unloaded_source_does_not_display_new_secret(self):
        flow, source, token = await self.make_options()
        source.store.suppress_write = True
        result = await flow.async_step_init({**settings(), 'credential_action': 'rotate'})
        self.assertEqual(result['errors'], {'base': 'storage_error'})
        self.assertNotIn('token', result.get('description_placeholders', {}))
        self.assertTrue(source.verifies(token))
        flow.hass.data.clear()
        result = await flow.async_step_init({**settings(), 'credential_action': 'revoke'})
        self.assertEqual(result['errors'], {'base': 'source_not_loaded'})

    async def test_reported_reconfiguration_routes_to_options(self):
        flow = self.initial_flow()
        flow._get_reconfigure_entry = MagicMock(return_value=reported_entry()[0])
        self.assertEqual((await flow.async_step_reconfigure())['reason'], 'use_options')
