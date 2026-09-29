#!/usr/bin/env python3
"""Test wykrywania punktow, do ktorych przyciaga celownik (OSNAP ze zdjecia).

Na syntetycznej scianie z :mod:`tools.make_test_photo` wiadomo, gdzie leza
prawdziwe detale: srodki punktow instalacyjnych i narozniki bialej ramki
wokol markera. Test sprawdza, ze kazdy z nich zostal znaleziony z bledem
ponizej 1,5 mm i ze nie ma "smieci" - punktow z faktury tynku albo wzdluz
okregow narysowanych wokol detali.

Uruchomienie:
    pytest tests/test_snap.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.make_test_photo import GROUND_TRUTH_MM, MARKER_SIZE_MM, build_wall, simulate_photo  # noqa: E402
from wallmeasure.detect import detect_marker  # noqa: E402
from wallmeasure.rectify import rectify_wall  # noqa: E402
from wallmeasure.server import _build_display, _snap_points  # noqa: E402

MAX_ERROR_MM = 1.5


def _wykryte_mm() -> np.ndarray:
    """Ta sama droga co w serwerze: obraz w rozdzielczosci wysylanej do telefonu."""
    photo, _ = simulate_photo(build_wall())
    rect = rectify_wall(photo, detect_marker(photo, marker_id=0), MARKER_SIZE_MM, 0.5)
    _, skala, obraz = _build_display(rect)
    punkty = _snap_points(rect, obraz, skala)
    return np.array([rect.px_to_mm((x / skala, y / skala)) for x, y in punkty])


def test_znajduje_detale_bez_smieci():
    wykryte = _wykryte_mm()
    ramka = [(-25.0, -25.0), (205.0, -25.0), (205.0, 205.0), (-25.0, 205.0)]
    oczekiwane = list(GROUND_TRUTH_MM.values()) + ramka
    for cel in oczekiwane:
        blad = np.hypot(*(wykryte - np.array(cel)).T).min()
        assert blad < MAX_ERROR_MM, f"nie znaleziono {cel} (najblizej {blad:.1f} mm)"
    # Poza detalami dopuszczamy tylko naroznik samej sciany na skraju kadru.
    assert len(wykryte) <= len(oczekiwane) + 1, f"za duzo punktow: {len(wykryte)}"


if __name__ == "__main__":
    test_znajduje_detale_bez_smieci()
    print("OK")
