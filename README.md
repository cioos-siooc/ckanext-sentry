# ckanext-sentry

`ckanext-sentry` initializes the Sentry Python SDK during CKAN startup
and forwards CKAN log output, Flask request errors, and SQLAlchemy
spans to Sentry.

It is intended for operational monitoring in CKAN deployments where
application errors, WSGI/Flask exceptions, and higher-severity log
messages should be visible in Sentry.

## What it does

- Initializes `sentry-sdk` early at module import (and again from
  `IConfigurer.update_config` as a safety net) so that worker boot-time
  errors under uWSGI `--lazy-apps` are captured.
- Enables three integrations:
  - `LoggingIntegration` — log records become breadcrumbs and events.
  - `FlaskIntegration` — uncaught view exceptions are captured with
    full HTTP request context.
  - `SqlalchemyIntegration` — DB queries appear in performance traces
    and error breadcrumbs.
- Tags each event with `environment`, `release` (when available), and
  `server_name` (container hostname).
- Scrubs sensitive headers (`Authorization`, `Cookie`,
  `X-CKAN-API-Key`, etc.) before sending.
- Exposes CLI diagnostics: `status`, `check`, `test`.
- Exposes a sysadmin-gated `/api/sentry/_diagnose` HTTP endpoint to
  validate live deployments without `docker exec`.

## Requirements

- CKAN 2.10+
- Python 3
- `sentry-sdk[flask]>=2.53,<3.0` (declared in
  [requirements.txt](requirements.txt))

## Installation

```bash
pip install -e .
pip install -r requirements.txt
```

In this repository's Docker setup, the extension is installed by
[ckan/Dockerfile](../../ckan/Dockerfile) at a pinned commit. After
making changes here, bump `CIOOS_SENTRY_COMMIT` in the Dockerfile to
pick them up in **base mode** (dev mode mounts `src/` directly).

## Enable the plugin

Add `sentry` to `ckan.plugins`:

```ini
ckan.plugins = sentry
```

