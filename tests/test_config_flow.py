"""Tests for the nanoleaf_token config flow.

Home Assistant, aiohttp and voluptuous aren't installed where these run, so
small stand-ins are put in `sys.modules` before the integration is imported.

Run from the repo root:  python3 -B -m unittest discover -s tests
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import enum
from ipaddress import IPv4Address
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.dont_write_bytecode = True

REPO = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- stand-ins


class FlowResultType(enum.StrEnum):
    FORM = "form"
    CREATE_ENTRY = "create_entry"
    ABORT = "abort"


class ConfigFlow:
    def __init_subclass__(cls, domain: str | None = None, **kwargs) -> None:
        super().__init_subclass__(**kwargs)

    def async_abort(self, *, reason, description_placeholders=None):
        return {
            "type": FlowResultType.ABORT,
            "reason": reason,
            "description_placeholders": description_placeholders,
        }

    def async_show_form(self, *, step_id, data_schema=None, errors=None, description_placeholders=None):
        return {
            "type": FlowResultType.FORM,
            "step_id": step_id,
            "errors": errors or {},
            "description_placeholders": description_placeholders,
        }


@dataclass
class ZeroconfServiceInfo:
    ip_address: IPv4Address
    ip_addresses: list
    port: int
    hostname: str
    type: str
    name: str
    properties: dict

    @property
    def host(self) -> str:
        return str(self.ip_address)


class TextSelectorType(enum.StrEnum):
    PASSWORD = "password"


class TextSelectorConfig(dict):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)


class TextSelector:
    def __init__(self, config=None) -> None:
        self.config = config


class ClientError(Exception):
    pass


def load_json_object(path, default=None):
    """Like Home Assistant's: {} for a missing file, an error for a broken one."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except ValueError as err:
        raise RuntimeError("broken json") from err
    if not isinstance(data, dict):
        raise RuntimeError("not an object")
    return data


SAVES: list[tuple[str, bool]] = []  # (path, private) for each save_json call


def save_json(path, data, private=False):
    SAVES.append((str(path), private))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def _module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod
    return mod


