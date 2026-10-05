from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .const import CONF_ENTITIES, DOMAIN, PLATFORMS

SERVICE_ADD_SOURCE_ENTITY = "add_source_entity"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
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
    await hass.config_entries.async_forward_entry_setups(
        entry,
        PLATFORMS,
    )

    entry.async_on_unload(
        entry.add_update_listener(_async_reload_entry)
    )

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    return await hass.config_entries.async_unload_platforms(
        entry,
        PLATFORMS,
    )


async def _async_reload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    await hass.config_entries.async_reload(entry.entry_id)