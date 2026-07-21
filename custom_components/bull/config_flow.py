"""Config flow for bull-iot integration."""

from collections.abc import Mapping
from typing import Any

from homeassistant import config_entries
import homeassistant.helpers.config_validation as cv
import voluptuous as vol

from .api import AuthenticationError, BullApi, CloudApiError, NetworkError
from .const import DOMAIN


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Bull IoT integration."""

    VERSION = 1

    def __init__(self):
        """Initialize a new ConfigFlow."""
        # Cannot access self.hass here, so we have to initialize it later
        self.bull_api = None

    async def async_step_user(self, user_input=None, error=None):
        """Handle the initial step."""
        if not self.bull_api:
            self.bull_api = BullApi(self.hass)
        errors = {"base": error} if error else {}
        if user_input is not None:
            try:
                await self.bull_api.async_login(
                    user_input["username"], user_input["password"]
                )
            except AuthenticationError as err:
                errors["base"] = err.error_key
            except (CloudApiError, NetworkError):
                errors["base"] = "connection_failed"
            else:
                await self.async_set_unique_id(self.bull_api.openid)
                self._abort_if_unique_id_configured()
                return await self.async_step_select_family()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required("username"): str, vol.Required("password"): str}
            ),
            errors=errors,
        )

    async def async_step_select_family(self, user_input=None, error=None):
        """Allow the user to select one or more family."""
        if user_input is not None:
            selected_families = user_input["families"]
            if len(selected_families) == 0:
                return await self.async_step_select_family(error="no_family_selected")
            # Convert string back to int
            selected_families = [int(familyId) for familyId in selected_families]

            self.bull_api.select_family(selected_families)
            data = self.bull_api.serialize()
            return self.async_create_entry(title=data["username"], data=data)
        try:
            await self.bull_api.async_get_families()
        except AuthenticationError as err:
            return await self.async_step_user(error=err.error_key)
        except (CloudApiError, NetworkError):
            return await self.async_step_user(error="connection_failed")
        # Keys must be string
        options = {
            str(
                family["familyId"]
            ): f"{family['familyName']} ({family['deviceCount']} devices)"
            for family in self.bull_api.families
        }

        return self.async_show_form(
            step_id="select_family",
            data_schema=vol.Schema(
                {
                    vol.Required("families"): cv.multi_select(options),
                }
            ),
            errors={"base": error},
        )

    async def async_step_reauth(self, _entry_data: Mapping[str, Any]):
        """Start reauthentication for an existing config entry."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Validate replacement credentials and reload the existing entry."""
        entry = self._get_reauth_entry()
        errors = {}
        if user_input is not None:
            api = BullApi(self.hass)
            try:
                await api.async_login(user_input["username"], user_input["password"])
            except AuthenticationError as err:
                errors["base"] = err.error_key
            except (CloudApiError, NetworkError):
                errors["base"] = "connection_failed"
            else:
                await self.async_set_unique_id(api.openid)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        "username": user_input["username"],
                        "password": user_input["password"],
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "username", default=entry.data.get("username", "")
                    ): str,
                    vol.Required("password"): str,
                }
            ),
            errors=errors,
        )
