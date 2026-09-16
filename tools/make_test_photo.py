#!/usr/bin/env python3
"""Generator syntetycznego zdjecia testowego o znanej geometrii.

Buduje plaska "sciane" w skali 1 px = 1 mm, naklejamy na nia marker ArUco oraz
punkty instalacyjne o dokladnie znanych wspolrzednych, a nastepnie symuluje
zdjecie zrobione telefonem pod katem (homografia + rozmycie + szum).

Dzieki temu mozna sprawdzic cala aplikacje bez drukarki i bez aparatu:
klikasz w srodki kolek, a wypisany ponizej klucz mowi, ile powinno wyjsc.

Przyklad:
    python tools/make_test_photo.py --output sciana_testowa.jpg
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.generate_marker import render_marker  # noqa: E402

MARKER_SIZE_MM = 180.0
MARKER_ORIGIN_ON_WALL = (620.0, 430.0)  # lewy gorny rog markera, w mm sciany
WALL_SIZE_MM = (2400, 1800)             # szerokosc x wysokosc modelu sciany
PHOTO_SIZE = (3200, 2400)

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
    cv2.rectangle(
        wall, (ox - 25, oy - 25), (ox + side + 25, oy + side + 25), (255, 255, 255), -1
    )
    wall[oy : oy + side, ox : ox + side] = marker

    for dx_mm, dy_mm in GROUND_TRUTH_MM.values():
        center = (int(round(ox + dx_mm)), int(round(oy + dy_mm)))
        cv2.circle(wall, center, 26, (60, 60, 60), 3, cv2.LINE_AA)
        cv2.circle(wall, center, 2, (0, 0, 200), -1, cv2.LINE_AA)
    return wall


def photo_homography(
    wall_shape: tuple[int, ...], photo_size: tuple[int, int] = PHOTO_SIZE
) -> np.ndarray:
    """Homografia symulujaca zdjecie sciany zrobione pod umiarkowanym katem."""
    height, width = wall_shape[:2]
    photo_w, photo_h = photo_size
    src = np.array([[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float32)
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


def simulate_photo(
    wall: np.ndarray, photo_size: tuple[int, int] = PHOTO_SIZE
) -> tuple[np.ndarray, np.ndarray]:
    """Zwraca (zdjecie, homografia sciana -> zdjecie)."""
    homography = photo_homography(wall.shape, photo_size)
    photo = cv2.warpPerspective(
        wall, homography, photo_size, flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT, borderValue=(90, 90, 90),
    )
    photo = cv2.GaussianBlur(photo, (3, 3), 0.8)  # nieidealne ostrzenie telefonu
    noise = np.random.default_rng(11).normal(0, 2.5, photo.shape)
    return np.clip(photo.astype(np.float64) + noise, 0, 255).astype(np.uint8), homography


def project(points_mm: np.ndarray, homography: np.ndarray) -> np.ndarray:
    """Przenosi punkty sciany (mm wzgledem markera) na zdjecie.

    To jest prawda o scenie - miejsce, w ktorym dany punkt naprawde widac.
    """
    wall_px = np.asarray(points_mm, dtype=np.float64) + np.array(MARKER_ORIGIN_ON_WALL)
    return cv2.perspectiveTransform(
        wall_px.reshape(-1, 1, 2), homography
    ).reshape(-1, 2)


def ground_truth_table() -> str:
    lines = [
        "KLUCZ - wspolrzedne wzgledem lewego gornego rogu markera (os Y w dol):",
        f"  {'punkt':<18}{'X [mm]':>10}{'Y [mm]':>10}{'odleglosc [mm]':>16}",
    ]
    for name, (dx_mm, dy_mm) in GROUND_TRUTH_MM.items():
        lines.append(
            f"  {name:<18}{dx_mm:10.1f}{dy_mm:10.1f}{float(np.hypot(dx_mm, dy_mm)):16.1f}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generuje zdjecie testowe sciany o znanej geometrii.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--output", default="sciana_testowa.jpg", help="plik wynikowy")
    parser.add_argument("--quality", type=int, default=92, help="jakosc JPEG")
    args = parser.parse_args(argv)

    photo, _ = simulate_photo(build_wall())
    output = Path(args.output)
    if output.parent and not output.parent.exists():
        output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), photo, [cv2.IMWRITE_JPEG_QUALITY, args.quality]):
        print(f"BLAD: nie udalo sie zapisac pliku {output}", file=sys.stderr)
        return 1

    print(f"Zapisano {output} ({photo.shape[1]} x {photo.shape[0]} px).\n")
    print(ground_truth_table())
    print(
        f"\nUruchom:  python measure_wall.py --image {output} --marker-size-mm 180\n"
        "Klikaj w srodki kolek (przybliz klawiszem '+') i porownaj odczyty z kluczem.\n"
        "Blad rzedu 1-2 mm jest poprawny - to rzad jednego piksela zdjecia zrodlowego."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
