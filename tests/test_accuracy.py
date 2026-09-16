#!/usr/bin/env python3
"""Test dokladnosci na syntetycznym zdjeciu sciany o znanej geometrii.

Budujemy plaska "sciane" w skali 1 px = 1 mm, naklejamy na nia marker i punkty
instalacyjne o znanych wspolrzednych, a nastepnie symulujemy zdjecie zrobione
pod katem (homografia + rozmycie + szum). Przepuszczamy tak powstale zdjecie
przez pelny potok aplikacji i porownujemy odczytane milimetry z wartosciami
zadanymi.

Uruchomienie:
    python tests/test_accuracy.py          # raport + kod wyjscia
    pytest tests/test_accuracy.py          # jako test jednostkowy
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.generate_marker import render_marker  # noqa: E402
from wallmeasure.detect import detect_marker  # noqa: E402
from wallmeasure.rectify import rectify_wall, reprojection_error_mm  # noqa: E402
from wallmeasure.session import MeasurementSession  # noqa: E402

MARKER_SIZE_MM = 180.0
MARKER_ORIGIN_ON_WALL = (620.0, 430.0)  # lewy gorny rog markera, w mm sciany
WALL_SIZE_MM = (2400, 1800)             # szerokosc x wysokosc modelu sciany

# Punkty instalacyjne zadane w mm wzgledem lewego gornego rogu markera.
GROUND_TRUTH_MM = {
    "gniazdko_1": (300.0, 250.0),
    "gniazdko_2": (700.0, 250.0),
    "podejscie_wody": (-250.0, 620.0),
    "wentylacja": (950.0, -300.0),
    "puszka_dolna": (120.0, 980.0),
}


def build_wall() -> np.ndarray:
    """Model sciany w skali 1 px = 1 mm, z markerem i zaznaczonymi punktami."""
    width, height = WALL_SIZE_MM
    wall = np.full((height, width, 3), 218, np.uint8)
    # Delikatna faktura tynku, zeby detekcja nie dzialala na idealnie plaskim tle.
    noise = np.random.default_rng(7).normal(0, 4, (height, width, 1))
    wall = np.clip(wall + noise, 0, 255).astype(np.uint8)

    side = int(MARKER_SIZE_MM)
    marker = cv2.cvtColor(render_marker("DICT_4X4_50", 0, side), cv2.COLOR_GRAY2BGR)
    ox, oy = int(MARKER_ORIGIN_ON_WALL[0]), int(MARKER_ORIGIN_ON_WALL[1])
    # Biala strefa cisza wokol markera - wymagana przez detektor ArUco.
    cv2.rectangle(wall, (ox - 25, oy - 25), (ox + side + 25, oy + side + 25), (255, 255, 255), -1)
    wall[oy : oy + side, ox : ox + side] = marker

    for (dx_mm, dy_mm) in GROUND_TRUTH_MM.values():
        center = (int(round(ox + dx_mm)), int(round(oy + dy_mm)))
        cv2.circle(wall, center, 26, (60, 60, 60), 3, cv2.LINE_AA)
        cv2.circle(wall, center, 2, (0, 0, 200), -1, cv2.LINE_AA)
    return wall


def photo_homography(wall_shape: tuple[int, int], photo_size: tuple[int, int]) -> np.ndarray:
    """Homografia symulujaca zdjecie sciany zrobione pod umiarkowanym katem."""
    height, width = wall_shape[:2]
    photo_w, photo_h = photo_size
    src = np.array(
        [[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float32
    )
    # Prawa krawedz odsunieta od aparatu -> zbiega sie ku srodkowi kadru.
    dst = np.array(
        [
            [0.10 * photo_w, 0.08 * photo_h],
            [0.93 * photo_w, 0.21 * photo_h],
            [0.90 * photo_w, 0.82 * photo_h],
            [0.07 * photo_w, 0.95 * photo_h],
        ],
        dtype=np.float32,
    )
    return cv2.getPerspectiveTransform(src, dst)


def simulate_photo(wall: np.ndarray, photo_size: tuple[int, int] = (3200, 2400)):
    homography = photo_homography(wall.shape, photo_size)
    photo = cv2.warpPerspective(
        wall, homography, photo_size, flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT, borderValue=(90, 90, 90),
    )
    photo = cv2.GaussianBlur(photo, (3, 3), 0.8)  # nieidealne ostrzenie telefonu
    noise = np.random.default_rng(11).normal(0, 2.5, photo.shape)
    return np.clip(photo.astype(np.float64) + noise, 0, 255).astype(np.uint8), homography


def project(points_mm: np.ndarray, homography: np.ndarray) -> np.ndarray:
    """Przenosi punkty sciany (mm) na zdjecie - to jest nasza prawda o scenie."""
    ox, oy = MARKER_ORIGIN_ON_WALL
    wall_px = points_mm + np.array([ox, oy])
    return cv2.perspectiveTransform(
        wall_px.reshape(-1, 1, 2).astype(np.float64), homography
    ).reshape(-1, 2)


def measure(mm_per_px: float = 0.5) -> tuple[dict[str, tuple[float, float]], float, float]:
    """Zwraca zmierzone wspolrzedne, blad odwzorowania markera i skale zrodla."""
    wall = build_wall()
    photo, photo_h = simulate_photo(wall)

    detection = detect_marker(photo, marker_id=0)
    rect = rectify_wall(
        photo, detection, marker_size_mm=MARKER_SIZE_MM, mm_per_px=mm_per_px
    )
    session = MeasurementSession(rect)

    names = list(GROUND_TRUTH_MM)
    truth = np.array([GROUND_TRUTH_MM[name] for name in names], dtype=np.float64)
    photo_points = project(truth, photo_h)

    measured: dict[str, tuple[float, float]] = {}
    for name, point_px in zip(names, photo_points):
        # Klikniecie uzytkownika symulujemy punktem przeniesionym do obrazu
        # wyprostowanego - dalej dziala juz zwykla sciezka pomiarowa.
        rectified_px = rect.source_to_rectified(tuple(point_px))
        measured[name] = session.to_mm(rectified_px)

    return (
        measured,
        reprojection_error_mm(rect, detection),
        detection.source_mm_per_px(MARKER_SIZE_MM),
    )


def test_marker_reprojection_is_subpixel() -> None:
    _, error_mm, _ = measure()
    assert error_mm < 0.5, f"Blad odwzorowania markera {error_mm:.3f} mm"


def test_measurement_accuracy_under_perspective() -> None:
    measured, _, _ = measure()
    errors = [
        float(np.hypot(measured[name][0] - truth[0], measured[name][1] - truth[1]))
        for name, truth in GROUND_TRUTH_MM.items()
    ]
    assert max(errors) < 5.0, f"Najwiekszy blad pomiaru {max(errors):.2f} mm"


def main() -> int:
    measured, reproj_mm, src_mm_per_px = measure()
    print(f"Skala zdjecia zrodlowego : 1 px = {src_mm_per_px:.3f} mm")
    print(f"Blad odwzorowania markera: {reproj_mm:.4f} mm\n")
    print(f"{'punkt':<16} {'zadane X/Y':>18} {'zmierzone X/Y':>20} {'blad [mm]':>10}")

    worst = 0.0
    for name, truth in GROUND_TRUTH_MM.items():
        got = measured[name]
        error = float(np.hypot(got[0] - truth[0], got[1] - truth[1]))
        worst = max(worst, error)
        print(
            f"{name:<16} {truth[0]:8.1f} {truth[1]:8.1f} "
            f"{got[0]:10.2f} {got[1]:9.2f} {error:10.2f}"
        )

    print(f"\nNajwiekszy blad: {worst:.2f} mm")
    return 0 if worst < 5.0 and reproj_mm < 0.5 else 1


if __name__ == "__main__":
    sys.exit(main())
