"""Config flow for bull-iot integration."""

from homeassistant import config_entries
import homeassistant.helpers.config_validation as cv
import voluptuous as vol

from .const import DOMAIN
from .api import BullApi


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Bull IoT integration."""

    VERSION = 1

    def __init__(self):
        """Initialize a new ConfigFlow."""
        # Cannot access self.hass here, so we have to initialize it later
        self.bull_api = None

    async def async_step_user(self, user_input=None, non_interactive=False, error=None):
        """Handle the initial step."""
        if not self.bull_api:
            self.bull_api = BullApi(self.hass)
        if user_input is not None:
            try:
                await self.bull_api.async_login(
                    user_input["username"], user_input["password"]
                )
            except Exception as e:
                return await self.async_step_user(error=str(e))
            return await self.async_step_select_family()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required("username"): str, vol.Required("password"): str}
            ),
            errors={"base": error},
        )

    async def async_step_select_family(self, user_input=None, error=None):
        """Allow the user to select one or more family."""
        # errors = {}
        if user_input is not None:
            selected_families = user_input["families"]
            if len(selected_families) == 0:
                return await self.async_step_select_family(error="no_family_selected")
            # Convert string back to int
            selected_families = [int(familyId) for familyId in selected_families]

            self.bull_api.select_family(selected_families)
            data = self.bull_api.serialize()
            return self.async_create_entry(title=data["username"], data=data)
        await self.bull_api.async_get_families()
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
