from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ENTITIES,
    CONF_RETENTION_DAYS,
    CONF_TIME_ZONE,
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
)


def _schema(default_tz: str, defaults: dict | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                "name", default=defaults.get("name", "Daily Activity")
            ): selector.TextSelector(),
            vol.Required(
                CONF_ENTITIES,
                default=defaults.get(CONF_ENTITIES, []),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="binary_sensor",
                    multiple=True,
                )
            ),
            vol.Required(
                CONF_TIME_ZONE,
                default=defaults.get(CONF_TIME_ZONE, default_tz),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_RETENTION_DAYS,
                default=defaults.get(
                    CONF_RETENTION_DAYS,
                    DEFAULT_RETENTION_DAYS,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=30,
                    max=2000,
                    step=1,
                    mode="box",
                )
            ),
        }
    )


def _validate(user_input: dict) -> dict[str, str]:
    errors: dict[str, str] = {}

    try:
        ZoneInfo(user_input[CONF_TIME_ZONE])
    except (ZoneInfoNotFoundError, ValueError):
        errors[CONF_TIME_ZONE] = "invalid_time_zone"

    if not user_input[CONF_ENTITIES]:
        errors[CONF_ENTITIES] = "no_entities"

    return errors


class DailyActivityConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}

        if user_input is not None:
            errors = _validate(user_input)

            if not errors:
                name = user_input.pop("name")
                return self.async_create_entry(
                    title=name,
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_schema(self.hass.config.time_zone),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return DailyActivityOptionsFlow()

    async def async_step_reconfigure(self, user_input=None):
        """Edit an existing helper without replacing its entry or history."""
        entry = self._get_reconfigure_entry()
        current = {**entry.data, **entry.options}
        defaults = {**current, "name": entry.title}
        errors = {}

        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                settings = dict(user_input)
                name = settings.pop("name")
                # Existing options must not override the edited settings.
                options = {
                    key: value
                    for key, value in entry.options.items()
                    if key not in settings
                }
                # The integration's update listener reloads the same entry.
                return self.async_update_and_abort(
                    entry,
                    title=name,
                    data={**entry.data, **settings},
                    options=options,
                    reason="reconfigure_successful",
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_schema(self.hass.config.time_zone, defaults),
            errors=errors,
        )


class DailyActivityOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        errors = {}

        current = {
            **self.config_entry.data,
            **self.config_entry.options,
        }

        defaults = {
            "name": self.config_entry.title,
            CONF_ENTITIES: current.get(CONF_ENTITIES, []),
            CONF_TIME_ZONE: current.get(
                CONF_TIME_ZONE,
                self.hass.config.time_zone,
            ),
            CONF_RETENTION_DAYS: current.get(
                CONF_RETENTION_DAYS,
                DEFAULT_RETENTION_DAYS,
            ),
        }

        if user_input is not None:
            errors = _validate(user_input)

            if not errors:
                name = user_input.pop("name")

                if name != self.config_entry.title:
                    self.hass.config_entries.async_update_entry(
                        self.config_entry,
                        title=name,
                    )

                return self.async_create_entry(
                    title="",
                    data=user_input,
                )

        return self.async_show_form(
            step_id="init",
            data_schema=_schema(
                self.hass.config.time_zone,
                defaults,
            ),
            errors=errors,
        )
