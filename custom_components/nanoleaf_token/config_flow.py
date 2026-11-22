"""Host + token form that hands off to the built-in Nanoleaf integration."""

from __future__ import annotations

import asyncio
import contextlib
from ipaddress import IPv4Address
import logging
import os
import re
import traceback
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_TOKEN
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.json import save_json
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homeassistant.util.json import load_json_object

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

NANOLEAF = "nanoleaf"
CONFIG_FILE = ".nanoleaf.conf"  # read by the built-in integration's discovery
API_TYPE = "_nanoleafapi._tcp.local."

# The token becomes part of the request address, so only what a Nanoleaf
# token is made of is let through.
TOKEN_RE = re.compile(r"[A-Za-z0-9]+")

SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


def _write_token(path: str, host: str, token: str) -> None:
    """Put the token where the built-in flow looks for it, keeping whatever
    else is in the file."""
    try:
        conf = load_json_object(path)
    except Exception:  # noqa: BLE001 - unreadable file: start over
        conf = {}
    conf[host] = {"token": token}
    save_json(path, conf, private=True)


def _forget_token(path: str, host: str) -> None:
    """Take our token back out of the file (the built-in flow deletes the
    file itself when the import worked)."""
    try:
        conf = load_json_object(path)
    except Exception:  # noqa: BLE001 - unreadable file: nothing of ours to clean
        return
    if conf.pop(host, None) is None:
        return
    if conf:
        save_json(path, conf, private=True)
    else:
        with contextlib.suppress(OSError):
            os.remove(path)


class NanoleafTokenFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders: dict[str, str] | None = None
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            token = user_input[CONF_TOKEN].strip()
            ip: IPv4Address | None = None
            try:
                ip = IPv4Address(host)  # the device's API is reached by IPv4 address
            except ValueError:
                errors[CONF_HOST] = "invalid_host"
            if not TOKEN_RE.fullmatch(token):
                errors[CONF_TOKEN] = "invalid_token_format"
            if ip is not None and not errors:
                name, err = await self._check(host, token)
                if err:
                    errors["base"] = err
                elif any(e.unique_id == name for e in self.hass.config_entries.async_entries(NANOLEAF)):
                    return self.async_abort(reason="already_configured")
                else:
                    added, reason = await self._handoff(ip, host, name, token)
                    if added:
                        return self.async_abort(
                            reason="handed_off", description_placeholders={"name": name}
                        )
                    if reason == "already_in_progress":
                        errors["base"] = "handoff_in_progress"
                    else:
                        errors["base"] = "handoff_failed"
                        placeholders = {"reason": reason}
        return self.async_show_form(
            step_id="user",
            data_schema=SCHEMA,
            errors=errors,
            description_placeholders=placeholders,
        )

    async def _check(self, host: str, token: str) -> tuple[str, str | None]:
        """Return (device name, error key)."""
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                f"http://{host}:16021/api/v1/{token}/", timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                if resp.status in (401, 403):
                    return "", "invalid_token"
                if resp.status >= 400:
                    return "", "cannot_connect"
                info = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            # nothing is logged here: the address of the request contains the token
            return "", "cannot_connect"
        name = info.get("name") if isinstance(info, dict) else None
        if not isinstance(name, str) or not name:
            return "", "not_nanoleaf"
        return name, None

    async def _handoff(
        self, ip: IPv4Address, host: str, name: str, token: str
    ) -> tuple[bool, str]:
        """Leave the token where the built-in flow looks, then 'discover' the
        device. Returns (True, "") if the built-in integration added it, else
        (False, why not). Whatever happens, the token doesn't stay in the file."""
        path = self.hass.config.path(CONFIG_FILE)
        await self.hass.async_add_executor_job(_write_token, path, host, token)
        flows = self.hass.config_entries.flow
        reason = ""
        try:
            result = await flows.async_init(
                NANOLEAF,
                context={"source": "zeroconf"},
                data=ZeroconfServiceInfo(
                    ip_address=ip,
                    ip_addresses=[ip],
                    port=16021,
                    hostname=f"{name.replace(' ', '-')}.local.",
                    type=API_TYPE,
                    name=f"{name}.{API_TYPE}",
                    properties={"id": f"token-import-{host}"},
                ),
            )
        except Exception as err:  # noqa: BLE001 - e.g. the built-in flow changed
            result = None
            reason = f"error {type(err).__name__}"
            _LOGGER.warning("The Nanoleaf integration couldn't take over %s: %s", name, type(err).__name__)
            # The full story, for whoever turns on debug logging; the token is
            # blanked in case an error message quotes the request address.
            _LOGGER.debug(
                "Handing %s to the Nanoleaf integration failed:\n%s",
                name, traceback.format_exc().replace(token, "***"),
            )
        added = bool(result) and result.get("type") is FlowResultType.CREATE_ENTRY
        if not added and result:
            if result.get("type") is FlowResultType.FORM:
                reason = f"it asked for pairing (step {result.get('step_id')}) instead of using the token"
                # Don't leave that half-finished flow behind.
                if result.get("flow_id"):
                    with contextlib.suppress(Exception):
                        flows.async_abort(result["flow_id"])
            else:
                reason = str(result.get("reason") or result.get("type"))
            _LOGGER.warning("The Nanoleaf integration didn't add %s: %s", name, reason)
        await self.hass.async_add_executor_job(_forget_token, path, host)
        return added, "" if added else reason
