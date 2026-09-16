"""Warstwa CLI: parsowanie argumentow i spiecie calego potoku pomiarowego."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

from .detect import (
    DEFAULT_DICTIONARY,
    MarkerNotFoundError,
    detect_marker,
    format_detection_report,
    quality_warnings,
)
from .export import export_results, save_image
from .rectify import rectify_wall, reprojection_error_mm
from .session import MeasurementSession

DEFAULT_OUTPUT_IMAGE = "wynik_pomiaru.png"
DEFAULT_OUTPUT_JSON = "wymiary.json"


def load_image(path: str | Path) -> np.ndarray:
    """Wczytuje zdjecie, radzac sobie takze ze sciezkami spoza ASCII."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Nie znaleziono pliku ze zdjeciem: {path}")

    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:  # np. polskie znaki w sciezce na Windows
        buffer = np.fromfile(str(path), dtype=np.uint8)
        image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise IOError(f"Nie udalo sie zdekodowac obrazu: {path}")
    return image


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="measure_wall.py",
        description=(
            "Bezdotykowe wymiarowanie punktow instalacyjnych na scianie ze zdjecia, "
            "z markerem ArUco jako wzorcem skali."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog=(
            "Obsluga okna: LPM = pomiar punktu, PPM = nowy punkt bazowy (0,0), "
            "u = cofnij, c = czysc, r = reset bazy, +/- = zoom, "
            "srodkowy przycisk / strzalki = przesuwanie, s = zapis, q = wyjscie."
        ),
    )
    parser.add_argument("--image", required=True, help="sciezka do zdjecia sciany")
    parser.add_argument(
        "--marker-size-mm",
        type=float,
        default=180.0,
        help="fizyczny bok markera ArUco w milimetrach",
    )
    parser.add_argument(
        "--marker-id",
        type=int,
        default=0,
        help="ID markera referencyjnego (-1 = pierwszy znaleziony)",
    )
    parser.add_argument(
        "--dictionary",
        default=DEFAULT_DICTIONARY,
        help="slownik ArUco, np. DICT_4X4_50",
    )
    parser.add_argument(
        "--mm-per-px",
        type=float,
        default=0.5,
        help="skala obrazu wyprostowanego (0.5 => 1 px = 0.5 mm)",
    )
    parser.add_argument(
        "--max-output-px",
        type=int,
        default=5000,
        help="limit boku obrazu wyprostowanego (ochrona pamieci przy duzym ukosie)",
    )
    parser.add_argument(
        "--view-max-px",
        type=int,
        default=1100,
        help="maksymalny bok okna podgladu",
    )
    parser.add_argument(
        "--y-up",
        action="store_true",
        help="os Y dodatnia w gore (domyslnie w dol, jak we wspolrzednych obrazu)",
    )
    parser.add_argument(
        "--output-image",
        default=DEFAULT_OUTPUT_IMAGE,
        help="plik PNG z naniesionymi wymiarami (klawisz 's')",
    )
    parser.add_argument(
        "--output-json",
        default=DEFAULT_OUTPUT_JSON,
        help="plik JSON z tabela punktow (klawisz 's')",
    )
    parser.add_argument(
        "--save-rectified",
        metavar="PLIK",
        help="dodatkowo zapisz czysty obraz wyprostowany (bez opisow)",
    )
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help=(
            "tryb wsadowy: detekcja, rektyfikacja i zapis wynikow bez otwierania okna "
            "(przydatne na serwerze lub do szybkiej kontroli jakosci zdjecia)"
        ),
    )
    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        image = load_image(args.image)
    except (FileNotFoundError, IOError) as error:
        print(f"BLAD: {error}", file=sys.stderr)
        return 2

    height, width = image.shape[:2]
    print(f"Zdjecie: {args.image} ({width} x {height} px)")

    try:
        detection = detect_marker(
            image,
            marker_id=args.marker_id,
            dictionary_name=args.dictionary,
        )
    except ValueError as error:
        print(f"BLAD: {error}", file=sys.stderr)
        return 2
    except MarkerNotFoundError as error:
        print(f"BLAD: {error}", file=sys.stderr)
        return 3

    print(format_detection_report(detection, args.marker_size_mm))
    for warning in quality_warnings(detection, args.marker_size_mm):
        print(f"UWAGA: {warning}")

    try:
        rect = rectify_wall(
            image,
            detection,
            marker_size_mm=args.marker_size_mm,
            mm_per_px=args.mm_per_px,
            max_output_px=args.max_output_px,
        )
    except (ValueError, RuntimeError) as error:
        print(f"BLAD: {error}", file=sys.stderr)
        return 4

    out_w, out_h = rect.size
    print(
        f"Rektyfikacja: {out_w} x {out_h} px = "
        f"{rect.width_mm / 1000:.2f} x {rect.height_mm / 1000:.2f} m sciany "
        f"(1 px = {rect.mm_per_px:g} mm)"
    )
    print(f"Blad odwzorowania narozników markera: {reprojection_error_mm(rect, detection):.3f} mm")

    if args.save_rectified:
        saved = save_image(rect.image, args.save_rectified)
        print(f"Zapisano obraz wyprostowany: {saved}")

    session = MeasurementSession(rect, y_up=args.y_up)
    source_name = Path(args.image).name

    if args.no_gui:
        image_path, json_path = export_results(
            session, args.output_image, args.output_json, source_name
        )
        print(f"Tryb wsadowy - zapisano: {image_path} oraz {json_path}")
        return 0

    from .ui import MeasurementUI  # import dopiero tutaj: wymaga backendu GUI

    try:
        MeasurementUI(
            session,
            source_name=source_name,
            output_image=args.output_image,
            output_json=args.output_json,
            view_max_px=args.view_max_px,
        ).run()
    except cv2.error as error:
        print(
            "BLAD: nie udalo sie otworzyc okna OpenCV. Na maszynie bez srodowiska "
            "graficznego uzyj trybu --no-gui.\n"
            f"Szczegoly: {error}",
            file=sys.stderr,
        )
        return 5

    print(f"Zmierzono {len(session.points)} punktow.")
    return 0
