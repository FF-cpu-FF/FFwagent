"""Benachrichtigungen: Telegram, Discord, Slack, E-Mail.

Alle Kanäle erben von `Kanal` und werden über `aktive_kanaele()` gesammelt.
Ein Kanal ist genau dann aktiv, wenn seine Umgebungsvariablen gesetzt sind –
es gibt keinen zweiten Schalter, der zusätzlich stimmen muss.

Push (etwa ntfy oder Pushover) ergänzt man als weitere Unterklasse plus einen
Eintrag in `KANAELE`; am Aufrufer ändert sich nichts.
"""
from __future__ import annotations

import html
import os
import smtplib
from abc import ABC, abstractmethod
from email.message import EmailMessage

import requests
from loguru import logger

from ..models.domain import Inserat


def _zeilen(inserat: Inserat) -> list[str]:
    fakten = []
    if inserat.warmmiete:
        fakten.append(f"{inserat.warmmiete:.0f} € warm")
    elif inserat.kaltmiete:
        fakten.append(f"{inserat.kaltmiete:.0f} € kalt")
    if inserat.zimmer:
        fakten.append(f"{inserat.zimmer:g} Zi.")
    if inserat.flaeche:
        fakten.append(f"{inserat.flaeche:.0f} m²")
    if inserat.distanz_km is not None:
        fakten.append(f"{inserat.distanz_km:.1f} km zur FS")

    zeilen = [" · ".join(fakten)] if fakten else []
    if inserat.stadtteil:
        zeilen.append(f"Lage: {inserat.stadtteil}")
    if inserat.einzug_ab:
        zeilen.append(f"Einzug ab {inserat.einzug_ab:%d.%m.%Y}")
    elif inserat.einzug_rohtext:
        zeilen.append(f"Einzug: {inserat.einzug_rohtext} (unbestätigt)")
    if inserat.ki and inserat.ki.zusammenfassung:
        zeilen.append(inserat.ki.zusammenfassung)
    if inserat.ki and inserat.ki.warnsignale:
        zeilen.append("Achtung: " + "; ".join(inserat.ki.warnsignale[:3]))
    zeilen.append(f"Score {inserat.bewertung.score}/100 · Quelle {inserat.quelle}")
    return zeilen


class Kanal(ABC):
    name = "basis"

    @property
    @abstractmethod
    def aktiv(self) -> bool: ...

    @abstractmethod
    def sende(self, inserate: list[Inserat]) -> bool: ...


class Telegram(Kanal):
    name = "telegram"

    def __init__(self) -> None:
        self.token = os.getenv("TELEGRAM_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")

    @property
    def aktiv(self) -> bool:
        return bool(self.token and self.chat_id)

    def sende(self, inserate: list[Inserat]) -> bool:
        erfolg = True
        for inserat in inserate:
            text = (
                f"<b>{html.escape(inserat.titel[:100])}</b>\n"
                + "\n".join(html.escape(z) for z in _zeilen(inserat))
                + f'\n<a href="{html.escape(inserat.url)}">Inserat öffnen</a>'
            )
            try:
                antwort = requests.post(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
                    timeout=15,
                )
                if not antwort.ok:
                    logger.warning("Telegram HTTP {}: {}", antwort.status_code, antwort.text[:200])
                    erfolg = False
            except requests.RequestException as fehler:
                logger.warning("Telegram: {}", fehler)
                erfolg = False
        return erfolg


class Discord(Kanal):
    name = "discord"

    def __init__(self) -> None:
        self.webhook = os.getenv("DISCORD_WEBHOOK_URL")

    @property
    def aktiv(self) -> bool:
        return bool(self.webhook)

    def sende(self, inserate: list[Inserat]) -> bool:
        embeds = [
            {
                "title": inserat.titel[:250],
                "url": inserat.url,
                "description": "\n".join(_zeilen(inserat))[:1800],
                "color": 0x2F855A if inserat.bewertung.score >= 75 else 0x4A5568,
                "footer": {"text": f"Score {inserat.bewertung.score} · {inserat.quelle}"},
            }
            for inserat in inserate[:10]                     # Discord erlaubt max. 10
        ]
        try:
            antwort = requests.post(
                self.webhook,
                json={"content": f"{len(inserate)} neue Treffer", "embeds": embeds},
                timeout=15,
            )
            return antwort.ok
        except requests.RequestException as fehler:
            logger.warning("Discord: {}", fehler)
            return False


