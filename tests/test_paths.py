"""File names and folders as macOS, Windows and Linux spell them."""

import os
import shutil
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

from chronon import correct, synth
from chronon.cli import main
from chronon.correct import _output_names, _unique
from chronon.synth import Clip, Device, Scenario

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _scene(folder: Path, names=("rec_01.wav", "cam_01.wav")) -> tuple[Path, Path]:
    scenario = Scenario(
        signal="noise",
        seed=3,
        devices=(Device("rec", (Clip(0.0, 12.0),)), Device("cam", (Clip(2.0, 8.0),))),
    )
    synth.write(scenario, folder)
    rec, cam = folder / names[0], folder / names[1]
    (folder / "rec_01.wav").rename(rec)
    (folder / "cam_01.wav").rename(cam)
    return rec, cam


def _spellings(media: Path) -> list[Path]:
    """Other ways to name the folder ``media``, as far as this system has them."""
    other = [media.parent / ".." / media.parent.name / media.name]
    upper = media.parent / media.name.upper()
    if upper.exists() and os.path.samefile(upper, media):  # case-insensitive file system
        other.append(upper)
    link = media.parent / "link"
    try:
        link.symlink_to(media, target_is_directory=True)
        other.append(link)
    except OSError:  # Windows without symlink rights
        pass
    return other


def test_never_writes_next_to_the_originals_however_the_folder_is_spelled(tmp_path):
    media = tmp_path / "media"
    rec, cam = _scene(media)
    spellings = _spellings(media)
    assert len(spellings) >= 2
    for outdir in spellings:
        with pytest.raises(correct.CorrectError, match="holds the input"):
            correct.sync([rec], [cam], outdir)
        with pytest.raises(correct.CorrectError, match="holds the input"):
            correct.run([rec], [cam], outdir)


def test_output_names_differ_beyond_case_and_unicode_form():
    nfc = unicodedata.normalize("NFC", "Grüße")
    nfd = unicodedata.normalize("NFD", "Grüße")
    names = _output_names([Path("a/Take.wav"), Path("b/take.wav"), Path(f"c/{nfc}.wav")])
    assert names == ["a_Take", "b_take", nfc]
    assert _unique(["ZOOM", "zoom", nfc, nfd]) == ["ZOOM", "zoom_2", nfc, f"{nfd}_2"]


def test_unicode_names_and_long_paths(tmp_path, capsys):
    # deeper than Windows' classic 260-character limit, with non-ASCII names in decomposed
    # form (as macOS file systems hand them out)
    deep = tmp_path.joinpath(*[f"Größe – 日本 Ordner {i:02d} " + "x" * 30 for i in range(6)])
    assert len(str(deep)) > 300
    rec_name = unicodedata.normalize("NFD", "Aufnahme Größe 日本.wav")
    rec, cam = _scene(deep / "media", (rec_name, "Kamera é.wav"))

    out = deep / "Ausgabe ü"
    assert main(["correct", str(rec), str(cam), "-o", str(out)]) == 0
    assert "Kamera" in capsys.readouterr().out
    rows = correct.read_report(out / "chronon-report.json")
    assert [Path(r["source"]).name for r in rows] == [rec_name, "Kamera é.wav"]
    assert all(Path(r["path"]).exists() for r in rows)
    assert rows[1]["verified"] is True

    synced = deep / "Sync ü"
    assert main(["sync", str(rec), str(cam), "-o", str(synced)]) == 0
    xml = (synced / "Sync ü.fcpxml").read_text(encoding="utf-8")
    uris = [part.split('"')[0] for part in xml.split('src="')[1:]]
    paths = {Path(unquote(urlparse(u).path).lstrip("/" if os.name == "nt" else "")) for u in uris}
    assert {p.name for p in paths} == {rec_name, "Kamera é.wav"}
    assert all(p.exists() for p in paths)
