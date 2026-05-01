import logging
import os
import socket
import urllib.parse
import urllib.request

import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit
import click
from flask import Blueprint, jsonify

log = logging.getLogger(__name__)

LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-ckan-api-key",
    "x-api-key",
    "proxy-authorization",
}

# Tracks the last successful init so `status` / blueprint can introspect
# without re-running side effects.
_INIT_STATE: dict = {
    "initialized": False,
    "reason": "not yet attempted",
    "dsn_host": None,
    "environment": None,
    "release": None,
    "event_level": None,
    "breadcrumb_level": None,
    "traces_sample_rate": None,
    "integrations": [],
    "sdk_version": None,
}


def _redact_dsn(dsn: str) -> str:
    """Return DSN host only — never expose the public/secret key."""
    if not dsn:
        return ""
    try:
        parsed = urllib.parse.urlparse(dsn)
        return f"{parsed.scheme}://***@{parsed.hostname}{parsed.path}"
    except Exception:
        return "<unparseable DSN>"


def _scrub_event(event, hint):
    """before_send hook — strip auth headers and cookies from the event."""
    request = event.get("request") or {}
    headers = request.get("headers")
    if isinstance(headers, dict):
        for key in list(headers.keys()):
            if key.lower() in SENSITIVE_HEADERS:
                headers[key] = "[Filtered]"
    if "cookies" in request:
        request["cookies"] = "[Filtered]"
    return event


def _resolve_release() -> str:
    """Resolve a release tag from env vars commonly available in containers."""
    for var in ("CKAN_RELEASE", "SENTRY_RELEASE", "GIT_COMMIT", "SOURCE_COMMIT"):
        val = os.environ.get(var)
        if val:
            return val
    return ""


def _init_sentry() -> bool:
    """Initialise the Sentry SDK from CKAN config.

    Idempotent — safe to call from both module import and IConfigurer.
    Mutates _INIT_STATE so `status` and the diagnose route can introspect.
    """
    global _INIT_STATE

    try:
        dsn = toolkit.config.get("ckanext.sentry.dsn", "")
    except Exception:
        # toolkit.config is unavailable at module import time outside a CKAN
        # app context — fall back to env vars only.
        dsn = ""
    # Fall back to env vars: ckanext-envvars-style first, then plain SENTRY_DSN
    # (the upstream Sentry SDK convention — common gotcha).
    if not dsn:
        dsn = os.environ.get("CKAN___CKANEXT__SENTRY__DSN", "") or os.environ.get(
            "SENTRY_DSN", ""
        )

    if not dsn:
        _INIT_STATE.update(
            initialized=False, reason="ckanext.sentry.dsn not configured"
        )
        log.info("Sentry DSN not configured — integration disabled")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
    except ImportError as exc:
        _INIT_STATE.update(initialized=False, reason=f"sentry-sdk import failed: {exc}")
        log.exception("sentry-sdk import failed")
        return False

    if sentry_sdk.is_initialized():
        # Already initialised by an earlier path (module import). Don't double-init,
        # but refresh _INIT_STATE so `status` reflects current settings.
        _INIT_STATE["initialized"] = True
        _INIT_STATE["reason"] = "already initialized"
        return True

    def _cfg(key, default):
        try:
            return toolkit.config.get(key, default)
        except Exception:
            env_key = "CKAN___" + key.replace(".", "__").upper()
            return os.environ.get(env_key, default)

    event_level_name = _cfg("ckanext.sentry.event_level", "ERROR").upper()
    breadcrumb_level_name = _cfg("ckanext.sentry.breadcrumb_level", "INFO").upper()
    traces_sample_rate = float(_cfg("ckanext.sentry.traces_sample_rate", "0.0"))
    environment = os.environ.get("ENVIRONMENT", "development")
    release = _resolve_release()
    server_name = os.environ.get("HOSTNAME") or socket.gethostname()

    integrations = [
        LoggingIntegration(
            level=LOG_LEVELS.get(breadcrumb_level_name, logging.INFO),
            event_level=LOG_LEVELS.get(event_level_name, logging.ERROR),
        ),
        FlaskIntegration(),
        SqlalchemyIntegration(),
    ]

    init_kwargs = dict(
        dsn=dsn,
        integrations=integrations,
        traces_sample_rate=traces_sample_rate,
        environment=environment,
        server_name=server_name,
        send_default_pii=False,
        before_send=_scrub_event,
    )
    if release:
        init_kwargs["release"] = release

    try:
        sentry_sdk.init(**init_kwargs)
    except Exception as exc:
        _INIT_STATE.update(initialized=False, reason=f"sentry_sdk.init failed: {exc}")
        log.exception("sentry_sdk.init failed")
        return False

    _INIT_STATE.update(
        initialized=True,
        reason="ok",
        dsn_host=_redact_dsn(dsn),
        environment=environment,
        release=release or None,
        event_level=event_level_name,
        breadcrumb_level=breadcrumb_level_name,
        traces_sample_rate=traces_sample_rate,
        integrations=[type(i).__name__ for i in integrations],
        sdk_version=getattr(sentry_sdk, "VERSION", None),
        server_name=server_name,
    )
    log.info(
        "Sentry initialized (env=%s, events=%s, breadcrumbs=%s, traces=%s)",
        environment,
        event_level_name,
        breadcrumb_level_name,
        traces_sample_rate,
    )
    return True