Or via env vars (this repo's pattern):

```env
CKAN__PLUGINS="... sentry ..."
```

## Configuration

| Config key | Default | Notes |
|---|---|---|
| `ckanext.sentry.dsn` | (empty) | Required. Empty disables Sentry. |
| `ckanext.sentry.event_level` | `ERROR` | Threshold for events. |
| `ckanext.sentry.breadcrumb_level` | `INFO` | Threshold for breadcrumbs. |
| `ckanext.sentry.traces_sample_rate` | `0.0` | Performance sampling 0–1. |

Process environment variables also consulted:

- `ENVIRONMENT` — Sentry `environment` tag (default: `development`).
- `CKAN_RELEASE`, `SENTRY_RELEASE`, `GIT_COMMIT`, `SOURCE_COMMIT` —
  first non-empty wins; sets the Sentry `release` tag.
- `HOSTNAME` — Sentry `server_name` tag. Falls back to
  `socket.gethostname()`.
- `SENTRY_DSN` — fallback DSN if `ckanext.sentry.dsn` and
  `CKAN___CKANEXT__SENTRY__DSN` are both unset.

### Example `.env`

```env
ENVIRONMENT=production
CKAN_RELEASE=2026.05.01-abc1234
CKAN__PLUGINS="... sentry ..."
CKAN___CKANEXT__SENTRY__DSN=https://examplePublicKey@o0.ingest.sentry.io/0
CKAN___CKANEXT__SENTRY__EVENT_LEVEL=ERROR
CKAN___CKANEXT__SENTRY__BREADCRUMB_LEVEL=INFO
CKAN___CKANEXT__SENTRY__TRACES_SAMPLE_RATE=0.1
```

## CLI

The extension registers a CKAN CLI group named `sentry`. It can be invoked
two ways:

- **Standalone** — `python3 -m ckanext.sentry.plugin <subcommand>` reads DSN
  from `SENTRY_DSN` / `CKAN___CKANEXT__SENTRY__DSN` and runs **without** loading
  CKAN's app. Use this for diagnostics on deployments where `ckan -c ckan.ini`
  fails (broken plugin, schema, or DB).
- **Via CKAN** — `ckan -c /srv/app/ckan.ini sentry <subcommand>` reads from
  `ckan.ini` and only works on a healthy CKAN install.

The `[ckan.click_command]` setuptools entry point makes the `sentry` group
available even if the `sentry` plugin isn't enabled in `ckan.plugins`.

### `ckan sentry status`

Print the current SDK state — initialized?, environment, release,
integrations loaded, sdk version, and (redacted) DSN host. Exits
non-zero if Sentry is not initialized. Use this as a deployment smoke
test.

```bash
ckan -c /srv/app/ckan.ini sentry status
```

### `ckan sentry check [--connect]`

Validate that the DSN is parseable and the ingest hostname resolves
via DNS. Pass `--connect` to additionally attempt an HTTPS HEAD to
confirm container egress (a non-2xx response is OK; we only fail on
DNS/parse errors).

```bash
ckan -c /srv/app/ckan.ini sentry check --connect
```

### `ckan sentry test [--kind=exception|message|log]`

Send a test event:

- `exception` (default): captures a raised `RuntimeError`.
- `message`: sends a `capture_message` at warning level.
- `log`: emits a `log.error()` — exercises the `LoggingIntegration`
  path end-to-end.

```bash
ckan -c /srv/app/ckan.ini sentry test --kind=log
```

## Justfile shortcuts

This repository's [justfile](../../justfile) wraps the commands so you
can run them against any compose service:

```bash
just sentry-status                       # dev mode default
just sentry-status ckan docker-compose.yml  # base mode
just sentry-check                        # DSN + DNS only
just sentry-check ckan-dev docker-compose.dev.yml --connect
just test-sentry                         # default kind=exception
just test-sentry log
just sentry-diagnose https://my-deploy <api-token>
```

## HTTP diagnostic endpoint

`GET /api/sentry/_diagnose` returns the same JSON payload as
`sentry status`. Access is gated to sysadmin users only — it relies on
CKAN's existing user authentication, so call it with a sysadmin API
token:

```bash
curl -H "Authorization: <api-token>" \
     https://my-deploy/api/sentry/_diagnose | jq
```

This is the recommended way to verify Sentry on a deployment when you
can't `docker exec` into the container (e.g. behind Coolify or a
managed orchestrator).

## How log forwarding works

`LoggingIntegration` uses two thresholds:

- `breadcrumb_level`: log records at or above this level become
  breadcrumbs.
- `event_level`: log records at or above this level are sent as Sentry
  events.

With the defaults: `INFO`/`WARNING`/`ERROR` logs become breadcrumbs;
`ERROR`/`CRITICAL` are sent as events.

## How WSGI errors are captured

`FlaskIntegration` registers via Flask's `got_request_exception`
signal, so any uncaught exception in a CKAN view (controller,
blueprint, or extension route) becomes a Sentry event with full
request context: URL, method, headers (scrubbed), user. This works
whether CKAN runs under uWSGI (base mode) or the Werkzeug dev server
(dev mode).

What it does **not** catch automatically: uWSGI worker harakiri kills
(SIGKILL) — those bypass Python entirely. To capture those, you need
a uWSGI-level signal handler that converts harakiri into a CRITICAL
log record before the worker dies. That is intentionally out of scope
here; raise a separate task if you need it.

## Development notes

- Entry point: `sentry=ckanext.sentry.plugin:SentryPlugin`
  ([setup.py](setup.py))
- Plugin class: `SentryPlugin` — implements `IConfigurer`, `IClick`,
  `IBlueprint`.
- Module-level `_INIT_STATE` dict is shared between init, the `status`
  CLI, and the diagnose route.
- Core implementation:
  [ckanext/sentry/plugin.py](ckanext/sentry/plugin.py).
