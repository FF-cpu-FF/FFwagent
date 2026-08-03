"""Dauerbetrieb mit APScheduler – optional, nicht der Normalbetrieb.

Der Agent ist auf manuelle Auslösung ausgelegt (siehe README). Dieser Modus
existiert für den Fall, dass du ihn doch dauerhaft laufen lassen willst,
etwa auf einem Raspberry Pi. Das Intervall kommt aus
`betrieb.intervall_minuten`, die KI bleibt auch hier aus, solange nicht
--mit-ki gesetzt ist.
"""
from __future__ import annotations

import signal
import sys

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from .config.profil import lade_profil
from .database.repository import Repository
from .llm.client import LlmClient
from .services.export import exportiere
from .services.pipeline import Pipeline


def starte_dienst(args) -> None:
    profil = lade_profil(args.profil)
    repo = Repository(args.datenbank)
    llm = LlmClient(profil, eingeschaltet=True if getattr(args, "mit_ki", False) else None)
    pipeline = Pipeline(profil, repo, llm, geocoding=getattr(args, "geocoding", False))

    def durchlauf() -> None:
        try:
            pipeline.laufe(mindestscore=getattr(args, "mindestscore", 0))
            exportiere(repo)
        except Exception:
            logger.exception("Durchlauf fehlgeschlagen, weiter beim nächsten Intervall")

    planer = BlockingScheduler(timezone="Europe/Berlin")
    planer.add_job(
        durchlauf,
        IntervalTrigger(minutes=profil.betrieb.intervall_minuten),
        id="scan",
        next_run_time=None,
        max_instances=1,
        coalesce=True,          # verpasste Läufe nicht nachholen, nur den nächsten
    )

    for signalnummer in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signalnummer, lambda *_: (planer.shutdown(wait=False), sys.exit(0)))

    logger.info("Dienst gestartet, Intervall {} Minuten", profil.betrieb.intervall_minuten)
    durchlauf()                 # sofort einmal, nicht erst nach dem ersten Intervall
    planer.start()
