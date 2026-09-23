"""Configuration loading.

These tests exist because of a real failure: `.env.example` shipped with
comma-separated `CORS_ORIGINS`, but a `List[str]` settings field is "complex"
to pydantic-settings, which JSON-decodes it *inside the dotenv source* before
any validator can run. The result was a SettingsError on `python scripts/init_db.py`
- the very first command in the README.

The lesson is narrow and worth a permanent guard: the file we tell people to
copy must actually boot the application.
"""
from __future__ import annotations

import os
import re

import pytest

from app.config import BASE_DIR, Settings

ENV_EXAMPLE = os.path.join(BASE_DIR, ".env.example")


def _parse_env_file(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, raw = line.partition("=")
            values[key.strip()] = re.sub(r"\s+#.*$", "", raw.strip()).strip('"').strip("'")
    return values


def test_env_example_exists():
    assert os.path.isfile(ENV_EXAMPLE), ".env.example is part of the setup instructions"


def test_settings_load_from_the_shipped_env_example():
    """`cp .env.example .env` must produce a working configuration.

    Constructing Settings is the whole point: the original bug raised a
    SettingsError here, before a single line of application code ran.

    Note that real environment variables outrank the dotenv file (correct
    pydantic-settings precedence), and the test fixtures set some of them, so
    this asserts on values the file alone owns.
    """
    settings = Settings(_env_file=ENV_EXAMPLE)

    assert settings.app_name
    assert settings.db_host == "127.0.0.1"
    assert settings.db_port == 3306
    assert settings.db_name == "sentinel_ip"
    assert settings.max_upload_mb == 25
    assert settings.default_autonomy_tier == 1
    assert settings.max_autonomy_tier == 3
    assert settings.simulated_filing is True


@pytest.mark.parametrize("key", ["CORS_ORIGINS", "ALLOWED_UPLOAD_EXTENSIONS"])
def test_env_example_documents_the_comma_separated_fields(key):
    """Guard the exact shape that broke: a bare comma-separated list."""
    values = _parse_env_file(ENV_EXAMPLE)
    assert key in values, f"{key} should be documented in .env.example"
    assert "," in values[key], f"{key} is the comma-separated form that regressed"
    assert not values[key].startswith("["), f"{key} must not require JSON syntax"


def test_comma_separated_values_parse_into_lists():
    settings = Settings(_env_file=ENV_EXAMPLE)

    assert isinstance(settings.cors_origins, list)
    assert "http://localhost:8000" in settings.cors_origins
    assert all(o.startswith("http") for o in settings.cors_origins)

    exts = settings.allowed_upload_extensions
    assert isinstance(exts, list)
    assert ".png" in exts and ".pdf" in exts and ".xlsx" in exts
    assert all(e.startswith(".") and e == e.lower() for e in exts)


@pytest.mark.parametrize("raw,expected", [
    ("a,b,c", ["a", "b", "c"]),
    (" a , b ,, c ", ["a", "b", "c"]),          # blanks and padding
    ("", []),
    ("single", ["single"]),
    ('["a","b"]', ["a", "b"]),                   # JSON form still accepted
    ("[not json", ["[not json"]),                # malformed JSON degrades safely
])
def test_csv_parser_handles_the_shapes_people_actually_write(raw, expected):
    assert Settings._split_csv(raw) == expected


def test_extension_list_is_normalised():
    """`PNG, .jpg` and `.png,.jpg` must mean the same thing."""
    settings = Settings(_env_file=None, allowed_upload_extensions="PNG, .JPG , pdf")
    assert settings.allowed_upload_extensions == [".png", ".jpg", ".pdf"]


def test_every_documented_variable_maps_to_a_real_setting():
    """Catch drift: a variable in .env.example the code silently ignores."""
    documented = set(_parse_env_file(ENV_EXAMPLE))
    known = set()
    for name, field in Settings.model_fields.items():
        known.add(name.upper())
        alias = getattr(field, "validation_alias", None)
        if isinstance(alias, str):
            known.add(alias.upper())

    unknown = documented - known
    assert not unknown, (
        f".env.example documents variables the application never reads: "
        f"{sorted(unknown)}"
    )


def test_production_startup_check_rejects_a_weak_configuration():
    """The startup guard must actually fire, not just exist."""
    from app.main import _startup_checks

    settings = Settings(
        _env_file=None, app_env="production", debug=True,
        secret_key="CHANGE_ME_short", cookie_secure=False,
    )
    import app.main as main_module

    original = main_module.settings
    main_module.settings = settings
    try:
        with pytest.raises(RuntimeError) as exc:
            _startup_checks()
        message = str(exc.value)
        assert "SECRET_KEY" in message
        assert "COOKIE_SECURE" in message
        assert "DEBUG" in message
    finally:
        main_module.settings = original
