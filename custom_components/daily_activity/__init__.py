from homeassistant.config_entries import ConfigEntry
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.frontend import add_extra_js_url
from pathlib import Path
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .const import (CONF_ENTITIES, CONF_SOURCE_ID, CONF_SOURCE_MODE, CONF_TOKEN_HASH, DOMAIN,
                    PLATFORMS, SOURCE_REPORTED_INTERVALS)
from .api import DailyActivityEventsView
from .reported import ReportedSource

SERVICE_ADD_SOURCE_ENTITY = "add_source_entity"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    domain_data = hass.data.setdefault(DOMAIN, {})
    sources = domain_data.setdefault("reported_sources", {})
    hass.http.register_view(DailyActivityEventsView(sources))

    async def close_sources(_event):
        for source in tuple(sources.values()):
            await source.async_close()
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, close_sources)

    url = "/daily_activity_static/daily-activity-editor.js"
    await hass.http.async_register_static_paths([
        StaticPathConfig(
            url,
            str(Path(__file__).parent / "frontend" / "daily-activity-editor.js"),
            False,
        )
    ])
    add_extra_js_url(hass, url + "?v=0.2.0")

    async def add_source_entity(call: ServiceCall) -> None:
        daily_activity_entity = call.data["daily_activity"]
        source_entity = call.data["source_entity"]

        entity_registry = er.async_get(hass)
        registry_entry = entity_registry.async_get(daily_activity_entity)

        if registry_entry is None:
            raise HomeAssistantError(
                f"Daily Activity entity not found: {daily_activity_entity}"
            )

        config_entry_id = registry_entry.config_entry_id

        if config_entry_id is None:
            raise HomeAssistantError(
                f"{daily_activity_entity} has no config entry"
            )

        entry = hass.config_entries.async_get_entry(config_entry_id)

        if entry is None or entry.domain != DOMAIN:
            raise HomeAssistantError(
                f"{daily_activity_entity} is not a Daily Activity entity"
            )

        current = {**entry.data, **entry.options}
        if current.get(CONF_SOURCE_MODE) == SOURCE_REPORTED_INTERVALS:
            raise HomeAssistantError("Reported sources do not monitor binary sensors")
        entities = list(current.get(CONF_ENTITIES, []))

        if source_entity in entities:
            return

        entities.append(source_entity)

        new_data = dict(entry.data)
        new_options = dict(entry.options)

        if CONF_ENTITIES in new_options:
            new_options[CONF_ENTITIES] = entities
            hass.config_entries.async_update_entry(
                entry,
                options=new_options,
            )
        else:
            new_data[CONF_ENTITIES] = entities
            hass.config_entries.async_update_entry(
                entry,
                data=new_data,
            )

        await hass.config_entries.async_reload(entry.entry_id)

    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_SOURCE_ENTITY,
        add_source_entity,
    )

    return True


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    source = None
    sources = hass.data.setdefault(DOMAIN, {}).setdefault("reported_sources", {})
    if entry.data.get(CONF_SOURCE_MODE) == SOURCE_REPORTED_INTERVALS:
        source_id = entry.data[CONF_SOURCE_ID]
        if any(other.entry_id != entry.entry_id and other.data.get(CONF_SOURCE_ID) == source_id
               for other in hass.config_entries.async_entries(DOMAIN)):
            raise HomeAssistantError("Duplicate reported source ID")
        if source_id in sources:
            raise HomeAssistantError("Reported source already loaded")
        source = ReportedSource(hass, entry)
        try:
            await source.async_load()
        except OSError:
            raise ConfigEntryNotReady("Reported ledger unavailable") from None
        # The bootstrap hash belongs only to the durable ledger after first load.
        # Missing/corrupt ledgers must not resurrect revoked bootstrap tokens.
        if not entry.data.get("reported_initialized") or CONF_TOKEN_HASH in entry.data:
            data = {key: value for key, value in entry.data.items() if key != CONF_TOKEN_HASH}
            hass.config_entries.async_update_entry(entry, data={**data, "reported_initialized": True})
        sources[source_id] = source
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        if source is not None:
            await source.async_close()
            sources.pop(source.source_id, None)
        raise

    entry.async_on_unload(
        entry.add_update_listener(_async_reload_entry)
    )

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and entry.data.get(CONF_SOURCE_MODE) == SOURCE_REPORTED_INTERVALS:
        sources = hass.data[DOMAIN]["reported_sources"]
        source = sources.get(entry.data[CONF_SOURCE_ID])
        if source is not None:
            await source.async_close()
            sources.pop(source.source_id, None)
    return unloaded


async def _async_reload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
