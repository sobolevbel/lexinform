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
