#!/usr/bin/env python3
"""Test dokladnosci na syntetycznym zdjeciu sciany o znanej geometrii.

Scene buduje :mod:`tools.make_test_photo` - plaska sciana w skali 1 px = 1 mm
z markerem i punktami instalacyjnymi o zadanych wspolrzednych, sfotografowana
"pod katem". Test przepuszcza takie zdjecie przez pelny potok aplikacji
i porownuje odczytane milimetry z wartosciami zadanymi.

Klikniecie uzytkownika symulujemy punktem, w ktorym dany detal NAPRAWDE
lezy na zdjeciu (rzut przez homografie sceny), a nie miejscem wyliczonym
z oczekiwanych milimetrow - inaczej test sprawdzalby sam siebie.

Uruchomienie:
    python tests/test_accuracy.py          # raport + kod wyjscia
    pytest tests/test_accuracy.py          # jako test jednostkowy
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.make_test_photo import (  # noqa: E402
    GROUND_TRUTH_MM,
    MARKER_SIZE_MM,
    build_wall,
    project,
    simulate_photo,
)
from wallmeasure.detect import detect_marker  # noqa: E402
from wallmeasure.rectify import rectify_wall, reprojection_error_mm  # noqa: E402
from wallmeasure.session import MeasurementSession  # noqa: E402

MAX_ERROR_MM = 5.0


def measure(mm_per_px: float = 0.5) -> tuple[dict[str, tuple[float, float]], float, float]:
    """Zwraca zmierzone wspolrzedne, blad odwzorowania markera i skale zrodla."""
    photo, photo_homography = simulate_photo(build_wall())

    detection = detect_marker(photo, marker_id=0)
    rect = rectify_wall(
        photo, detection, marker_size_mm=MARKER_SIZE_MM, mm_per_px=mm_per_px
    )
    session = MeasurementSession(rect)

    names = list(GROUND_TRUTH_MM)
    truth = np.array([GROUND_TRUTH_MM[name] for name in names], dtype=np.float64)
    photo_points = project(truth, photo_homography)

    measured = {
        name: session.to_mm(rect.source_to_rectified(tuple(point_px)))
        for name, point_px in zip(names, photo_points)
    }
    return (
        measured,
        reprojection_error_mm(rect, detection),
        detection.source_mm_per_px(MARKER_SIZE_MM),
    )


def errors_mm(measured: dict[str, tuple[float, float]]) -> dict[str, float]:
    return {
        name: float(np.hypot(measured[name][0] - truth[0], measured[name][1] - truth[1]))
        for name, truth in GROUND_TRUTH_MM.items()
    }


def test_transform_chain_is_consistent() -> None:
    _, error_mm, _ = measure()
    assert error_mm < 0.5, f"Blad odwzorowania markera {error_mm:.3f} mm"


def test_measurement_accuracy_under_perspective() -> None:
    measured, _, _ = measure()
    worst = max(errors_mm(measured).values())
    assert worst < MAX_ERROR_MM, f"Najwiekszy blad pomiaru {worst:.2f} mm"


def test_scale_choice_does_not_shift_results() -> None:
    """Gestsza siatka pikseli nie moze zmieniac wyniku w milimetrach."""
    coarse, _, _ = measure(mm_per_px=1.0)
    fine, _, _ = measure(mm_per_px=0.25)
    for name in GROUND_TRUTH_MM:
        assert abs(coarse[name][0] - fine[name][0]) < 1.0
        assert abs(coarse[name][1] - fine[name][1]) < 1.0


def main() -> int:
    measured, reproj_mm, src_mm_per_px = measure()
    errors = errors_mm(measured)

    print(f"Skala zdjecia zrodlowego : 1 px = {src_mm_per_px:.3f} mm")
    print(f"Blad odwzorowania markera: {reproj_mm:.4f} mm\n")
    print(f"{'punkt':<16} {'zadane X/Y':>18} {'zmierzone X/Y':>20} {'blad [mm]':>10}")
    for name, truth in GROUND_TRUTH_MM.items():
        got = measured[name]
        print(
            f"{name:<16} {truth[0]:8.1f} {truth[1]:8.1f} "
            f"{got[0]:10.2f} {got[1]:9.2f} {errors[name]:10.2f}"
        )

    worst = max(errors.values())
    print(f"\nNajwiekszy blad: {worst:.2f} mm (dopuszczalny {MAX_ERROR_MM:.1f} mm)")
    return 0 if worst < MAX_ERROR_MM and reproj_mm < 0.5 else 1


if __name__ == "__main__":
    sys.exit(main())