# Attempt early init at module import — catches errors that happen before
# IConfigurer.update_config runs (e.g. plugin load order issues, worker boot
# crashes under uWSGI --lazy-apps). Safe because _init_sentry() is idempotent
# and gracefully no-ops when toolkit.config isn't ready yet.
try:
    _init_sentry()
except Exception:
    log.exception("Early Sentry init failed; will retry from update_config")


class SentryPlugin(plugins.SingletonPlugin):
    """CKAN plugin that initializes Sentry SDK and forwards logs."""

    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.IClick)
    plugins.implements(plugins.IBlueprint)

    def update_config(self, config_):
        # Safety-net re-init: by now toolkit.config is fully populated, so this
        # picks up DSN values that weren't visible at module import time.
        _init_sentry()

    def get_commands(self):
        return [sentry]

    def get_blueprint(self):
        return _build_blueprint()


# ---------------------------------------------------------------------------
# Blueprint — sysadmin-only diagnostic endpoint
# ---------------------------------------------------------------------------


def _build_blueprint() -> Blueprint:
    bp = Blueprint("ckanext_sentry", __name__, url_prefix="/api/sentry")

    @bp.route("/_diagnose", methods=["GET"])
    def diagnose():
        if not _is_sysadmin():
            return jsonify({"error": "sysadmin required"}), 403
        return jsonify(_status_payload())

    return bp


def _is_sysadmin() -> bool:
    try:
        user = toolkit.current_user
        return bool(user and getattr(user, "sysadmin", False))
    except Exception:
        return False


def _status_payload() -> dict:
    return {
        "initialized": _INIT_STATE["initialized"],
        "reason": _INIT_STATE["reason"],
        "dsn_host": _INIT_STATE.get("dsn_host"),
        "environment": _INIT_STATE.get("environment"),
        "release": _INIT_STATE.get("release"),
        "event_level": _INIT_STATE.get("event_level"),
        "breadcrumb_level": _INIT_STATE.get("breadcrumb_level"),
        "traces_sample_rate": _INIT_STATE.get("traces_sample_rate"),
        "integrations": _INIT_STATE.get("integrations", []),
        "sdk_version": _INIT_STATE.get("sdk_version"),
        "server_name": _INIT_STATE.get("server_name"),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@click.group()
def sentry():
    """Sentry diagnostics commands."""


@sentry.command("status")
def sentry_status():
    """Print the current Sentry SDK state."""
    if not _INIT_STATE["initialized"]:
        _init_sentry()

    payload = _status_payload()
    width = max(len(k) for k in payload)
    for k, v in payload.items():
        click.echo(f"{k.ljust(width)} : {v}")

    if not payload["initialized"]:
        raise SystemExit(1)


@sentry.command("check")
@click.option(
    "--connect", is_flag=True, help="Attempt an HTTPS HEAD against the ingest host."
)
def sentry_check(connect):
    """Validate DSN parsing, DNS resolution, and (optionally) HTTPS reachability."""
    dsn = toolkit.config.get("ckanext.sentry.dsn", "") or os.environ.get(
        "CKAN___CKANEXT__SENTRY__DSN", ""
    ) or os.environ.get("SENTRY_DSN", "")
    if not dsn:
        click.secho(
            "ERROR: no DSN configured "
            "(ckanext.sentry.dsn / CKAN___CKANEXT__SENTRY__DSN / SENTRY_DSN).",
            fg="red",
        )
        raise SystemExit(1)

    parsed = urllib.parse.urlparse(dsn)
    if not parsed.hostname or parsed.scheme not in ("http", "https"):
        click.secho(f"ERROR: DSN does not parse as a URL: {_redact_dsn(dsn)}", fg="red")
        raise SystemExit(2)
    click.secho(f"DSN parsed: {_redact_dsn(dsn)}", fg="green")

    try:
        ip = socket.gethostbyname(parsed.hostname)
        click.secho(f"DNS:        {parsed.hostname} -> {ip}", fg="green")
    except socket.gaierror as exc:
        click.secho(f"DNS FAILED: {parsed.hostname} ({exc})", fg="red")
        raise SystemExit(3)

    if connect:
        # Probe the actual project ingest envelope path — DSN path holds the
        # project id (e.g. /4511316015054848). A 4xx response is fine: it
        # proves the host is up, the project is routed, and egress works.
        # The ingest endpoint only accepts POST with auth, so HEAD will return
        # 405 (method not allowed) on a healthy ingest.
        project_id = parsed.path.lstrip("/")
        url = (
            f"{parsed.scheme}://{parsed.hostname}/api/{project_id}/envelope/"
            if project_id
            else f"{parsed.scheme}://{parsed.hostname}/"
        )
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=5) as resp:
                click.secho(f"HTTPS HEAD {url} -> {resp.status}", fg="green")
        except urllib.error.HTTPError as exc:
            # Any HTTP response (incl. 4xx/5xx) means the host is reachable
            # and TLS works. That's all `--connect` is meant to prove.
            click.secho(
                f"HTTPS HEAD {url} -> {exc.code} (egress OK; "
                f"non-2xx is normal for ingest endpoints)",
                fg="green",
            )
        except urllib.error.URLError as exc:
            click.secho(f"HTTPS FAILED: {url} ({exc.reason})", fg="red")
            raise SystemExit(4)