def _install_stand_ins() -> None:
    _module("aiohttp", ClientError=ClientError, ClientTimeout=lambda total=None: total)
    _module("voluptuous", Schema=lambda schema: schema, Required=lambda key: key)
    _module("homeassistant")
    _module("homeassistant.core", HomeAssistant=object)
    _module("homeassistant.const", CONF_HOST="host", CONF_TOKEN="token")
    _module("homeassistant.config_entries", ConfigFlow=ConfigFlow, ConfigFlowResult=dict)
    _module("homeassistant.data_entry_flow", FlowResultType=FlowResultType)
    cv = _module("homeassistant.helpers.config_validation", empty_config_schema=lambda domain: None)
    _module("homeassistant.helpers", config_validation=cv)
    _module("homeassistant.helpers.typing", ConfigType=dict)
    _module("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda hass: hass.session)
    _module("homeassistant.helpers.json", save_json=save_json)
    _module(
        "homeassistant.helpers.selector",
        TextSelector=TextSelector,
        TextSelectorConfig=TextSelectorConfig,
        TextSelectorType=TextSelectorType,
    )
    _module("homeassistant.helpers.service_info")
    _module("homeassistant.helpers.service_info.zeroconf", ZeroconfServiceInfo=ZeroconfServiceInfo)
    _module("homeassistant.util")
    _module("homeassistant.util.json", load_json_object=load_json_object)


_install_stand_ins()
sys.path.insert(0, str(REPO / "custom_components"))

from nanoleaf_token import config_flow as cf  # noqa: E402

HOST = "192.168.1.50"
TOKEN = "AbCdEf0123456789AbCdEf0123456789"
NAME = "Skylight TEST"


# ------------------------------------------------------------------- fakes


class FakeResponse:
    def __init__(self, status=200, body=None, json_error=None) -> None:
        self.status = status
        self._body = body
        self._json_error = json_error

    async def json(self, content_type=None):
        if self._json_error:
            raise self._json_error
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """`get()` returns the given response, or raises the given error."""

    def __init__(self, response=None, error=None) -> None:
        self.response = response
        self.error = error
        self.urls: list[str] = []

    def get(self, url, timeout=None):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.response


class FakeFlowManager:
    """Stands in for hass.config_entries.flow."""

    def __init__(self, result=None, error=None, on_init=None) -> None:
        self.result = result
        self.error = error
        self.on_init = on_init
        self.init_calls: list[tuple] = []
        self.aborted: list[str] = []

    async def async_init(self, handler, *, context=None, data=None):
        self.init_calls.append((handler, context, data))
        if self.on_init:
            self.on_init()
        if self.error:
            raise self.error
        return self.result

    def async_abort(self, flow_id):
        self.aborted.append(flow_id)


class FakeHass:
    def __init__(self, tmpdir: str, session=None, flow=None, entries=()) -> None:
        self.session = session
        self.config = types.SimpleNamespace(path=lambda name: os.path.join(tmpdir, name))
        self.config_entries = types.SimpleNamespace(
            flow=flow or FakeFlowManager(),
            async_entries=lambda domain: list(entries),
        )

    async def async_add_executor_job(self, func, *args):
        return func(*args)


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.path = os.path.join(self.tmp, cf.CONFIG_FILE)
        SAVES.clear()

    def flow(self, **hass_kwargs) -> cf.NanoleafTokenFlow:
        flow = cf.NanoleafTokenFlow()
        flow.hass = FakeHass(self.tmp, **hass_kwargs)
        return flow

    def read(self) -> dict:
        with open(self.path, encoding="utf-8") as fh:
            return json.load(fh)

    def put(self, data) -> None:
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(data if isinstance(data, str) else json.dumps(data))


# ------------------------------------------------------------------- _check


class CheckTests(Base):
    async def check(self, **session_kwargs):
        session = FakeSession(**session_kwargs)
        result = await self.flow(session=session)._check(HOST, TOKEN)
        return result, session

    async def test_good_answer_gives_the_name(self):
        result, session = await self.check(response=FakeResponse(body={"name": NAME}))
        self.assertEqual(result, (NAME, None))
        self.assertEqual(session.urls, [f"http://{HOST}:16021/api/v1/{TOKEN}/"])

    async def test_401_and_403_mean_bad_token(self):
        for status in (401, 403):
            result, _ = await self.check(response=FakeResponse(status=status))
            self.assertEqual(result, ("", "invalid_token"), status)

    async def test_other_http_errors_mean_cannot_connect(self):
        for status in (400, 404, 500):
            result, _ = await self.check(response=FakeResponse(status=status))
            self.assertEqual(result, ("", "cannot_connect"), status)

    async def test_network_error_and_timeout_mean_cannot_connect(self):
        for error in (ClientError("refused"), asyncio.TimeoutError()):
            result, _ = await self.check(error=error)
            self.assertEqual(result, ("", "cannot_connect"), error)

    async def test_answer_that_is_not_json_means_cannot_connect(self):
        result, _ = await self.check(response=FakeResponse(json_error=ValueError("not json")))
        self.assertEqual(result, ("", "cannot_connect"))

    async def test_json_without_a_usable_name_is_not_a_nanoleaf(self):
        for body in ({}, {"name": ""}, {"name": 5}, ["name"], "name", None):
            result, _ = await self.check(response=FakeResponse(body=body))
            self.assertEqual(result, ("", "not_nanoleaf"), body)

    async def test_failures_log_nothing(self):
        with self.assertNoLogs(level=logging.DEBUG):
            await self.check(error=ClientError(f"http://{HOST}:16021/api/v1/{TOKEN}/"))


# ----------------------------------------------------- token file read/write


class TokenFileTests(Base):
    def test_write_creates_a_private_file(self):
        cf._write_token(self.path, HOST, TOKEN)
        self.assertEqual(self.read(), {HOST: {"token": TOKEN}})
        self.assertEqual(SAVES, [(self.path, True)])

    def test_write_keeps_other_devices(self):
        self.put({"10.0.0.9": {"token": "other"}})
        cf._write_token(self.path, HOST, TOKEN)
        self.assertEqual(self.read(), {"10.0.0.9": {"token": "other"}, HOST: {"token": TOKEN}})

    def test_write_replaces_an_unreadable_file(self):
        self.put("{not json")
        cf._write_token(self.path, HOST, TOKEN)
        self.assertEqual(self.read(), {HOST: {"token": TOKEN}})

    def test_forget_removes_the_file_when_ours_was_the_only_entry(self):
        self.put({HOST: {"token": TOKEN}})
        cf._forget_token(self.path, HOST)
        self.assertFalse(os.path.exists(self.path))

    def test_forget_keeps_other_devices(self):
        self.put({"10.0.0.9": {"token": "other"}, HOST: {"token": TOKEN}})
        cf._forget_token(self.path, HOST)
        self.assertEqual(self.read(), {"10.0.0.9": {"token": "other"}})
        self.assertEqual(SAVES, [(self.path, True)])

    def test_forget_with_no_file_does_nothing(self):
        cf._forget_token(self.path, HOST)
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(SAVES, [])

    def test_forget_leaves_a_file_without_our_entry_alone(self):
        self.put({"10.0.0.9": {"token": "other"}})
        cf._forget_token(self.path, HOST)
        self.assertEqual(self.read(), {"10.0.0.9": {"token": "other"}})
        self.assertEqual(SAVES, [])

    def test_forget_leaves_an_unreadable_file_alone(self):
        self.put("{not json")
        cf._forget_token(self.path, HOST)
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "{not json")


