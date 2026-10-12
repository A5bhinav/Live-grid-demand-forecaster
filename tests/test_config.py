import pytest

from gridcast import config
from gridcast.config import Settings


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EIA_API_KEY", "abc123")
    settings = Settings(_env_file=None)
    assert settings.require_eia_api_key() == "abc123"


def test_secrets_are_masked_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EIA_API_KEY", "abc123")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-secret")
    rendered = repr(Settings(_env_file=None))
    assert "abc123" not in rendered
    assert "service-secret" not in rendered


@pytest.mark.parametrize("value", [None, ""])
def test_missing_or_blank_eia_key_raises(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv("EIA_API_KEY", raising=False)
    else:
        monkeypatch.setenv("EIA_API_KEY", value)
    with pytest.raises(RuntimeError, match="EIA_API_KEY is not set"):
        Settings(_env_file=None).require_eia_api_key()


def test_weather_points_are_inside_california() -> None:
    assert len(config.LOCATIONS) == 6
    for location in config.LOCATIONS:
        assert 32 < location.latitude < 42
        assert -125 < location.longitude < -114