@sentry.command("test")
@click.option(
    "--kind",
    type=click.Choice(["exception", "message", "log"], case_sensitive=False),
    default="exception",
    show_default=True,
    help="What kind of test event to send.",
)
@click.option(
    "--message",
    default=None,
    help="Custom message text (overrides the default test string).",
)
@click.option(
    "--unhandled",
    is_flag=True,
    help="Let the exception propagate so the local traceback is also printed. "
    "Only applies to --kind=exception.",
)
def sentry_test(kind, message, unhandled):
    """Send a test event to Sentry to verify the integration is working."""
    import sentry_sdk

    dsn = toolkit.config.get("ckanext.sentry.dsn", "") or os.environ.get(
        "CKAN___CKANEXT__SENTRY__DSN", ""
    ) or os.environ.get("SENTRY_DSN", "")
    if not dsn:
        click.secho(
            "ERROR: no DSN configured "
            "(ckanext.sentry.dsn / CKAN___CKANEXT__SENTRY__DSN / SENTRY_DSN).",
            fg="red",
        )
        raise SystemExit(1)

    if not sentry_sdk.is_initialized():
        _init_sentry()

    kind = kind.lower()
    default_msg = "ckanext-sentry test " + kind
    text = message or default_msg

    if kind == "exception":
        if unhandled:
            # Flush eagerly because the raise below short-circuits the
            # post-block flush.
            click.secho(
                f"Raising unhandled RuntimeError: {text!r}", fg="yellow"
            )
            sentry_sdk.flush(timeout=10)
            raise RuntimeError(text)
        try:
            raise RuntimeError(text)
        except RuntimeError:
            event_id = sentry_sdk.capture_exception()
    elif kind == "message":
        event_id = sentry_sdk.capture_message(text, level="warning")
    elif kind == "log":
        # Routes through LoggingIntegration — verifies the log->event pipeline.
        log.error(text)
        event_id = "(via LoggingIntegration)"

    sentry_sdk.flush(timeout=10)
    click.secho(
        f"Test event ({kind}) sent to Sentry. event_id={event_id}", fg="green"
    )


@sentry.command("raise")
@click.option(
    "--message",
    default="ckanext-sentry deliberate test error",
    show_default=True,
    help="Exception message.",
)
def sentry_raise(message):
    """Raise an uncaught RuntimeError — Sentry's global excepthook captures it.

    Unlike `test --kind=exception` (which calls capture_exception explicitly),
    this exits the process with a real Python traceback. Use it to verify the
    full unhandled-exception path: SDK excepthook → ingest → dashboard.
    """
    import sentry_sdk

    dsn = toolkit.config.get("ckanext.sentry.dsn", "") or os.environ.get(
        "CKAN___CKANEXT__SENTRY__DSN", ""
    ) or os.environ.get("SENTRY_DSN", "")
    if not dsn:
        click.secho(
            "ERROR: no DSN configured "
            "(ckanext.sentry.dsn / CKAN___CKANEXT__SENTRY__DSN / SENTRY_DSN).",
            fg="red",
        )
        raise SystemExit(1)

    if not sentry_sdk.is_initialized():
        _init_sentry()

    click.secho(
        f"Raising RuntimeError: {message!r} (Sentry will flush on exit)",
        fg="yellow",
    )
    raise RuntimeError(message)


# Allow `python -m ckanext.sentry.plugin status|check|test` so deployments with
# a broken CKAN app load (where `ckan -c ckan.ini` can't boot) can still run
# Sentry diagnostics. Reads config from env vars only on this path.
if __name__ == "__main__":
    sentry()