# ----------------------------------------------------------------- _handoff


class HandoffTests(Base):
    async def handoff(self, manager: FakeFlowManager):
        flow = self.flow(flow=manager)
        return await flow._handoff(IPv4Address(HOST), HOST, NAME, TOKEN)

    async def test_created_entry_is_success(self):
        seen = {}

        def builtin_imports():  # what the built-in flow does on a good import
            seen["file"] = self.read()
            os.remove(self.path)

        manager = FakeFlowManager(result={"type": FlowResultType.CREATE_ENTRY}, on_init=builtin_imports)
        self.assertEqual(await self.handoff(manager), (True, ""))
        # the token was in the file, under the host, while the built-in flow ran
        self.assertEqual(seen["file"], {HOST: {"token": TOKEN}})
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(manager.aborted, [])

    async def test_discovery_record_is_what_the_builtin_flow_reads(self):
        manager = FakeFlowManager(result={"type": FlowResultType.CREATE_ENTRY})
        await self.handoff(manager)
        ((handler, context, info),) = manager.init_calls
        self.assertEqual(handler, "nanoleaf")
        self.assertEqual(context, {"source": "zeroconf"})
        self.assertEqual(info.host, HOST)  # the key the token is filed under
        self.assertEqual(info.name.replace(f".{info.type}", ""), NAME)
        self.assertEqual(info.type, "_nanoleafapi._tcp.local.")
        self.assertEqual(info.port, 16021)
        self.assertIn("id", info.properties)
        self.assertNotEqual(info.properties["id"], HOST)

    async def test_abort_passes_the_reason_on_and_cleans_up(self):
        manager = FakeFlowManager(result={"type": FlowResultType.ABORT, "reason": "invalid_token"})
        with self.assertLogs(cf._LOGGER, level=logging.WARNING) as logs:
            self.assertEqual(await self.handoff(manager), (False, "invalid_token"))
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(manager.aborted, [])
        self.assertNotIn(TOKEN, "\n".join(logs.output))

    async def test_pairing_form_is_a_failure_and_the_flow_is_removed(self):
        manager = FakeFlowManager(
            result={"type": FlowResultType.FORM, "step_id": "link", "flow_id": "abc"}
        )
        with self.assertLogs(cf._LOGGER, level=logging.WARNING):
            added, reason = await self.handoff(manager)
        self.assertFalse(added)
        self.assertIn("link", reason)
        self.assertEqual(manager.aborted, ["abc"])
        self.assertFalse(os.path.exists(self.path))

    async def test_exception_is_a_failure_and_never_logs_the_token(self):
        manager = FakeFlowManager(error=RuntimeError(f"GET http://{HOST}:16021/api/v1/{TOKEN}/ failed"))
        with self.assertLogs(cf._LOGGER, level=logging.DEBUG) as logs:
            self.assertEqual(await self.handoff(manager), (False, "error RuntimeError"))
        text = "\n".join(logs.output)
        self.assertNotIn(TOKEN, text)
        self.assertIn("Traceback", text)  # the debug line carries the traceback
        self.assertEqual([r.levelno for r in logs.records], [logging.WARNING, logging.DEBUG])
        self.assertFalse(os.path.exists(self.path))

    async def test_failure_keeps_other_devices_in_the_file(self):
        self.put({"10.0.0.9": {"token": "other"}})
        manager = FakeFlowManager(result={"type": FlowResultType.ABORT, "reason": "cannot_connect"})
        with self.assertLogs(cf._LOGGER, level=logging.WARNING):
            await self.handoff(manager)
        self.assertEqual(self.read(), {"10.0.0.9": {"token": "other"}})


# ------------------------------------------------------------ the form step


