"""Semantische Bewertung durch ein LLM.

Zweck ist ausdrücklich nicht, das Ranking zu ersetzen. Zahlen, Distanz und
Einzugstermin gehören in deterministischen Code – dort ist ein LLM schlechter
und teurer. Das Modell übernimmt, was Regeln schlecht können: einschätzen, ob
ein Grundriss für eine Zweier-WG taugt, ob ein Inserat unseriös wirkt, ob
zwischen den Zeilen ein Nachteil versteckt ist.

Deshalb ist der Einfluss auf `ranking.llm_einfluss_max` gedeckelt (Standard
±10 Punkte). Ein halluziniertes Urteil kann die Reihenfolge verschieben, aber
kein Inserat an die Spitze heben, das die harten Kriterien nicht erfüllt.

Anthropic und OpenAI werden beide unterstützt; ohne Schlüssel läuft der Agent
vollständig, nur ohne KI-Text.
"""
from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime

from loguru import logger

from ..config.profil import Suchprofil
from ..models.domain import Inserat, KiBewertung
from .prompts import ANWEISUNG, baue_nutzeranfrage


class LlmClient:
    """Dünne Hülle über Anthropic bzw. OpenAI.

    `aktiv` ist False, wenn kein Schlüssel gesetzt oder kein SDK installiert
    ist. Der Aufrufer muss das nicht abfragen – `bewerte()` gibt dann None
    zurück und der Lauf geht ohne KI-Text weiter.
    """

    def __init__(self, profil: Suchprofil, eingeschaltet: bool | None = None) -> None:
        self.profil = profil
        self.anbieter: str | None = None
        self.modell = ""
        self._client = None

        # Zählt mit, was der Lauf gekostet hat – wird im Dashboard und in der
        # Actions-Zusammenfassung angezeigt.
        self.aufrufe = 0
        self.tokens_ein = 0
        self.tokens_aus = 0

        # Der Schalter aus dem Profil lässt sich pro Lauf überstimmen
        # (--mit-ki auf der Kommandozeile, Haken im Workflow-Dialog).
        self.eingeschaltet = profil.ki.aktiv if eingeschaltet is None else eingeschaltet
        if not self.eingeschaltet:
            logger.info("KI-Bewertung ausgeschaltet – dieser Lauf verbraucht keine Tokens")
            return

        if (schluessel := os.getenv("ANTHROPIC_API_KEY")):
            try:
                import anthropic

                self._client = anthropic.Anthropic(api_key=schluessel)
                self.anbieter = "anthropic"
                self.modell = os.getenv("LLM_MODELL", "claude-sonnet-4-6")
            except ImportError:
                logger.warning("ANTHROPIC_API_KEY gesetzt, aber das anthropic-SDK fehlt")
        elif (schluessel := os.getenv("OPENAI_API_KEY")):
            try:
                import openai

                self._client = openai.OpenAI(api_key=schluessel)
                self.anbieter = "openai"
                self.modell = os.getenv("LLM_MODELL", "gpt-4o-mini")
            except ImportError:
                logger.warning("OPENAI_API_KEY gesetzt, aber das openai-SDK fehlt")

        if not self._client:
            logger.info("Keine KI-Bewertung: kein API-Schlüssel gefunden")

    @property
    def aktiv(self) -> bool:
        return self.eingeschaltet and self._client is not None

    @property
    def tokens_gesamt(self) -> int:
        return self.tokens_ein + self.tokens_aus

    def bewerte(self, inserat: Inserat) -> KiBewertung | None:
        if not self.aktiv:
            return None
        try:
            rohtext = self._frage(baue_nutzeranfrage(inserat, self.profil))
            daten = _json_aus(rohtext)
        except Exception as fehler:
            logger.warning("KI-Bewertung für {} fehlgeschlagen: {}", inserat.uid, fehler)
            return None
        if not daten:
            return None

        grenze = self.profil.ranking.llm_einfluss_max
        delta = int(daten.get("punkte_delta", 0) or 0)
        return KiBewertung(
            zusammenfassung=str(daten.get("zusammenfassung", ""))[:400],
            wg_geeignet=_dreiwertig(daten.get("wg_geeignet")),
            seriositaet=_stufe(daten.get("seriositaet")),
            lagebewertung=_stufe(daten.get("lagebewertung")),
            warnsignale=[str(w)[:120] for w in (daten.get("warnsignale") or [])][:5],
            fehlende_angaben=[str(w)[:80] for w in (daten.get("fehlende_angaben") or [])][:5],
            punkte_delta=max(-grenze, min(grenze, delta)),
            modell=self.modell,
            erzeugt_am=datetime.now(UTC),
        )

    def _frage(self, anfrage: str) -> str:
        self.aufrufe += 1

        if self.anbieter == "anthropic":
            antwort = self._client.messages.create(
                model=self.modell,
                max_tokens=800,
                system=ANWEISUNG,
                messages=[{"role": "user", "content": anfrage}],
            )
            verbrauch = getattr(antwort, "usage", None)
            if verbrauch:
                self.tokens_ein += getattr(verbrauch, "input_tokens", 0) or 0
                self.tokens_aus += getattr(verbrauch, "output_tokens", 0) or 0
            return "".join(b.text for b in antwort.content if getattr(b, "type", "") == "text")

        antwort = self._client.chat.completions.create(
            model=self.modell,
            max_tokens=800,
            messages=[
                {"role": "system", "content": ANWEISUNG},
                {"role": "user", "content": anfrage},
            ],
            response_format={"type": "json_object"},
        )
        verbrauch = getattr(antwort, "usage", None)
        if verbrauch:
            self.tokens_ein += getattr(verbrauch, "prompt_tokens", 0) or 0
            self.tokens_aus += getattr(verbrauch, "completion_tokens", 0) or 0
        return antwort.choices[0].message.content or ""


def _json_aus(text: str) -> dict | None:
    """Holt das JSON-Objekt aus der Antwort, auch wenn Backticks drumherum stehen."""
    if not text:
        return None
    saeuberlich = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        daten = json.loads(saeuberlich)
    except json.JSONDecodeError:
        klammer = re.search(r"\{.*\}", saeuberlich, re.S)
        if not klammer:
            return None
        try:
            daten = json.loads(klammer.group(0))
        except json.JSONDecodeError:
            return None
    return daten if isinstance(daten, dict) else None


def _dreiwertig(wert) -> bool | None:
    if isinstance(wert, bool):
        return wert
    if isinstance(wert, str):
        if wert.lower() in ("ja", "true", "yes"):
            return True
        if wert.lower() in ("nein", "false", "no"):
            return False
    return None


def _stufe(wert) -> int | None:
    try:
        zahl = int(wert)
    except (TypeError, ValueError):
        return None
    return max(1, min(5, zahl))
