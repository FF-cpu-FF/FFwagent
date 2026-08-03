"""Umgebungsvariablen und Geheimnisse.

Hier – am Rand des Systems, wo fremde Werte hereinkommen – ist Pydantic am
Platz. Das Suchprofil selbst liegt in `profil.py` und bleibt frameworkfrei,
damit der Kern ohne Installation testbar ist (siehe ARCHITEKTUR.md).
"""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Einstellungen(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    datenbank_url: str = Field("sqlite:///data/wohnungen.db", alias="DATENBANK_URL")
    profil_pfad: str = Field("config/suchprofil.yml", alias="PROFIL_PFAD")
    export_pfad: str = Field("docs/data/listings.json", alias="EXPORT_PFAD")

    # KI – optional. Ohne Schlüssel läuft alles, nur ohne Zusammenfassungen.
    anthropic_api_key: str | None = Field(None, alias="ANTHROPIC_API_KEY")
    openai_api_key: str | None = Field(None, alias="OPENAI_API_KEY")
    llm_modell: str | None = Field(None, alias="LLM_MODELL")

    # Portale
    is24_api_key: str | None = Field(None, alias="IS24_API_KEY")

    # Benachrichtigung – jeder Kanal aktiviert sich selbst, sobald er
    # vollständig konfiguriert ist.
    telegram_token: str | None = Field(None, alias="TELEGRAM_TOKEN")
    telegram_chat_id: str | None = Field(None, alias="TELEGRAM_CHAT_ID")
    discord_webhook_url: str | None = Field(None, alias="DISCORD_WEBHOOK_URL")
    slack_webhook_url: str | None = Field(None, alias="SLACK_WEBHOOK_URL")
    smtp_host: str | None = Field(None, alias="SMTP_HOST")
    smtp_port: int = Field(587, alias="SMTP_PORT")
    smtp_user: str | None = Field(None, alias="SMTP_USER")
    smtp_password: str | None = Field(None, alias="SMTP_PASSWORD")
    smtp_from: str | None = Field(None, alias="SMTP_FROM")
    smtp_to: str | None = Field(None, alias="SMTP_TO")

    mindestscore_meldung: int = Field(0, alias="MINDESTSCORE_MELDUNG")
    geocoding_aktiv: bool = Field(False, alias="GEOCODING_AKTIV")


einstellungen = Einstellungen()
