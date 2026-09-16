"""Rektyfikacja perspektywy sciany na podstawie znanej geometrii markera.

Marker o znanym boku (domyslnie 180 mm) jest jedynym zrodlem skali i orientacji.
Przeksztalcamy zdjecie tak, aby marker stal sie idealnym kwadratem - wtedy cala
plaszczyzna sciany jest w rzucie prostopadlym, a skala px -> mm jest stala.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .detect import MarkerDetection


@dataclass(frozen=True)
class Rectification:
    """Wyprostowany obraz sciany wraz z pelnym opisem ukladu wspolrzednych."""

    image: np.ndarray          # obraz w rzucie prostopadlym (BGR)
    homography: np.ndarray     # (3, 3) zrodlo -> obraz wyprostowany
    mm_per_px: float           # stala skala obrazu wyprostowanego
    marker_origin_px: tuple[float, float]   # lewy gorny rog markera
    marker_corners_px: np.ndarray           # (4, 2) narozniki markera po rektyfikacji
    marker_size_mm: float
    source_size: tuple[int, int]            # (szerokosc, wysokosc) zdjecia zrodlowego

    @property
    def size(self) -> tuple[int, int]:
        height, width = self.image.shape[:2]
        return width, height

    @property
    def width_mm(self) -> float:
        return self.image.shape[1] * self.mm_per_px

    @property
    def height_mm(self) -> float:
        return self.image.shape[0] * self.mm_per_px

    def px_to_mm(
        self,
        point_px: tuple[float, float],
        origin_px: tuple[float, float] | None = None,
        y_up: bool = False,
    ) -> tuple[float, float]:
        """Przelicza punkt obrazu wyprostowanego na milimetry wzgledem osnowy."""
        origin = self.marker_origin_px if origin_px is None else origin_px
        dx_mm = (point_px[0] - origin[0]) * self.mm_per_px
        dy_mm = (point_px[1] - origin[1]) * self.mm_per_px
        if y_up:
            dy_mm = -dy_mm
        return dx_mm, dy_mm

    def mm_to_px(
        self,
        point_mm: tuple[float, float],
        origin_px: tuple[float, float] | None = None,
        y_up: bool = False,
    ) -> tuple[float, float]:
        """Operacja odwrotna do :meth:`px_to_mm`."""
        origin = self.marker_origin_px if origin_px is None else origin_px
        dy_mm = -point_mm[1] if y_up else point_mm[1]
        return (
            origin[0] + point_mm[0] / self.mm_per_px,
            origin[1] + dy_mm / self.mm_per_px,
        )

    def source_to_rectified(self, point_px: tuple[float, float]) -> tuple[float, float]:
        """Przenosi punkt ze zdjecia zrodlowego do obrazu wyprostowanego."""
        src = np.array([[[float(point_px[0]), float(point_px[1])]]], dtype=np.float64)
        dst = cv2.perspectiveTransform(src, self.homography)
        return float(dst[0, 0, 0]), float(dst[0, 0, 1])


def _target_square(side_px: float) -> np.ndarray:
    """Docelowy kwadrat markera: TL, TR, BR, BL - zgodnie z kolejnoscia ArUco."""
    return np.array(
        [[0.0, 0.0], [side_px, 0.0], [side_px, side_px], [0.0, side_px]],
        dtype=np.float32,
    )


def _projected_bounds(
    source_size: tuple[int, int],
    homography: np.ndarray,
    side_px: float,
    max_output_px: int,
) -> tuple[float, float, float, float]:
    """Prostokat obejmujacy cale zdjecie po rektyfikacji, przyciety do limitu.

    Przy ukosnym ujeciu odlegle fragmenty sciany rozciagaja sie w nieskonczonosc,
    dlatego wynik jest zawsze ograniczany do okna wokol markera.
    """
    width, height = source_size
    corners = np.array(
        [[[0.0, 0.0]], [[width, 0.0]], [[width, height]], [[0.0, height]]],
        dtype=np.float64,
    )
    projected = cv2.perspectiveTransform(corners, homography).reshape(-1, 2)

    # Punkty za horyzontem daja inf/nan - pomijamy je, marker jest zawsze pewny.
    finite = projected[np.isfinite(projected).all(axis=1)]
    square = _target_square(side_px).astype(np.float64)
    points = np.vstack([finite, square]) if len(finite) else square

    min_x, min_y = points.min(axis=0)
    max_x, max_y = points.max(axis=0)

    # Przyciecie do okna wysrodkowanego na markerze.
    half = max_output_px / 2.0
    center = side_px / 2.0
    min_x = max(min_x, center - half)
    max_x = min(max_x, center + half)
    min_y = max(min_y, center - half)
    max_y = min(max_y, center + half)

    # Marker musi zawsze zmiescic sie w kadrze z niewielkim zapasem.
    pad = side_px * 0.15
    min_x = min(min_x, -pad)
    min_y = min(min_y, -pad)
    max_x = max(max_x, side_px + pad)
    max_y = max(max_y, side_px + pad)
    return float(min_x), float(min_y), float(max_x), float(max_y)


def rectify_wall(
    image: np.ndarray,
    detection: MarkerDetection,
    marker_size_mm: float = 180.0,
    mm_per_px: float = 0.5,
    max_output_px: int = 5000,
    interpolation: int = cv2.INTER_CUBIC,
) -> Rectification:
    """Prostuje perspektywe sciany i ustala stala skale px -> mm.

    Args:
        image: zdjecie zrodlowe (BGR).
        detection: wynik detekcji markera na tym zdjeciu.
        marker_size_mm: fizyczny bok markera w milimetrach.
        mm_per_px: zadana skala wyniku, np. 0.5 => 1 px = 0.5 mm.
        max_output_px: maksymalny bok obrazu wyjsciowego (zabezpieczenie pamieci).
    """
    if marker_size_mm <= 0:
        raise ValueError("Rozmiar markera musi byc dodatni.")
    if mm_per_px <= 0:
        raise ValueError("Skala mm/px musi byc dodatnia.")

    side_px = marker_size_mm / mm_per_px
    src = detection.corners.astype(np.float32)
    dst = _target_square(side_px)

    base_h = cv2.getPerspectiveTransform(src, dst)
    if base_h is None or not np.isfinite(base_h).all():
        raise RuntimeError("Nie udalo sie wyznaczyc homografii dla wykrytego markera.")

    height, width = image.shape[:2]
    min_x, min_y, max_x, max_y = _projected_bounds(
        (width, height), base_h, side_px, max_output_px
    )

    out_w = int(np.ceil(max_x - min_x))
    out_h = int(np.ceil(max_y - min_y))
    out_w = max(1, min(out_w, max_output_px))
    out_h = max(1, min(out_h, max_output_px))

    translation = np.array(
        [[1.0, 0.0, -min_x], [0.0, 1.0, -min_y], [0.0, 0.0, 1.0]], dtype=np.float64
    )
    homography = translation @ base_h

    rectified = cv2.warpPerspective(
        image,
        homography,
        (out_w, out_h),
        flags=interpolation,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(32, 32, 32),
    )

    origin = (-min_x, -min_y)
    corners_px = _target_square(side_px).astype(np.float64) + np.array(origin)
    return Rectification(
        image=rectified,
        homography=homography,
        mm_per_px=float(mm_per_px),
        marker_origin_px=(float(origin[0]), float(origin[1])),
        marker_corners_px=corners_px,
        marker_size_mm=float(marker_size_mm),
        source_size=(width, height),
    )


def reprojection_error_mm(rect: Rectification, detection: MarkerDetection) -> float:
    """Maksymalny blad odwzorowania narozników markera, w milimetrach.

    Kontrola spojnosci calego lancucha przeksztalcen (homografia + przesuniecie
    kadru + zapisany uklad wspolrzednych). Homografia z czterech punktow trafia
    w narozniki dokladnie, wiec wartosc istotnie wieksza od zera oznacza blad
    w skladaniu przeksztalcen, a nie niedokladnosc detekcji.
    """
    src = detection.corners.reshape(-1, 1, 2).astype(np.float64)
    projected = cv2.perspectiveTransform(src, rect.homography).reshape(-1, 2)
    errors_px = np.linalg.norm(projected - rect.marker_corners_px, axis=1)
    return float(errors_px.max() * rect.mm_per_px)
