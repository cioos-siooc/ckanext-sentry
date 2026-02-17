import logging

import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit

log = logging.getLogger(__name__)

# Map string level names to logging constants
LOG_LEVELS = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL,
}


class SentryPlugin(plugins.SingletonPlugin):
    """CKAN plugin that initializes Sentry SDK and forwards logs."""

    plugins.implements(plugins.IConfigurer)

    def update_config(self, config_):
        dsn = toolkit.config.get('ckanext.sentry.dsn', '')
        if not dsn:
            log.info('Sentry DSN not configured (ckanext.sentry.dsn) '
                     '- Sentry integration disabled')
            return

        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration

        event_level_name = toolkit.config.get(
            'ckanext.sentry.event_level', 'ERROR').upper()
        breadcrumb_level_name = toolkit.config.get(
            'ckanext.sentry.breadcrumb_level', 'INFO').upper()
        traces_sample_rate = float(toolkit.config.get(
            'ckanext.sentry.traces_sample_rate', '0.0'))
        environment = toolkit.config.get('ckanext.sentry.environment', '')

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
            init_kwargs['environment'] = environment

        sentry_sdk.init(**init_kwargs)

        log.info('Sentry initialized (events=%s, breadcrumbs=%s, '
                 'traces_sample_rate=%s)',
                 event_level_name, breadcrumb_level_name, traces_sample_rate)
