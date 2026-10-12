"""Direction selection in initial, options, and reconfigure flows."""

from pathlib import Path
import json
import unittest
from unittest.mock import MagicMock

import voluptuous as vol

from tests.support import integration, make_entry, make_hass

flow_module = integration.config_flow


def submission(direction='off_to_on'):
    return {
        'name': 'Internet', 'entities': ['binary_sensor.internet'],
        'transition_direction': direction, 'time_zone': 'America/New_York',
        'retention_days': 400,
    }


def prepare(flow):
    flow.hass = make_hass()
    flow.async_show_form = MagicMock(side_effect=lambda **kwargs: kwargs)
    flow.async_create_entry = MagicMock(side_effect=lambda **kwargs: kwargs)
    return flow


class ConfigFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_initial_form_defaults_and_accepts_both_directions(self):
        for direction in ('off_to_on', 'on_to_off'):
            flow = prepare(flow_module.DailyActivityConfigFlow())
            form = await flow.async_step_binary_sensors()
            schema = form['data_schema']
            legacy = submission()
            legacy.pop('transition_direction')
            self.assertEqual(schema(legacy)['transition_direction'], 'off_to_on')
            validated = schema(submission(direction))
            result = await flow.async_step_user(validated)
            self.assertEqual(result['data']['transition_direction'], direction)
            self.assertEqual(result['title'], 'Internet')

    async def test_options_default_for_legacy_and_override_saved_data(self):
        for data_direction, options, expected in (
            (None, {}, 'off_to_on'),
            ('on_to_off', {}, 'on_to_off'),
            ('off_to_on', {'transition_direction': 'on_to_off'}, 'on_to_off'),
        ):
            flow = prepare(flow_module.DailyActivityOptionsFlow())
            flow.config_entry = make_entry(data_direction, options)
            form = await flow.async_step_init()
            values = submission()
            values.pop('transition_direction')
            self.assertEqual(form['data_schema'](values)['transition_direction'], expected)
            for direction in ('off_to_on', 'on_to_off'):
                result = await flow.async_step_init(submission(direction))
                self.assertEqual(result['data']['transition_direction'], direction)

    async def test_reconfigure_keeps_entry_and_clears_direction_override(self):
        flow = prepare(flow_module.DailyActivityConfigFlow())
        entry = make_entry('off_to_on', {'transition_direction': 'on_to_off'})
        flow._get_reconfigure_entry = MagicMock(return_value=entry)
        flow.async_update_and_abort = MagicMock(side_effect=lambda *args, **kwargs: kwargs)
        form = await flow.async_step_reconfigure()
        values = submission()
        values.pop('transition_direction')
        self.assertEqual(form['data_schema'](values)['transition_direction'], 'on_to_off')
        result = await flow.async_step_reconfigure(submission('off_to_on'))
        self.assertEqual(result['data']['transition_direction'], 'off_to_on')
        self.assertNotIn('transition_direction', result['options'])
        self.assertIs(flow.async_update_and_abort.call_args.args[0], entry)
        flow.async_create_entry.assert_not_called()

    async def test_invalid_direction_is_rejected_by_schema_and_flows(self):
        with self.assertRaises(vol.Invalid):
            flow_module._schema('America/New_York')(submission('invalid'))
        for kind in ('user', 'init', 'reconfigure'):
            flow = prepare(
                flow_module.DailyActivityOptionsFlow() if kind == 'init'
                else flow_module.DailyActivityConfigFlow()
            )
            flow.config_entry = make_entry()
            flow._get_reconfigure_entry = MagicMock(return_value=flow.config_entry)
            result = await getattr(flow, f'async_step_{kind}')(submission('invalid'))
            self.assertEqual(result['errors'], {
                'transition_direction': 'invalid_transition_direction',
            })
            flow.async_create_entry.assert_not_called()

    async def test_legacy_validation_and_existing_errors(self):
        values = submission()
        values.pop('transition_direction')
        self.assertEqual(flow_module._validate(values), {})
        values.update(time_zone='Invalid/Zone', entities=[])
        self.assertEqual(flow_module._validate(values), {
            'time_zone': 'invalid_time_zone', 'entities': 'no_entities',
        })

    async def test_duration_option_defaults_and_saved_override(self):
        for options, expected in (({}, False), ({'track_durations': True}, True)):
            flow = prepare(flow_module.DailyActivityOptionsFlow())
            flow.config_entry = make_entry(options=options)
            form = await flow.async_step_init()
            self.assertEqual(form['data_schema'](submission())['track_durations'], expected)
            values = submission()
            values['track_durations'] = True
            result = await flow.async_step_init(values)
            self.assertTrue(result['data']['track_durations'])
        self.assertFalse(flow_module._schema('America/New_York')(submission())['track_durations'])

    async def test_translations_cover_all_forms(self):
        root = Path(__file__).resolve().parents[1] / 'custom_components/daily_activity'
        strings = json.loads((root / 'strings.json').read_text())
        self.assertEqual(strings, json.loads((root / 'translations/en.json').read_text()))
        for section, steps in (('config', ('binary_sensors', 'reconfigure')), ('options', ('init',))):
            for step in steps:
                self.assertIn('transition_direction', strings[section]['step'][step]['data'])
                self.assertIn('track_durations', strings[section]['step'][step]['data'])
            self.assertIn('invalid_transition_direction', strings[section]['error'])
