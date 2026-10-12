"""Load integration modules with small HA boundaries, without a running HA server."""

import asyncio
import json
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import zoneinfo  # Load before temporarily patching sys.modules for HA imports.

import voluptuous as vol


class SensorEntity:
    async def async_added_to_hass(self):
        pass

    async def async_will_remove_from_hass(self):
        pass

    def async_write_ha_state(self):
        pass

    def async_on_remove(self, callback):
        pass


class Store:
    """Small disk boundary; supports verification of acknowledged writes."""
    def __init__(self, hass, version, key, private=False, **kwargs):
        self.hass = hass
        self.version = version
        self.key = key
        self.path = hass.config.path('.storage', key)
        self.suppress_write = False

    async def async_load(self):
        path = Path(self.path)
        return json.loads(path.read_text())['data'] if path.exists() else None

    async def async_save(self, data):
        if self.suppress_write:
            return
        def write():
            path = Path(self.path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({'version': self.version, 'key': self.key, 'data': data}))
        await self.hass.async_add_executor_job(write)


class ConfigFlow:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__()


class Selector:
    def __init__(self, config=None):
        self.config = config or {}

    def __call__(self, value):
        return value


class SelectSelector(Selector):
    def __call__(self, value):
        options = [option['value'] for option in self.config['options']]
        return vol.In(options)(value)


def module(name, **attributes):
    result = ModuleType(name)
    result.__dict__.update(attributes)
    return result


def load_integration():
    """Mock imports only; tests execute the repository's actual module code."""
    prefix = '_daily_activity_unit_tests'
    root = Path(__file__).resolve().parents[1] / 'custom_components/daily_activity'
    package = module(prefix)
    package.__path__ = [str(root)]
    mocks = {
        prefix: package,
        'homeassistant': module('homeassistant'),
        'homeassistant.components': module('homeassistant.components'),
        'homeassistant.components.http': module('homeassistant.components.http',
            HomeAssistantView=object, StaticPathConfig=lambda *args: args),
        'homeassistant.components.frontend': module('homeassistant.components.frontend',
            add_extra_js_url=MagicMock()),
        'homeassistant.exceptions': module('homeassistant.exceptions',
            HomeAssistantError=RuntimeError, ConfigEntryNotReady=RuntimeError),
        'homeassistant.helpers.entity_registry': module('homeassistant.helpers.entity_registry',
            async_get=MagicMock()),
        'homeassistant.components.sensor': module(
            'homeassistant.components.sensor', SensorEntity=SensorEntity,
        ),
        'homeassistant.config_entries': module(
            'homeassistant.config_entries', ConfigEntry=object,
            ConfigFlow=ConfigFlow, OptionsFlow=object,
        ),
        'homeassistant.const': module(
            'homeassistant.const', STATE_ON='on', STATE_OFF='off',
            EVENT_HOMEASSISTANT_STOP='homeassistant_stop',
        ),
        'homeassistant.core': module(
            'homeassistant.core', Event=object, HomeAssistant=object, ServiceCall=object,
            callback=lambda function: function,
        ),
        'homeassistant.helpers': module('homeassistant.helpers'),
        'homeassistant.helpers.event': module(
            'homeassistant.helpers.event',
            async_track_point_in_time=MagicMock(return_value=MagicMock()),
            async_track_state_change_event=MagicMock(return_value=MagicMock()),
        ),
        'homeassistant.helpers.storage': module(
            'homeassistant.helpers.storage', Store=Store,
        ),
        'homeassistant.helpers.selector': module(
            'homeassistant.helpers.selector',
            BooleanSelector=Selector, TextSelector=Selector, EntitySelector=Selector, NumberSelector=Selector,
            SelectSelector=SelectSelector, EntitySelectorConfig=dict,
            NumberSelectorConfig=dict, SelectSelectorConfig=dict,
        ),
    }
    loaded = {}
    with patch.dict(sys.modules, mocks):
        for name in ('const', 'durations', 'storage', 'reported', 'api', 'sensor', 'config_flow', '__init__'):
            spec = importlib.util.spec_from_file_location(
                f'{prefix}.{name}', root / f'{name}.py',
            )
            result = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = result
            spec.loader.exec_module(result)
            loaded[name] = result
    return SimpleNamespace(**loaded)


integration = load_integration()


def make_entry(direction=None, options=None):
    data = {'entities': ['binary_sensor.internet'], 'time_zone': 'America/New_York'}
    if direction is not None:
        data['transition_direction'] = direction
    return SimpleNamespace(
        data=data, options=options or {}, entry_id='existing-entry', title='Internet',
    )


def make_hass():
    tasks = []

    def create_task(coroutine):
        task = asyncio.create_task(coroutine)
        tasks.append(task)
        return task

    return SimpleNamespace(
        data={},
        async_add_executor_job=lambda function, *args: asyncio.to_thread(function, *args),
        http=SimpleNamespace(async_register_static_paths=AsyncMock(), register_view=MagicMock()),
        bus=SimpleNamespace(async_listen_once=MagicMock(return_value=MagicMock())),
        states=SimpleNamespace(get=MagicMock(return_value=None)),
        config=SimpleNamespace(time_zone='America/New_York', path=lambda *parts: str(Path('/tmp/daily_activity-unit').joinpath(*parts))),
        config_entries=SimpleNamespace(async_update_entry=MagicMock()),
        async_create_task=create_task, tasks=tasks,
    )


def make_store():
    return SimpleNamespace(async_load=AsyncMock(return_value=None), async_save=AsyncMock())