class Slack(Kanal):
    name = "slack"

    def __init__(self) -> None:
        self.webhook = os.getenv("SLACK_WEBHOOK_URL")

    @property
    def aktiv(self) -> bool:
        return bool(self.webhook)

    def sende(self, inserate: list[Inserat]) -> bool:
        bloecke = []
        for inserat in inserate[:20]:
            bloecke.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*<{inserat.url}|{inserat.titel[:120]}>*\n"
                        + "\n".join(_zeilen(inserat)),
                    },
                }
            )
            bloecke.append({"type": "divider"})
        try:
            antwort = requests.post(
                self.webhook,
                json={"text": f"{len(inserate)} neue Treffer", "blocks": bloecke},
                timeout=15,
            )
            return antwort.ok
        except requests.RequestException as fehler:
            logger.warning("Slack: {}", fehler)
            return False


class Email(Kanal):
    name = "email"

    def __init__(self) -> None:
        self.host = os.getenv("SMTP_HOST")
        self.port = int(os.getenv("SMTP_PORT", "587"))
        self.nutzer = os.getenv("SMTP_USER")
        self.passwort = os.getenv("SMTP_PASSWORD")
        self.von = os.getenv("SMTP_FROM") or self.nutzer
        self.an = os.getenv("SMTP_TO")

    @property
    def aktiv(self) -> bool:
        return bool(self.host and self.nutzer and self.passwort and self.an)

    def sende(self, inserate: list[Inserat]) -> bool:
        nachricht = EmailMessage()
        nachricht["Subject"] = f"{len(inserate)} neue Wohnungen in Frankfurt"
        nachricht["From"] = self.von
        nachricht["To"] = self.an
        nachricht.set_content(
            "\n\n".join(
                f"{i.titel}\n" + "\n".join(_zeilen(i)) + f"\n{i.url}" for i in inserate
            )
        )
        nachricht.add_alternative(_html_mail(inserate), subtype="html")
        try:
            with smtplib.SMTP(self.host, self.port, timeout=30) as server:
                server.starttls()
                server.login(self.nutzer, self.passwort)
                server.send_message(nachricht)
            return True
        except Exception as fehler:
            logger.warning("E-Mail: {}", fehler)
            return False


def _html_mail(inserate: list[Inserat]) -> str:
    karten = []
    for inserat in inserate:
        zeilen = "".join(f"<div>{html.escape(z)}</div>" for z in _zeilen(inserat))
        karten.append(
            f'<div style="border:1px solid #ccc;padding:14px;margin-bottom:10px;font-family:sans-serif">'
            f'<a href="{html.escape(inserat.url)}" style="font-size:16px;font-weight:600;color:#1E3E8C">'
            f"{html.escape(inserat.titel[:120])}</a>{zeilen}</div>"
        )
    return f'<html><body style="background:#f4f4f2;padding:16px">{"".join(karten)}</body></html>'


KANAELE: list[type[Kanal]] = [Telegram, Discord, Slack, Email]


def aktive_kanaele() -> list[Kanal]:
    kanaele = [k() for k in KANAELE]
    aktiv = [k for k in kanaele if k.aktiv]
    if not aktiv:
        logger.info(
            "Kein Benachrichtigungskanal konfiguriert – Treffer stehen nur im Dashboard. "
            "Siehe .env.example."
        )
    return aktiv


def benachrichtige(inserate: list[Inserat]) -> list[str]:
    """Sendet über alle aktiven Kanäle. Rückgabe: Namen der erfolgreichen."""
    if not inserate:
        return []
    erfolgreich = []
    for kanal in aktive_kanaele():
        if kanal.sende(inserate):
            erfolgreich.append(kanal.name)
            logger.info("{}: {} Treffer gemeldet", kanal.name, len(inserate))
    return erfolgreich
