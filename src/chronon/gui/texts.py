"""The core's codes in German (notes, errors, plan steps; docs/app.md)."""

from __future__ import annotations

from pathlib import Path

from chronon.messages import Note


def _name(path: str) -> str:
    return Path(path).name


def _clip(n: Note) -> str:
    return f"{_name(n['file'])}: " if n.get("file") else ""


NOTES = {
    "no_reliable_match": lambda n: "kein sicherer Treffer",
    "inverted": lambda n: "invertiert",
    "clock_wanders": lambda n: f"Uhr wandert ±{n['ms']:.1f} ms".replace(".", ","),
    "matched_track": lambda n: f"über Spur {_name(n['file'])}",
    "reference_clock": lambda n: "gleiche Uhr wie die Referenz",
    "measured_via": lambda n: f"gemessen über {_name(n['file'])}",
    "drift_from": lambda n: f"Drift von {_name(n['file'])} übernommen",
    "linked_via": lambda n: f"gemessen über {_name(n['file'])}",
    "joined": lambda n: "zusammengefügt: " + ", ".join(_name(f) for f in n["files"]),
    "wav_over_2gib": lambda n: "WAV über 2 GiB: manche Programme lesen sie nicht",
    "caf_over_2gib": lambda n: "Als CAF geschrieben: über 2 GiB",
    "caf_no_time_stamp": lambda n: (
        "CAF hat keinen Zeitstempel: Position nur über die Timeline-Datei"
    ),
    "video_unchanged": lambda n: "Ton einer Videodatei; das Video bleibt unverändert",
    "video_placed": lambda n: f"{_clip(n)}Video ±{n['ms']:.0f} ms",
    "variable_frame_rate": lambda n: f"{_clip(n)}variable Bildrate: Bild und Ton am Ende prüfen",
    "not_verifiable": lambda n: f"Prüfung nicht möglich: {n['reason']}",
    "verification_failed": lambda n: "PRÜFUNG FEHLGESCHLAGEN: nicht synchron zur Referenz",
    "verification_failed_via": lambda n: "PRÜFUNG FEHLGESCHLAGEN (über die parallele Spur)",
    "text": lambda n: n["text"],
}


def note(n: Note | str) -> str:
    if isinstance(n, str):
        return n
    text = NOTES.get(n.get("code", ""))
    return text(n) if text else str(n)


def notes(ns: list, skip: tuple[str, ...] = ()) -> str:
    return " · ".join(note(n) for n in ns if isinstance(n, str) or n.get("code") not in skip)


def _gib(n: float) -> str:
    return f"{n / 2**30:.1f} GiB".replace(".", ",")


ERRORS = {
    "ffmpeg_missing": lambda e: "ffmpeg wurde nicht gefunden.",
    "unreadable_file": lambda e: f"{_name(e['file'])} ist nicht lesbar.",
    "no_audio": lambda e: f"{_name(e['file'])} enthält keinen Ton.",
    "outdir_holds_input": lambda e: (
        f"Der Ausgabeordner enthält die Originaldatei {_name(e['file'])}. "
        "Bitte einen eigenen Ordner wählen: Chronon schreibt nie neben die Originale."
    ),
    "not_enough_space": lambda e: (
        f"Nicht genug Platz: {_gib(e['need_bytes'])} nötig, {_gib(e['free_bytes'])} frei."
    ),
    "output_exists": lambda e: f"{_name(e['file'])} gibt es im Ausgabeordner schon.",
    "too_large_for_wav": lambda e: f"{_name(e['file'])} ist zu groß für WAV ({_gib(e['bytes'])}).",
    "source_missing": lambda e: f"{_name(e['file'])} fehlt; die Datei gehörte zur Analyse.",
    "source_changed": lambda e: (
        f"{_name(e['file'])} hat sich seit dem Sync geändert. Bitte neu synchronisieren."
    ),
}


def error(event: dict) -> str:
    """An error event for the user: German for the codes, else the core's message."""
    text = ERRORS.get(event.get("code") or "")
    try:
        return text(event) if text else event.get("message", "Unbekannter Fehler")
    except KeyError:
        return event.get("message", "Unbekannter Fehler")


def step(s: dict) -> str:
    """A step of the plan event (board 06)."""
    return {
        "read": "Dateien einlesen",
        "reference": "Wellenformen berechnen",
        "compare": f"Spuren vergleichen: {s.get('device') or ''}",
        "drift": "Drift messen und prüfen",
        "write": "Dateien schreiben",
        "verify": "Ergebnis prüfen",
        "overview": "Wellenformen berechnen",
    }.get(s.get("kind", ""), s.get("id", ""))


def verdict(row: dict) -> str:
    """'ok', 'wanders' or 'unsure' (README "Verdict badges")."""
    if not row.get("reliable", True):
        return "unsure"
    if (row.get("wander_ms") or 0.0) > 1.0:
        return "wanders"
    return "ok"


VERDICTS = {"ok": "zuverlässig", "wanders": "Uhr wandert", "unsure": "kein sicherer Treffer"}
