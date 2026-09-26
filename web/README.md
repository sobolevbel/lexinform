# lexinform web

The website is a separate package in the repository's uv workspace. It may import the bot's pure
models and helpers; the bot never imports Django or this package.

## Local bootstrap

No `.env` file is required for the development defaults. Start PostgreSQL, install the web package
and apply migrations:

```bash
docker compose -f web/compose.local.yml up -d --wait
uv sync --package lexinform-web
uv run --package lexinform-web python web/manage.py migrate
uv run --package lexinform-web python web/manage.py web_doctor
```

Run the development server with:

```bash
uv run --package lexinform-web python web/manage.py runserver
```

The local database is exposed on loopback only. Production settings have no secret-key or database
password fallback.

## Shortcuts from the repository root

```bash
make web-sync                 # install all workspace packages and tools, as in CI
make web-serve                # development server; ARGS="127.0.0.1:8002" changes the address
make web-test                 # tests; ARGS="-k import -vv" narrows the selection
make web-lint                 # Ruff and formatting check
make web-typecheck            # strict mypy
make web-format               # apply formatting
make web-check                # lint, types, Django checks, migration drift and tests
```

Start PostgreSQL and apply local migrations with the bootstrap commands above before running
the server or full web check. The shortcuts do not start Docker or apply database migrations.

## Staff access

Run `uv run --package lexinform-web python web/manage.py createsuperuser` for the first
administrator. Open `/accounts/login/`, use the account's email, verify that email, and
enroll TOTP before entering `/admin/`. Development verification/reset mail is printed to
the console through Django 6.1 `MAILERS`; test mail stays in memory.

Create editors with `is_staff` and the required Wagtail group/page permissions. Being staff
or completing MFA does not grant publication, user management or upload permissions.
Reader signup is closed. Save recovery codes securely: each can be used once in place of TOTP.
Password reset preserves the authenticator. Admin access requires another MFA check after
12 hours; disabling TOTP through the web UI is not allowed.

If both the device and recovery codes are lost, an administrator must verify the person's
identity outside the login flow before removing that account's authenticators through a
trusted management session. This must be recorded in the operational incident log; do not
disable the middleware or remove MFA on the basis of an email reset alone. The next login
requires enrollment again.

Production delivery configuration, shared rate-limit/replay cache and operational recovery
rehearsal belong to WEB-05. Console mail and a per-process cache are development defaults,
not the production deployment configuration.
