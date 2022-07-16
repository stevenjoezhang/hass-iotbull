from homeassistant import config_entries
import voluptuous as vol

from .const import DOMAIN
from .api import BullApi


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Bull IoT integration."""

    VERSION = 1

    def __init__(self):
        """Initialize a new ConfigFlow."""
        pass

    async def async_step_user(self, user_input=None, non_interactive=False, error=None):
        """Handle the initial step."""
        if user_input is not None:
            bull_api = BullApi(self.hass)
            try:
                await bull_api.async_login(user_input['username'], user_input['password'])
            except Exception as e:
                return await self.async_step_user(error=str(e))

            # return self.async_abort(reason="success")
            return self.async_create_entry(
                title=user_input['username'],
                data=bull_api.serialize()
            )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required('username'): str,
                vol.Required('password'): str
            }),
            errors={'base': error}
        )

    async def async_step_bull_account(self, user_input=None, error=None):
        pass
