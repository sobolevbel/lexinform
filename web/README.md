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
