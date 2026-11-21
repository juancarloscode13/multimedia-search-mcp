"""Carga y validación de la configuración.

Las credenciales se leen del fichero ``.env`` del proyecto (o de variables de
entorno ya presentes en el proceso). Nunca se escriben en el código ni se
imprimen: la API Key se guarda como :class:`pydantic.SecretStr`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError, field_validator

from .errors import ConfigurationError

__all__ = ["DEFAULT_TIMEOUT_SECONDS", "ProwlarrSettings", "Settings", "find_env_file"]

DEFAULT_TIMEOUT_SECONDS = 10.0
MIN_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 120.0

# Raíz del repositorio cuando se ejecuta desde el árbol de fuentes
# (``<repo>/src/multimedia_search_mcp/config.py``). Se usa solo como último
# recurso para localizar el ``.env`` cuando el directorio de trabajo no es el
# del proyecto (por ejemplo, al lanzarlo como servidor MCP).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE_VARIABLE = "PROWLARR_ENV_FILE"


def find_env_file(explicit: Path | None = None) -> Path | None:
    """Localiza el ``.env`` por orden de prioridad, sin leer su contenido."""
    if explicit is not None:
        return explicit if explicit.is_file() else None

    candidates = [Path.cwd() / ".env", PROJECT_ROOT / ".env"]
    override = os.environ.get(ENV_FILE_VARIABLE)
    if override:
        # La variable explícita manda sobre la heurística de rutas.
        candidates.insert(0, Path(override).expanduser())

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


class ProwlarrSettings(BaseModel):
    """Configuración del proveedor Prowlarr."""

    model_config = ConfigDict(frozen=True)

    base_url: str | None = None
    api_key: SecretStr | None = None
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @field_validator("base_url")
    @classmethod
    def _clean_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().rstrip("/")
        if not cleaned:
            raise ValueError("PROWLARR_BASE_URL está vacía")
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError("PROWLARR_BASE_URL debe empezar por http:// o https://")
        return cleaned

    @field_validator("api_key")
    @classmethod
    def _check_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        if not value.get_secret_value().strip():
            raise ValueError("PROWLARR_API_KEY está vacía")
        return value

    @field_validator("timeout_seconds")
    @classmethod
    def _check_timeout(cls, value: float) -> float:
        if not MIN_TIMEOUT_SECONDS <= value <= MAX_TIMEOUT_SECONDS:
            raise ValueError(
                f"PROWLARR_TIMEOUT debe estar entre {MIN_TIMEOUT_SECONDS} y {MAX_TIMEOUT_SECONDS} segundos"
            )
        return value

    @property
    def configured(self) -> bool:
        """Indica si hay credenciales suficientes para intentar consultar."""
        return self.base_url is not None and self.api_key is not None

    @property
    def missing_variables(self) -> list[str]:
        missing: list[str] = []
        if self.base_url is None:
            missing.append("PROWLARR_BASE_URL")
        if self.api_key is None:
            missing.append("PROWLARR_API_KEY")
        return missing

    @property
    def api_key_value(self) -> str:
        """Devuelve la clave en claro. Uso interno: solo para la cabecera HTTP."""
        if self.api_key is None:
            raise ConfigurationError("Falta PROWLARR_API_KEY en el entorno")
        return self.api_key.get_secret_value().strip()

    def require_configured(self) -> None:
        if self.configured:
            return
        missing = ", ".join(self.missing_variables)
        raise ConfigurationError(
            f"Configuración incompleta de Prowlarr: faltan {missing}. "
            "Copia .env.example a .env y rellena los valores."
        )


class Settings(BaseModel):
    """Configuración completa del servidor."""

    model_config = ConfigDict(frozen=True)

    prowlarr: ProwlarrSettings
    env_file: Path | None = None

    @classmethod
    def load(
        cls,
        env_file: Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> "Settings":
        """Carga la configuración desde ``.env`` y el entorno del proceso.

        Las variables ya presentes en el entorno tienen prioridad sobre el
        fichero (``override=False``), lo que permite inyectarlas desde el
        cliente MCP sin tocar el ``.env``.
        """
        source = os.environ if environ is None else environ
        resolved_env = None if environ is not None else find_env_file(env_file)
        if environ is None and env_file is not None and resolved_env is None:
            raise ConfigurationError(
                f"No existe el fichero de entorno indicado: {env_file}"
            )

        if resolved_env is not None:
            load_dotenv(resolved_env, override=False)

        raw_timeout = source.get("PROWLARR_TIMEOUT", "").strip()
        timeout = DEFAULT_TIMEOUT_SECONDS
        if raw_timeout:
            try:
                timeout = float(raw_timeout)
            except ValueError as exc:
                raise ConfigurationError(
                    "PROWLARR_TIMEOUT debe ser un número (segundos)"
                ) from exc

        api_key = source.get("PROWLARR_API_KEY")
        try:
            prowlarr = ProwlarrSettings(
                base_url=source.get("PROWLARR_BASE_URL") or None,
                api_key=SecretStr(api_key) if api_key else None,
                timeout_seconds=timeout,
            )
        except ValidationError as exc:
            details = "; ".join(
                str(error["msg"]) for error in exc.errors(include_url=False)
            )
            raise ConfigurationError(f"Configuración inválida: {details}") from exc

        return cls(prowlarr=prowlarr, env_file=resolved_env)
