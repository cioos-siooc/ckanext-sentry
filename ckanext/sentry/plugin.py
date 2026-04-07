import logging
import os

import click
import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit

log = logging.getLogger(__name__)

# Map string level names to logging constants
LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def _init_sentry():
    """Initialise the Sentry SDK from CKAN config.  Returns True on success."""
    dsn = toolkit.config.get("ckanext.sentry.dsn", "")
    if not dsn:
        log.info(
            "Sentry DSN not configured (ckanext.sentry.dsn) "
            "- Sentry integration disabled"
        )
        return False

    import sentry_sdk
    from sentry_sdk.integrations.logging import LoggingIntegration

    event_level_name = toolkit.config.get(
        "ckanext.sentry.event_level", "ERROR"
    ).upper()
    breadcrumb_level_name = toolkit.config.get(
        "ckanext.sentry.breadcrumb_level", "INFO"
    ).upper()
    traces_sample_rate = float(
        toolkit.config.get("ckanext.sentry.traces_sample_rate", "0.0")
    )
    environment = os.environ.get("ENVIRONMENT", "development")

    event_level = LOG_LEVELS.get(event_level_name, logging.ERROR)
    breadcrumb_level = LOG_LEVELS.get(breadcrumb_level_name, logging.INFO)

    sentry_logging = LoggingIntegration(
        level=breadcrumb_level,
        event_level=event_level,
    )

    init_kwargs = dict(
        dsn=dsn,
        integrations=[sentry_logging],
        traces_sample_rate=traces_sample_rate,
    )

    if environment:
        init_kwargs["environment"] = environment

    sentry_sdk.init(**init_kwargs)

    log.info(
        "Sentry initialized (events=%s, breadcrumbs=%s, traces_sample_rate=%s)",
        event_level_name,
        breadcrumb_level_name,
        traces_sample_rate,
    )
    return True


class SentryPlugin(plugins.SingletonPlugin):
    """CKAN plugin that initializes Sentry SDK and forwards logs."""

    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.IClick)

    # IConfigurer

    def update_config(self, config_):
        _init_sentry()

    # IClick

    def get_commands(self):
        return [sentry]


@click.group()
def sentry():
    """Sentry diagnostics commands."""


@sentry.command("test")
def sentry_test():
    """Send a test event to Sentry to verify the integration is working."""
    import sentry_sdk

    dsn = toolkit.config.get("ckanext.sentry.dsn", "")
    if not dsn:
        click.secho(
            "ERROR: ckanext.sentry.dsn is not set. "
            "Configure it in your .env file and restart.",
            fg="red",
        )
        raise SystemExit(1)

    # Ensure Sentry is initialised (covers the case where the CLI is invoked
    # before update_config runs, e.g. via `ckan sentry test`).
    if not sentry_sdk.is_initialized():
        _init_sentry()

    try:
        raise RuntimeError(
            "ckanext-sentry test event — if you see this in Sentry, "
            "the integration is working!"
        )
    except RuntimeError:
        sentry_sdk.capture_exception()

    sentry_sdk.flush(timeout=10)
    click.secho(
        "Test event sent to Sentry. Check your Sentry dashboard.",
        fg="green",
    )
