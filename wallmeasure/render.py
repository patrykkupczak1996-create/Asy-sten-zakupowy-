"""Warstwa graficzna: celowniki, linie wymiarowe i panel informacyjny.

Uwaga: czcionki Hershey w OpenCV nie obsluguja polskich znakow diakrytycznych,
dlatego wszystkie napisy nanoszone na obraz sa celowo bez ogonkow.
"""

from __future__ import annotations

from typing import Callable

import cv2
import numpy as np

from .session import MeasurementSession

COLOR_MARKER = (0, 200, 255)     # pomaranczowy - marker referencyjny
COLOR_ORIGIN = (60, 60, 255)     # czerwony - punkt bazowy (0,0)
COLOR_POINT = (80, 230, 80)      # zielony - punkty instalacyjne
COLOR_GUIDE = (255, 200, 0)      # blekitny - linie wymiarowe DX/DY
COLOR_TEXT = (255, 255, 255)
FONT = cv2.FONT_HERSHEY_SIMPLEX

HELP_LINE = (
    "LPM=pomiar  PPM=nowa baza  u=cofnij  c=czysc  r=reset bazy  "
    "+/-=zoom  srodkowy przycisk=przesun  s=zapis  q=wyjscie"
)


def drawing_scale(image: np.ndarray) -> float:
    """Wspolczynnik grubosci linii i czcionki dopasowany do rozmiaru obrazu."""
    shorter = min(image.shape[:2])
    return max(1.0, shorter / 900.0)


def _thickness(scale: float, base: float = 2.0) -> int:
    return max(1, int(round(base * scale)))


def draw_dashed_line(
    image: np.ndarray,
    pt1: tuple[float, float],
    pt2: tuple[float, float],
    color: tuple[int, int, int],
    thickness: int,
    dash: int = 12,
) -> None:
    p1 = np.array(pt1, dtype=np.float64)
    p2 = np.array(pt2, dtype=np.float64)
    length = float(np.linalg.norm(p2 - p1))
    if length < 1.0:
        return
    steps = max(2, int(length / max(dash, 1)))
    for i in range(0, steps, 2):
        a = p1 + (p2 - p1) * (i / steps)
        b = p1 + (p2 - p1) * (min(i + 1, steps) / steps)
        cv2.line(
            image,
            (int(round(a[0])), int(round(a[1]))),
            (int(round(b[0])), int(round(b[1]))),
            color,
            thickness,
            cv2.LINE_AA,
        )


def draw_label(
    image: np.ndarray,
    text: str,
    anchor: tuple[float, float],
    scale: float,
    color: tuple[int, int, int] = COLOR_TEXT,
    bg: tuple[int, int, int] = (20, 20, 20),
) -> None:
    """Napis na polprzezroczystym tle, przyciety do granic obrazu."""
    font_scale = 0.5 * scale
    thickness = _thickness(scale, 1.2)
    (text_w, text_h), baseline = cv2.getTextSize(text, FONT, font_scale, thickness)
    pad = int(round(5 * scale))

    x = int(round(anchor[0]))
    y = int(round(anchor[1]))
    x = max(pad, min(x, image.shape[1] - text_w - pad))
    y = max(text_h + pad, min(y, image.shape[0] - baseline - pad))

    top_left = (x - pad, y - text_h - pad)
    bottom_right = (x + text_w + pad, y + baseline + pad)
    overlay = image.copy()
    cv2.rectangle(overlay, top_left, bottom_right, bg, cv2.FILLED)
    cv2.addWeighted(overlay, 0.6, image, 0.4, 0, dst=image)
    cv2.putText(image, text, (x, y), FONT, font_scale, color, thickness, cv2.LINE_AA)


