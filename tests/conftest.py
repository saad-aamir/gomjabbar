"""Shared pytest setup: the `postgres` marker and when its tests are skipped.

What: tests marked `postgres` need MCPMark's PostgreSQL 17 container (Docker). They are
skipped in CI, and anywhere the container cannot be started, with the reason printed.
Why: SPEC 13 keeps Docker out of CI. Without this, the tests started the container on the
GitHub runner themselves, which both broke that rule and made CI depend on downloading
MCPMark's sample databases.
How: GitHub Actions sets CI=true; `pytest -rs` in the workflow prints the skip reasons.
"""

import os

import pytest

CI_REASON = (
    "needs Docker for MCPMark's PostgreSQL 17 container, which CI does not run (SPEC 13); "
    "run these locally or in a cloud session"
)


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "postgres: needs the PostgreSQL container (skipped in CI and without Docker)"
    )


def pytest_collection_modifyitems(config, items):
    postgres_items = [item for item in items if "postgres" in item.keywords]
    if not postgres_items:
        return
    if os.environ.get("CI") == "true":
        reason = CI_REASON
    else:
        # Imported here: only needed when postgres tests were collected.
        from gomjabbar.sandbox import postgres

        if postgres.service_available():
            return
        reason = "no PostgreSQL container available (is the Docker daemon running?)"
    for item in postgres_items:
        item.add_marker(pytest.mark.skip(reason=reason))
