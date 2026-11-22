"""Add a Nanoleaf to the built-in integration using a token you already have.

The built-in Nanoleaf integration only gets a token by button-press pairing,
unless a device is *discovered* and a token for it is waiting in
`.nanoleaf.conf`. Some devices (e.g. Skylight, NL64) are never discovered, so
there's no way to hand it an existing token.

This helper fills that gap: its config flow takes host + token, checks them,
writes `.nanoleaf.conf`, and starts the built-in integration's discovery flow,
which imports the token and creates a normal `nanoleaf` entry (and deletes the
file). This helper never creates an entry of its own.

It leans on two things inside the built-in integration: that it still imports
tokens from `.nanoleaf.conf`, and that it accepts a discovery record we make
up. If either goes away the form says so, and the token is taken back out of
the file.
"""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

DOMAIN = "nanoleaf_token"
CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    return True
