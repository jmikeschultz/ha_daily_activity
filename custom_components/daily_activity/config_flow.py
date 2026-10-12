from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ENTITIES,
    CONF_SOURCE_MODE,
    CONF_SOURCE_ID,
    CONF_TOKEN_HASH,
    SOURCE_BINARY_SENSORS,
    SOURCE_REPORTED_INTERVALS,
    CONF_TRACK_DURATIONS,
    CONF_RETENTION_DAYS,
    CONF_TIME_ZONE,
    CONF_TRANSITION_DIRECTION,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_TRANSITION_DIRECTION,
    DOMAIN,
    TRANSITION_OFF_TO_ON,
    TRANSITION_ON_TO_OFF,
)

from .reported import ID_PATTERN, InvalidCredential, new_token


def _schema(default_tz: str, defaults: dict | None = None, *, reported=False, options=False) -> vol.Schema:
    defaults = defaults or {}
    fields = {
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
                CONF_TRANSITION_DIRECTION,
                default=defaults.get(
                    CONF_TRANSITION_DIRECTION,
                    DEFAULT_TRANSITION_DIRECTION,
                ),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"value": TRANSITION_OFF_TO_ON, "label": "OFF → ON"},
                        {"value": TRANSITION_ON_TO_OFF, "label": "ON → OFF"},
                    ],
                    mode="dropdown",
                )
            ),
            vol.Required(
                CONF_TIME_ZONE,
                default=defaults.get(CONF_TIME_ZONE, default_tz),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_TRACK_DURATIONS,
                default=defaults.get(CONF_TRACK_DURATIONS, False),
            ): selector.BooleanSelector(),
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
    if reported:
        fields = {key: value for key, value in fields.items()
                  if key.schema not in (CONF_ENTITIES, CONF_TRACK_DURATIONS)}
        if not options:
            fields[vol.Required(CONF_SOURCE_ID)] = selector.TextSelector()
        else:
            fields[vol.Optional("credential_action", default="keep")] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=[
                    {"value": "keep", "label": "Keep current token"},
                    {"value": "rotate", "label": "Issue / rotate token"},
                    {"value": "revoke", "label": "Revoke token"},
                ], mode="dropdown")
            )
    return vol.Schema(fields)


def _validate(user_input: dict) -> dict[str, str]:
    errors: dict[str, str] = {}

    try:
        ZoneInfo(user_input[CONF_TIME_ZONE])
    except (ZoneInfoNotFoundError, ValueError):
        errors[CONF_TIME_ZONE] = "invalid_time_zone"

    if user_input.get(CONF_SOURCE_MODE, SOURCE_BINARY_SENSORS) == SOURCE_BINARY_SENSORS and not user_input.get(CONF_ENTITIES):
        errors[CONF_ENTITIES] = "no_entities"

    if user_input.get(
        CONF_TRANSITION_DIRECTION, DEFAULT_TRANSITION_DIRECTION
    ) not in (TRANSITION_OFF_TO_ON, TRANSITION_ON_TO_OFF):
        errors[CONF_TRANSITION_DIRECTION] = "invalid_transition_direction"

    if user_input.get(CONF_SOURCE_MODE) == SOURCE_REPORTED_INTERVALS:
        source = user_input.get(CONF_SOURCE_ID)
        if not isinstance(source, str) or not ID_PATTERN.fullmatch(source):
            errors[CONF_SOURCE_ID] = "invalid_source_id"
    retention = user_input.get(CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS)
    if isinstance(retention, bool) or not isinstance(retention, (int, float)) or not 30 <= retention <= 2000 or retention != int(retention):
        errors[CONF_RETENTION_DAYS] = "invalid_retention"
    return errors


class DailyActivityConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            mode = user_input.get(CONF_SOURCE_MODE, SOURCE_BINARY_SENSORS)
            if mode == SOURCE_REPORTED_INTERVALS:
                return await self.async_step_reported_intervals()
            if mode == SOURCE_BINARY_SENSORS:
                # Also accepts the previous user-step submissions.
                return await self.async_step_binary_sensors(
                    user_input if CONF_ENTITIES in user_input else None
                )
        return self.async_show_form(
            step_id="user", data_schema=vol.Schema({
                vol.Required(CONF_SOURCE_MODE, default=SOURCE_BINARY_SENSORS):
                    selector.SelectSelector(selector.SelectSelectorConfig(options=[
                        {"value": SOURCE_BINARY_SENSORS, "label": "Home Assistant binary sensors"},
                        {"value": SOURCE_REPORTED_INTERVALS, "label": "Reported intervals"},
                    ], mode="dropdown")),
            }), errors={} if user_input is None else {CONF_SOURCE_MODE: "invalid_source_mode"},
        )

    async def async_step_binary_sensors(self, user_input=None):
        errors = {}
        if user_input is not None:
            settings = {**user_input, CONF_SOURCE_MODE: SOURCE_BINARY_SENSORS}
            errors = _validate(settings)
            if not errors:
                name = settings.pop("name")
                return self.async_create_entry(title=name, data=settings)
        return self.async_show_form(
            step_id="binary_sensors", data_schema=_schema(self.hass.config.time_zone), errors=errors,
        )

    async def async_step_reported_intervals(self, user_input=None):
        errors = {}
        if user_input is not None:
            settings = {**user_input, CONF_SOURCE_MODE: SOURCE_REPORTED_INTERVALS}
            errors = _validate(settings)
            if not errors:
                await self.async_set_unique_id(settings[CONF_SOURCE_ID])
                self._abort_if_unique_id_configured()
                # Check the explicit ID as well, for entries restored from older backups.
                if any(entry.data.get(CONF_SOURCE_ID) == settings[CONF_SOURCE_ID]
                       for entry in self._async_current_entries()):
                    return self.async_abort(reason="already_configured")
                token, hashed = new_token()
                settings[CONF_TOKEN_HASH] = hashed
                self._reported_settings = settings
                self._new_token = token
                return await self.async_step_credentials()
        return self.async_show_form(
            step_id="reported_intervals",
            data_schema=_schema(self.hass.config.time_zone, reported=True), errors=errors,
        )

    async def async_step_credentials(self, user_input=None):
        if user_input is not None:
            settings = self._reported_settings
            name = settings.pop("name")
            self._new_token = None
            return self.async_create_entry(title=name, data=settings)
        return self.async_show_form(
            step_id="credentials", data_schema=vol.Schema({}),
            description_placeholders={"token": self._new_token},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return DailyActivityOptionsFlow()

    async def async_step_reconfigure(self, user_input=None):
        """Edit an existing helper without replacing its entry or history."""
        entry = self._get_reconfigure_entry()
        if entry.data.get(CONF_SOURCE_MODE) == SOURCE_REPORTED_INTERVALS:
            return self.async_abort(reason="use_options")
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
        if self.config_entry.data.get(CONF_SOURCE_MODE) == SOURCE_REPORTED_INTERVALS:
            return await self.async_step_reported_options(user_input)
        errors = {}

        current = {
            **self.config_entry.data,
            **self.config_entry.options,
        }

        defaults = {
            "name": self.config_entry.title,
            CONF_TRACK_DURATIONS: current.get(CONF_TRACK_DURATIONS, False),
            CONF_ENTITIES: current.get(CONF_ENTITIES, []),
            CONF_TRANSITION_DIRECTION: current.get(
                CONF_TRANSITION_DIRECTION,
                DEFAULT_TRANSITION_DIRECTION,
            ),
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


    async def async_step_reported_options(self, user_input=None):
        current = {**self.config_entry.data, **self.config_entry.options}
        errors = {}
        if user_input is not None:
            # Source mode/ID and bootstrap hash cannot be edited through options.
            editable = {"name", CONF_TIME_ZONE, CONF_TRANSITION_DIRECTION,
                        CONF_RETENTION_DAYS, "credential_action"}
            settings = {key: value for key, value in user_input.items() if key in editable}
            errors = _validate({**current, **settings})
            action = settings.pop("credential_action", "keep")
            if action not in ("keep", "rotate", "revoke"):
                errors["credential_action"] = "invalid_credential_action"
            if not errors and action != "keep":
                source = self.hass.data.get(DOMAIN, {}).get("reported_sources", {}).get(current[CONF_SOURCE_ID])
                if source is None:
                    errors["base"] = "source_not_loaded"
                else:
                    try:
                        token = await source.async_credential(revoke=action == "revoke")
                    except OSError:
                        errors["base"] = "storage_error"
                    except InvalidCredential:
                        errors["base"] = "source_not_loaded"
                    else:
                        if token is not None:
                            self._reported_settings = settings
                            self._new_token = token
                            return await self.async_step_credentials()
            if not errors:
                return self._finish_reported(settings)
        return self.async_show_form(
            step_id="reported_options",
            data_schema=_schema(self.hass.config.time_zone,
                                {**current, "name": self.config_entry.title}, reported=True, options=True),
            description_placeholders={"source_id": current[CONF_SOURCE_ID]}, errors=errors,
        )

    def _finish_reported(self, settings):
        name = settings.pop("name")
        if name != self.config_entry.title:
            self.hass.config_entries.async_update_entry(self.config_entry, title=name)
        return self.async_create_entry(title="", data=settings)

    async def async_step_credentials(self, user_input=None):
        if user_input is not None:
            self._new_token = None
            return self._finish_reported(self._reported_settings)
        return self.async_show_form(
            step_id="credentials", data_schema=vol.Schema({}),
            description_placeholders={"token": self._new_token},
        )