class UserStepTests(Base):
    def good_session(self) -> FakeSession:
        return FakeSession(response=FakeResponse(body={"name": NAME}))

    async def submit(self, flow, host=HOST, token=TOKEN):
        return await flow.async_step_user({"host": host, "token": token})

    async def test_first_show_has_no_errors(self):
        result = await self.flow().async_step_user(None)
        self.assertEqual((result["type"], result["errors"]), (FlowResultType.FORM, {}))

    def test_token_field_is_a_password_field(self):
        self.assertEqual(cf.SCHEMA["token"].config["type"], TextSelectorType.PASSWORD)

    async def test_host_must_be_ipv4(self):
        for host in ("nanoleaf.local", "fe80::1", "192.168.1", "192.168.1.50:16021", ""):
            session = self.good_session()
            result = await self.submit(self.flow(session=session), host=host)
            self.assertEqual(result["errors"], {"host": "invalid_host"}, host)
            self.assertEqual(session.urls, [], host)

    async def test_token_must_be_letters_and_digits(self):
        for token in ("", "   ", "abc/def", "abc?x=1", "abc#frag", "abc def", "../x", "abc%2F"):
            session = self.good_session()
            result = await self.submit(self.flow(session=session), token=token)
            self.assertEqual(result["errors"], {"token": "invalid_token_format"}, token)
            self.assertEqual(session.urls, [], token)  # the device is never asked

    async def test_both_fields_can_be_wrong_at_once(self):
        result = await self.submit(self.flow(session=self.good_session()), host="x", token="a/b")
        self.assertEqual(result["errors"], {"host": "invalid_host", "token": "invalid_token_format"})

    async def test_spaces_around_the_values_are_ignored(self):
        session = self.good_session()
        manager = FakeFlowManager(result={"type": FlowResultType.CREATE_ENTRY})
        result = await self.submit(self.flow(session=session, flow=manager), host=f" {HOST} ", token=f" {TOKEN}\n")
        self.assertEqual(result["reason"], "handed_off")
        self.assertEqual(session.urls, [f"http://{HOST}:16021/api/v1/{TOKEN}/"])

    async def test_check_error_is_shown(self):
        session = FakeSession(response=FakeResponse(status=401))
        manager = FakeFlowManager()
        result = await self.submit(self.flow(session=session, flow=manager))
        self.assertEqual(result["errors"], {"base": "invalid_token"})
        self.assertEqual(manager.init_calls, [])
        self.assertFalse(os.path.exists(self.path))

    async def test_device_already_set_up_stops_before_the_handoff(self):
        manager = FakeFlowManager()
        entries = [types.SimpleNamespace(unique_id=NAME)]
        result = await self.submit(self.flow(session=self.good_session(), flow=manager, entries=entries))
        self.assertEqual((result["type"], result["reason"]), (FlowResultType.ABORT, "already_configured"))
        self.assertEqual(manager.init_calls, [])
        self.assertFalse(os.path.exists(self.path))

    async def test_success_ends_with_the_device_name(self):
        manager = FakeFlowManager(result={"type": FlowResultType.CREATE_ENTRY})
        result = await self.submit(self.flow(session=self.good_session(), flow=manager))
        self.assertEqual((result["type"], result["reason"]), (FlowResultType.ABORT, "handed_off"))
        self.assertEqual(result["description_placeholders"], {"name": NAME})

    async def test_handoff_failure_shows_the_builtin_reason(self):
        manager = FakeFlowManager(result={"type": FlowResultType.ABORT, "reason": "cannot_connect"})
        with self.assertLogs(cf._LOGGER, level=logging.WARNING):
            result = await self.submit(self.flow(session=self.good_session(), flow=manager))
        self.assertEqual(result["errors"], {"base": "handoff_failed"})
        self.assertEqual(result["description_placeholders"], {"reason": "cannot_connect"})

    async def test_setup_already_waiting_gets_its_own_message(self):
        manager = FakeFlowManager(result={"type": FlowResultType.ABORT, "reason": "already_in_progress"})
        with self.assertLogs(cf._LOGGER, level=logging.WARNING):
            result = await self.submit(self.flow(session=self.good_session(), flow=manager))
        self.assertEqual(result["errors"], {"base": "handoff_in_progress"})


# ------------------------------------------------------------- translations


class TranslationTests(unittest.TestCase):
    def test_every_key_the_flow_uses_has_a_string(self):
        path = REPO / "custom_components" / "nanoleaf_token" / "translations" / "en.json"
        strings = json.loads(path.read_text(encoding="utf-8"))["config"]
        for key in (
            "invalid_host", "invalid_token_format", "invalid_token", "cannot_connect",
            "not_nanoleaf", "handoff_failed", "handoff_in_progress",
        ):
            self.assertIn(key, strings["error"])
        for key in ("handed_off", "already_configured"):
            self.assertIn(key, strings["abort"])
        self.assertIn("{reason}", strings["error"]["handoff_failed"])
        self.assertIn("{name}", strings["abort"]["handed_off"])
        # a placeholder is only filled in for the handoff_failed message
        for key, text in strings["error"].items():
            if key != "handoff_failed":
                self.assertNotIn("{", text, key)


class ManifestTests(unittest.TestCase):
    def test_manifest_has_what_hassfest_and_hacs_ask_for(self):
        path = REPO / "custom_components" / "nanoleaf_token" / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        for key in ("domain", "name", "codeowners", "documentation", "issue_tracker", "iot_class", "version"):
            self.assertTrue(manifest.get(key), key)
        self.assertEqual(manifest["domain"], cf.DOMAIN)
        self.assertRegex(manifest["version"], r"^\d+\.\d+\.\d+$")
        # domain, name, then the rest in alphabetical order
        keys = list(manifest)
        self.assertEqual(keys[:2], ["domain", "name"])
        self.assertEqual(keys[2:], sorted(keys[2:]))


if __name__ == "__main__":
    unittest.main()
