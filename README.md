# ckanext-sentry

`ckanext-sentry` is a small CKAN extension that initializes the Sentry Python SDK during CKAN startup and forwards CKAN log output to Sentry using `LoggingIntegration`.

It is intended for operational monitoring in CKAN deployments where application errors and higher-severity log messages should be visible in Sentry.

## What it does

- Initializes `sentry-sdk` when the CKAN plugin loads
- Sends CKAN log records to Sentry as events
- Stores lower-severity log records as Sentry breadcrumbs
- Exposes a CLI command to send a test event

## What it does not do

This extension currently focuses on Sentry SDK initialization and logging integration. It does not add extra CKAN UI pages, templates, or custom middleware.

## Requirements

- CKAN
- Python 3
- `sentry-sdk`

## Installation

Install the extension in the same Python environment as CKAN:

```bash
pip install -e .
pip install -r requirements.txt
```

If you are using this repository's Docker setup, the extension is already installed by [ckan/Dockerfile](../../ckan/Dockerfile).

## Enable the plugin

Add `sentry` to `ckan.plugins`.

Example in `ckan.ini`:

```ini
ckan.plugins = sentry
```

If you use `ckanext-envvars`, the equivalent environment variable pattern in this repository is:

```env
CKAN__PLUGINS="... sentry ..."
```

## Configuration

The extension reads the following CKAN config keys:

- `ckanext.sentry.dsn`
- `ckanext.sentry.event_level`
- `ckanext.sentry.breadcrumb_level`
- `ckanext.sentry.traces_sample_rate`

It also reads the process environment variable:

- `ENVIRONMENT`

If `ckanext.sentry.dsn` is empty, the extension logs that Sentry is disabled and does nothing else.

### Default values

- `ckanext.sentry.event_level = ERROR`
- `ckanext.sentry.breadcrumb_level = INFO`
- `ckanext.sentry.traces_sample_rate = 0.0`
- `ENVIRONMENT = development`

### Example `ckan.ini`

```ini
ckan.plugins = sentry

ckanext.sentry.dsn = https://examplePublicKey@o0.ingest.sentry.io/0
ckanext.sentry.event_level = ERROR
ckanext.sentry.breadcrumb_level = INFO
ckanext.sentry.traces_sample_rate = 0.0
```

### Example with environment variables

In this repository, Sentry is configured through environment variables:

```env
ENVIRONMENT=production
CKAN__PLUGINS="... sentry ..."
CKAN___CKANEXT__SENTRY__DSN=https://examplePublicKey@o0.ingest.sentry.io/0
CKAN___CKANEXT__SENTRY__EVENT_LEVEL=ERROR
CKAN___CKANEXT__SENTRY__BREADCRUMB_LEVEL=INFO
CKAN___CKANEXT__SENTRY__TRACES_SAMPLE_RATE=0.0
```

## CLI

The extension registers a CKAN CLI group named `sentry`.

Send a test event:

```bash
ckan -c /path/to/ckan.ini sentry test
```

The command will:

- fail if `ckanext.sentry.dsn` is not configured
- initialize Sentry if needed
- capture a test exception
- flush the event to Sentry before exiting

In this Docker workspace, the parent repository already provides a helper command in [justfile](../../justfile):

```bash
just test-sentry
```

## How log forwarding works

The extension configures Sentry's `LoggingIntegration` with two thresholds:

- `breadcrumb_level`: log records at or above this level become breadcrumbs
- `event_level`: log records at or above this level become Sentry events

For example, with the defaults:

- `INFO`, `WARNING`, and `ERROR` logs become breadcrumbs
- `ERROR` and `CRITICAL` logs are sent as Sentry events

## Development notes

- Entry point: `sentry=ckanext.sentry.plugin:SentryPlugin`
- Plugin class: `SentryPlugin`
- CLI command group: `sentry`

Core implementation lives in [ckanext/sentry/plugin.py](ckanext/sentry/plugin.py).
