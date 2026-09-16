#!/usr/bin/env python3
"""Generator markera referencyjnego do wydruku.

Marker musi miec na wydruku dokladnie zadany rozmiar fizyczny (domyslnie
180 x 180 mm liczone po zewnetrznej krawedzi czarnej ramki), bo to on jest
jedynym wzorcem skali calego pomiaru. Dlatego arkusz generowany jest w zadanym
DPI i nalezy go drukowac w skali 100% ("rozmiar rzeczywisty", bez dopasowania
do strony).

Przyklad:
    python tools/generate_marker.py --id 0 --size-mm 180 --dpi 300 --output marker_180mm.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

FONT = cv2.FONT_HERSHEY_SIMPLEX


def mm_to_px(value_mm: float, dpi: int) -> int:
    return int(round(value_mm / 25.4 * dpi))


def render_marker(dictionary_name: str, marker_id: int, side_px: int) -> np.ndarray:
    """Obraz samego markera (z czarna ramka) o zadanym boku w pikselach."""
    key = dictionary_name.upper()
    if not hasattr(cv2.aruco, key):
        raise ValueError(f"Nieznany slownik ArUco: {dictionary_name!r}")
    dict_id = getattr(cv2.aruco, key)
    dictionary = (
        cv2.aruco.getPredefinedDictionary(dict_id)
        if hasattr(cv2.aruco, "getPredefinedDictionary")
        else cv2.aruco.Dictionary_get(dict_id)
    )

    # Rysujemy w rozmiarze bedacym wielokrotnoscia liczby modulow, a dopiero
    # potem skalujemy metoda najblizszego sasiada - moduly zostaja ostre.
    modules = 6  # 4x4 bity danych + ramka o szerokosci 1 modulu
    draft = max(modules * 10, ((side_px // modules) + 1) * modules)
    if hasattr(cv2.aruco, "generateImageMarker"):
        marker = cv2.aruco.generateImageMarker(dictionary, marker_id, draft)
    else:  # OpenCV <= 4.6
        marker = cv2.aruco.drawMarker(dictionary, marker_id, draft)
    return cv2.resize(marker, (side_px, side_px), interpolation=cv2.INTER_NEAREST)


def build_sheet(
    marker: np.ndarray,
    size_mm: float,
    dpi: int,
    marker_id: int,
    dictionary_name: str,
    quiet_zone_mm: float = 20.0,
) -> np.ndarray:
    """Marker na bialym arkuszu, z biala strefa cisza i opisem kontrolnym."""
    side_px = marker.shape[0]
    quiet_px = mm_to_px(quiet_zone_mm, dpi)
    caption_px = mm_to_px(18.0, dpi)

    sheet = np.full(
        (side_px + 2 * quiet_px + caption_px, side_px + 2 * quiet_px), 255, np.uint8
    )
    sheet[quiet_px : quiet_px + side_px, quiet_px : quiet_px + side_px] = marker

    # Znaczniki kontrolne - po wydruku linijka miedzy nimi musi pokazac size_mm.
    tick = mm_to_px(6.0, dpi)
    thickness = max(1, mm_to_px(0.3, dpi))
    x0, y0 = quiet_px, quiet_px
    x1, y1 = quiet_px + side_px, quiet_px + side_px
    for x in (x0, x1):
        cv2.line(sheet, (x, y0 - tick), (x, y0 - tick // 3), 0, thickness)
        cv2.line(sheet, (x, y1 + tick // 3), (x, y1 + tick), 0, thickness)
    for y in (y0, y1):
        cv2.line(sheet, (x0 - tick, y), (x0 - tick // 3, y), 0, thickness)
        cv2.line(sheet, (x1 + tick // 3, y), (x1 + tick, y), 0, thickness)

    caption = (
        f"ArUco {dictionary_name} ID={marker_id}  |  {size_mm:g} x {size_mm:g} mm  |  "
        f"{dpi} DPI  |  DRUKUJ W SKALI 100%"
    )
    font_scale = sheet.shape[1] / 1400.0
    text_thickness = max(1, int(round(font_scale * 2)))
    (text_w, _), _ = cv2.getTextSize(caption, FONT, font_scale, text_thickness)
    cv2.putText(
        sheet,
        caption,
        (max(4, (sheet.shape[1] - text_w) // 2), sheet.shape[0] - caption_px // 3),
        FONT,
        font_scale,
        0,
        text_thickness,
        cv2.LINE_AA,
    )
    return sheet


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generuje marker ArUco do wydruku w dokladnym rozmiarze fizycznym.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--id", type=int, default=0, help="ID markera")
    parser.add_argument("--dictionary", default="DICT_4X4_50", help="slownik ArUco")
    parser.add_argument("--size-mm", type=float, default=180.0, help="bok markera w mm")
    parser.add_argument("--dpi", type=int, default=300, help="rozdzielczosc wydruku")
    parser.add_argument(
        "--quiet-zone-mm", type=float, default=20.0, help="biala ramka wokol markera"
    )
    parser.add_argument("--output", default="marker_180mm.png", help="plik wynikowy PNG")
    args = parser.parse_args(argv)

    if args.size_mm <= 0 or args.dpi <= 0:
        print("BLAD: rozmiar i DPI musza byc dodatnie.", file=sys.stderr)
        return 2

    side_px = mm_to_px(args.size_mm, args.dpi)
    try:
        marker = render_marker(args.dictionary, args.id, side_px)
    except (ValueError, cv2.error) as error:
        print(f"BLAD: {error}", file=sys.stderr)
        return 2

    sheet = build_sheet(
        marker, args.size_mm, args.dpi, args.id, args.dictionary.upper(), args.quiet_zone_mm
    )
    output = Path(args.output)
    if not cv2.imwrite(str(output), sheet):
        print(f"BLAD: nie udalo sie zapisac pliku {output}", file=sys.stderr)
        return 1

    print(
        f"Zapisano {output} ({sheet.shape[1]} x {sheet.shape[0]} px, {args.dpi} DPI).\n"
        f"Marker: {args.size_mm:g} x {args.size_mm:g} mm = {side_px} x {side_px} px.\n"
        "Wydrukuj w skali 100%, naklej na sztywna plyte i sprawdz linijka "
        "odleglosc miedzy znacznikami kontrolnymi."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
