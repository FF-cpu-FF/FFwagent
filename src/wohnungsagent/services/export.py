"""Export nach JSON für das statische GitHub-Pages-Dashboard.

Das Streamlit-Dashboard liest direkt aus SQLite. Auf GitHub Pages läuft aber
kein Python, deshalb schreibt dieser Export einen Auszug als JSON in
`docs/data/`, den die statische Seite abholt.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..database.repository import Repository


def exportiere(repo: Repository, ziel: Path = Path("docs/data/listings.json"),
               limit: int = 400) -> Path:
    ziel.parent.mkdir(parents=True, exist_ok=True)
    treffer = repo.treffer(limit=limit)
    laeufe = repo.laeufe(limit=1)

    daten = {
        "aktualisiert": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "anzahl": len(treffer),
        "letzter_lauf": {
            "gestartet": laeufe[0].gestartet.isoformat(timespec="seconds") if laeufe else None,
            "roh_gefunden": laeufe[0].roh_gefunden if laeufe else 0,
            "treffer": laeufe[0].treffer if laeufe else 0,
            "ki_aufrufe": laeufe[0].ki_aufrufe if laeufe else 0,
            "ki_tokens": laeufe[0].ki_tokens if laeufe else 0,
            "quellen_ok": laeufe[0].quellen_ok if laeufe else [],
            "quellen_fehler": laeufe[0].quellen_fehler if laeufe else {},
            "quellen_robots_blockiert": laeufe[0].quellen_robots_blockiert if laeufe else [],
        },
        "listings": [i.als_dict() for i in treffer],
    }
    ziel.write_text(json.dumps(daten, ensure_ascii=False, indent=1), encoding="utf-8")
    return ziel
