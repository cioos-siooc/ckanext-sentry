"""Tests for plugin.py.

Run in the ckan container against ckan.ini, which enables the plugin
(``just test-sentry``). The SDK is stubbed: no test initialises it or sends
an event, whatever DSN the instance has.
"""

import socket

import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit
import pytest
import sentry_sdk
from click.testing import CliRunner

from ckanext.sentry import plugin

DSN = "https://publickey@o123.ingest.sentry.io/4567"


@pytest.fixture(scope="session", autouse=True)
def disable_sdk():
    """Close the client the plugin may have started at import from the
    instance's DSN, so nothing a test logs is sent."""
    sentry_sdk.init(dsn="")


@pytest.fixture
def no_dsn(monkeypatch):
    monkeypatch.setitem(toolkit.config, "ckanext.sentry.dsn", "")
    monkeypatch.delenv("CKAN___CKANEXT__SENTRY__DSN", raising=False)
    monkeypatch.delenv("SENTRY_DSN", raising=False)


@pytest.fixture(autouse=True)
def sdk(monkeypatch):
    """Stub the SDK and reset _INIT_STATE; yields the kwargs of each init()."""
    calls = []
    monkeypatch.setattr(sentry_sdk, "is_initialized", lambda: False)
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(
        plugin, "_INIT_STATE", {"initialized": False, "reason": "not yet attempted"}
    )
    return calls


@pytest.mark.ckan_config("ckan.plugins", "sentry")
@pytest.mark.usefixtures("with_plugins")
def test_plugin_loaded():
    assert plugins.plugin_loaded("sentry")


def test_redact_dsn_hides_key():
    redacted = plugin._redact_dsn(DSN)
    assert "publickey" not in redacted
    assert redacted == "https://***@o123.ingest.sentry.io/4567"


def test_scrub_event_filters_credentials():
    event = {
        "request": {
            "headers": {"Authorization": "token", "X-CKAN-API-Key": "key", "Accept": "*/*"},
            "cookies": {"ckan": "session"},
        }
    }
    request = plugin._scrub_event(event, None)["request"]
    assert request["headers"] == {
        "Authorization": "[Filtered]",
        "X-CKAN-API-Key": "[Filtered]",
        "Accept": "*/*",
    }
    assert request["cookies"] == "[Filtered]"


def test_resolve_release_order(monkeypatch):
    for var in ("CKAN_RELEASE", "SENTRY_RELEASE", "GIT_COMMIT", "SOURCE_COMMIT"):
        monkeypatch.delenv(var, raising=False)
    assert plugin._resolve_release() == ""
    monkeypatch.setenv("SOURCE_COMMIT", "abc123")
    monkeypatch.setenv("CKAN_RELEASE", "2.12.1")
    assert plugin._resolve_release() == "2.12.1"


@pytest.mark.usefixtures("no_dsn")
def test_init_without_dsn_is_disabled(sdk):
    assert plugin._init_sentry() is False
    assert sdk == []
    assert plugin._INIT_STATE["reason"] == "ckanext.sentry.dsn not configured"


@pytest.mark.usefixtures("no_dsn")
def test_init_from_env_dsn(monkeypatch, sdk):
    monkeypatch.setenv("SENTRY_DSN", DSN)
    monkeypatch.setitem(toolkit.config, "ckanext.sentry.traces_sample_rate", "oops")
    assert plugin._init_sentry() is True
    (kwargs,) = sdk
    assert kwargs["dsn"] == DSN
    assert kwargs["send_default_pii"] is False
    assert kwargs["before_send"] is plugin._scrub_event
    assert kwargs["traces_sample_rate"] == 0.0
    assert plugin._INIT_STATE["dsn_host"] == plugin._redact_dsn(DSN)


@pytest.mark.usefixtures("no_dsn")
def test_status_cli_fails_without_dsn():
    result = CliRunner().invoke(plugin.sentry, ["status"])
    assert result.exit_code == 1
    assert "ckanext.sentry.dsn not configured" in result.output


@pytest.mark.usefixtures("no_dsn")
def test_status_cli_reports_initialized(monkeypatch):
    monkeypatch.setitem(toolkit.config, "ckanext.sentry.dsn", DSN)
    result = CliRunner().invoke(plugin.sentry, ["status"])
    assert result.exit_code == 0, result.output
    assert "publickey" not in result.output
    assert plugin._redact_dsn(DSN) in result.output


@pytest.mark.usefixtures("no_dsn")
@pytest.mark.parametrize(
    "dsn, resolves, exit_code",
    [("", True, 1), ("not a url", True, 2), (DSN, False, 3), (DSN, True, 0)],
)
def test_check_cli(monkeypatch, dsn, resolves, exit_code):
    def gethostbyname(host):
        if not resolves:
            raise socket.gaierror("no such host")
        return "192.0.2.1"

    monkeypatch.setitem(toolkit.config, "ckanext.sentry.dsn", dsn)
    monkeypatch.setattr(plugin.socket, "gethostbyname", gethostbyname)
    result = CliRunner().invoke(plugin.sentry, ["check"])
    assert result.exit_code == exit_code, result.output
    assert "publickey" not in result.output


@pytest.mark.ckan_config("ckan.plugins", "sentry")
@pytest.mark.usefixtures("with_plugins")
def test_diagnose_requires_sysadmin(app):
    assert app.get("/api/sentry/_diagnose", status=403).json == {"error": "sysadmin required"}


@pytest.mark.ckan_config("ckan.plugins", "sentry")
@pytest.mark.usefixtures("with_plugins")
def test_diagnose_returns_status(app, monkeypatch):
    monkeypatch.setattr(plugin, "_is_sysadmin", lambda: True)
    payload = app.get("/api/sentry/_diagnose", status=200).json
    assert payload == plugin._status_payload()
