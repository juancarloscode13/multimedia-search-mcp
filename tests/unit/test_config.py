"""Carga de configuración: prioridad de fuentes, validación y no filtración."""

from __future__ import annotations

from pathlib import Path

import pytest

from multimedia_search_mcp.config import ProwlarrSettings, Settings, find_env_file
from multimedia_search_mcp.errors import ConfigurationError

API_KEY = "local-env-key-000111222333"


class TestSettingsLoad:
    def test_loads_from_mapping(self) -> None:
        settings = Settings.load(
            environ={
                "PROWLARR_BASE_URL": "http://127.0.0.1:9696/",
                "PROWLARR_API_KEY": API_KEY,
            }
        )
        assert settings.prowlarr.configured is True
        assert settings.prowlarr.base_url == "http://127.0.0.1:9696"
        assert settings.prowlarr.api_key_value == API_KEY
        assert settings.prowlarr.timeout_seconds == 10.0

    def test_missing_values_are_reported_not_raised(self) -> None:
        settings = Settings.load(environ={})
        assert settings.prowlarr.configured is False
        assert settings.prowlarr.missing_variables == ["PROWLARR_BASE_URL", "PROWLARR_API_KEY"]

    def test_require_configured_names_missing_variables(self) -> None:
        settings = Settings.load(environ={})
        with pytest.raises(ConfigurationError) as excinfo:
            settings.prowlarr.require_configured()
        assert "PROWLARR_BASE_URL" in str(excinfo.value)
        assert "PROWLARR_API_KEY" in str(excinfo.value)

    @pytest.mark.parametrize("base_url", ["prowlarr:9696", "ftp://host", "   "])
    def test_invalid_base_url_is_rejected(self, base_url: str) -> None:
        with pytest.raises(ConfigurationError):
            Settings.load(environ={"PROWLARR_BASE_URL": base_url, "PROWLARR_API_KEY": API_KEY})

    @pytest.mark.parametrize("timeout", ["cero", "0", "999"])
    def test_invalid_timeout_is_rejected(self, timeout: str) -> None:
        with pytest.raises(ConfigurationError):
            Settings.load(
                environ={
                    "PROWLARR_BASE_URL": "http://127.0.0.1:9696",
                    "PROWLARR_API_KEY": API_KEY,
                    "PROWLARR_TIMEOUT": timeout,
                }
            )

    def test_custom_timeout(self) -> None:
        settings = Settings.load(
            environ={
                "PROWLARR_BASE_URL": "http://127.0.0.1:9696",
                "PROWLARR_API_KEY": API_KEY,
                "PROWLARR_TIMEOUT": "30",
            }
        )
        assert settings.prowlarr.timeout_seconds == 30.0

    def test_api_key_is_not_in_repr(self) -> None:
        settings = Settings.load(
            environ={"PROWLARR_BASE_URL": "http://127.0.0.1:9696", "PROWLARR_API_KEY": API_KEY}
        )
        assert API_KEY not in repr(settings)
        assert API_KEY not in str(settings.prowlarr)


class TestEnvFile:
    def test_find_env_file_explicit_path(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("PROWLARR_BASE_URL=http://x\n", encoding="utf-8")
        assert find_env_file(env_file) == env_file

    def test_find_env_file_missing_path(self, tmp_path: Path) -> None:
        assert find_env_file(tmp_path / "nope.env") is None

    def test_explicit_missing_env_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError):
            Settings.load(env_file=tmp_path / "no-existe.env")

    def test_dotenv_file_is_loaded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "PROWLARR_BASE_URL=http://127.0.0.1:8080\n"
            f"PROWLARR_API_KEY={API_KEY}\n",
            encoding="utf-8",
        )
        monkeypatch.delenv("PROWLARR_BASE_URL", raising=False)
        monkeypatch.delenv("PROWLARR_API_KEY", raising=False)

        settings = Settings.load(env_file=env_file)
        assert settings.env_file == env_file
        assert settings.prowlarr.base_url == "http://127.0.0.1:8080"
        assert settings.prowlarr.configured is True

    def test_environment_wins_over_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "PROWLARR_BASE_URL=http://from-file:9696\n"
            "PROWLARR_API_KEY=file-key-abcdef\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("PROWLARR_BASE_URL", "http://from-env:9696")
        monkeypatch.setenv("PROWLARR_API_KEY", API_KEY)

        settings = Settings.load(env_file=env_file)
        assert settings.prowlarr.base_url == "http://from-env:9696"
        assert settings.prowlarr.api_key_value == API_KEY


class TestProwlarrSettings:
    def test_secret_is_masked(self) -> None:
        settings = ProwlarrSettings(base_url="http://h:1", api_key=API_KEY)  # type: ignore[arg-type]
        assert "***" in repr(settings)
        assert settings.api_key_value == API_KEY