def draw_crosshair(
    image: np.ndarray,
    point: tuple[float, float],
    scale: float,
    color: tuple[int, int, int],
    radius_base: float = 12.0,
) -> None:
    """Celownik z przerwa w srodku - klikniety piksel pozostaje widoczny."""
    x, y = int(round(point[0])), int(round(point[1]))
    radius = int(round(radius_base * scale))
    gap = max(2, int(round(radius * 0.35)))
    thickness = _thickness(scale, 1.6)

    cv2.circle(image, (x, y), radius, color, thickness, cv2.LINE_AA)
    cv2.line(image, (x - radius - gap, y), (x - gap, y), color, thickness, cv2.LINE_AA)
    cv2.line(image, (x + gap, y), (x + radius + gap, y), color, thickness, cv2.LINE_AA)
    cv2.line(image, (x, y - radius - gap), (x, y - gap), color, thickness, cv2.LINE_AA)
    cv2.line(image, (x, y + gap), (x, y + radius + gap), color, thickness, cv2.LINE_AA)
    cv2.circle(image, (x, y), max(1, thickness // 2), color, cv2.FILLED, cv2.LINE_AA)


def draw_annotations(
    session: MeasurementSession,
    canvas: np.ndarray,
    transform: Callable[[tuple[float, float]], tuple[float, float]] | None = None,
    scale: float | None = None,
) -> np.ndarray:
    """Nanosi marker, osnowe i punkty pomiarowe na podany obraz (w miejscu).

    ``transform`` przenosi wspolrzedne obrazu wyprostowanego na wspolrzedne
    plotna. Dzieki temu ten sam kod obsluguje zarowno eksport w pelnej
    rozdzielczosci (przeksztalcenie tozsamosciowe), jak i podglad z zoomem -
    w podgladzie grubosc linii pozostaje stala niezaleznie od powiekszenia,
    wiec celownik nie zaslania mierzonego detalu.
    """
    to_canvas = transform or (lambda point: (float(point[0]), float(point[1])))
    scale = drawing_scale(canvas) if scale is None else scale
    thickness = _thickness(scale)
    height, width = canvas.shape[:2]

    def visible(point: tuple[float, float], margin: float = 0.0) -> bool:
        return -margin <= point[0] <= width + margin and -margin <= point[1] <= height + margin

    # 1. Marker referencyjny.
    corners = np.array(
        [to_canvas(corner) for corner in session.rect.marker_corners_px], dtype=np.int32
    )
    cv2.polylines(canvas, [corners], True, COLOR_MARKER, thickness, cv2.LINE_AA)
    marker_label_at = (corners[3][0], corners[3][1] + int(26 * scale))
    if visible(marker_label_at):
        draw_label(
            canvas, f"MARKER {session.rect.marker_size_mm:.0f} mm",
            marker_label_at, scale, COLOR_MARKER,
        )

    origin = to_canvas(session.origin_px)
    last = session.last_point

    # 2. Linie wymiarowe DX / DY - tylko dla ostatniego punktu, zeby nie zaciemniac.
    if last is not None:
        target = to_canvas(last.px)
        corner = (target[0], origin[1])
        draw_dashed_line(canvas, origin, corner, COLOR_GUIDE, thickness, int(14 * scale))
        draw_dashed_line(canvas, corner, target, COLOR_GUIDE, thickness, int(14 * scale))
        cv2.line(
            canvas,
            (int(round(origin[0])), int(round(origin[1]))),
            (int(round(target[0])), int(round(target[1]))),
            COLOR_GUIDE, thickness, cv2.LINE_AA,
        )
        labels = [
            (f"DX {last.dx_mm:+.1f} mm", ((origin[0] + target[0]) / 2, origin[1] - 10 * scale)),
            (f"DY {last.dy_mm:+.1f} mm", (target[0] + 12 * scale, (origin[1] + target[1]) / 2)),
            (
                f"L {last.distance_mm:.1f} mm",
                ((origin[0] + target[0]) / 2, (origin[1] + target[1]) / 2 + 24 * scale),
            ),
        ]
        for text, anchor in labels:
            if visible(anchor, 40 * scale):
                draw_label(canvas, text, anchor, scale, COLOR_GUIDE)

    # 3. Punkty instalacyjne.
    for point in session.points:
        target = to_canvas(point.px)
        if not visible(target, 30 * scale):
            continue
        color = COLOR_POINT if point is not last else (120, 255, 255)
        draw_crosshair(canvas, target, scale, color)
        draw_label(
            canvas,
            f"{point.name}  X{point.dx_mm:+.1f}  Y{point.dy_mm:+.1f}",
            (target[0] + 20 * scale, target[1] - 16 * scale),
            scale,
            color,
        )

    # 4. Punkt bazowy rysujemy na koncu - musi byc zawsze na wierzchu.
    if visible(origin, 40 * scale):
        draw_crosshair(canvas, origin, scale, COLOR_ORIGIN, radius_base=16.0)
        draw_label(
            canvas, "BAZA (0,0)",
            (origin[0] + 22 * scale, origin[1] + 30 * scale), scale, COLOR_ORIGIN,
        )
    return canvas


def hud_lines(
    session: MeasurementSession,
    source_name: str,
    cursor_px: tuple[float, float] | None = None,
    status: str = "",
    max_points: int = 12,
) -> list[str]:
    """Tresc panelu informacyjnego (bez polskich znakow diakrytycznych)."""
    rect = session.rect
    lines = [
        f"PLIK: {source_name}",
        f"SKALA: 1 px = {rect.mm_per_px:g} mm   |   MARKER: {rect.marker_size_mm:.0f} mm",
        f"BAZA (0,0): {session.origin_description}",
        f"OS Y: {'w gore dodatnia' if session.y_up else 'w dol dodatnia'}",
    ]
    if cursor_px is not None:
        cx_mm, cy_mm = session.to_mm(cursor_px)
        lines.append(f"KURSOR: X {cx_mm:+8.1f} mm   Y {cy_mm:+8.1f} mm")

    points = session.points
    lines.append("")
    lines.append(f"PUNKTY ({len(points)}):   ID      X_mm      Y_mm      L_mm")
    if not points:
        lines.append("  (kliknij LPM w punkt instalacyjny)")
    else:
        hidden = max(0, len(points) - max_points)
        for point in points[-max_points:]:
            lines.append(
                f"  {point.name:>4}  {point.dx_mm:+9.1f} {point.dy_mm:+9.1f} "
                f"{point.distance_mm:9.1f}"
            )
        if hidden:
            lines.append(f"  ... oraz {hidden} wczesniejszych")

    if status:
        lines.append("")
        lines.append(status)
    return lines


def draw_hud(image: np.ndarray, lines: list[str], scale: float) -> None:
    """Rysuje panel informacyjny w lewym gornym rogu (modyfikuje obraz w miejscu)."""
    font_scale = 0.46 * scale
    thickness = max(1, int(round(1.1 * scale)))
    pad = int(round(10 * scale))
    line_h = int(round(20 * scale))

    widths = [
        cv2.getTextSize(line, FONT, font_scale, thickness)[0][0] for line in lines
    ] or [0]
    box_w = min(image.shape[1] - 2 * pad, max(widths) + 2 * pad)
    box_h = line_h * len(lines) + pad

    overlay = image.copy()
    cv2.rectangle(overlay, (pad, pad), (pad + box_w, pad + box_h), (15, 15, 15), cv2.FILLED)
    cv2.addWeighted(overlay, 0.62, image, 0.38, 0, dst=image)
    cv2.rectangle(image, (pad, pad), (pad + box_w, pad + box_h), (90, 90, 90), 1, cv2.LINE_AA)

    y = pad + line_h
    for line in lines:
        cv2.putText(
            image, line, (pad + int(8 * scale), y), FONT, font_scale,
            COLOR_TEXT, thickness, cv2.LINE_AA,
        )
        y += line_h


def draw_footer(image: np.ndarray, text: str, scale: float) -> None:
    """Pasek z podpowiedziami skrotow na dole obrazu."""
    font_scale = 0.44 * scale
    thickness = max(1, int(round(1.1 * scale)))
    (text_w, text_h), baseline = cv2.getTextSize(text, FONT, font_scale, thickness)
    pad = int(round(8 * scale))
    top = image.shape[0] - text_h - baseline - 2 * pad

    overlay = image.copy()
    cv2.rectangle(overlay, (0, top), (image.shape[1], image.shape[0]), (15, 15, 15), cv2.FILLED)
    cv2.addWeighted(overlay, 0.62, image, 0.38, 0, dst=image)
    cv2.putText(
        image, text, (pad, image.shape[0] - baseline - pad), FONT, font_scale,
        COLOR_TEXT, thickness, cv2.LINE_AA,
    )


def render_for_export(session: MeasurementSession, source_name: str) -> np.ndarray:
    """Pelnowymiarowy obraz z wymiarami i tabela punktow - gotowy do zapisu."""
    canvas = draw_annotations(session, session.rect.image.copy())
    scale = drawing_scale(canvas)
    draw_hud(canvas, hud_lines(session, source_name, max_points=40), scale)
    return canvas
