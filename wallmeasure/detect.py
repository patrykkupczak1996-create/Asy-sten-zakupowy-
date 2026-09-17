"""Detekcja markera referencyjnego ArUco z dokladnoscia subpikselowa.

Modul izoluje roznice miedzy API OpenCV 4.6-, 4.7+ i 5.x, tak aby reszta
aplikacji korzystala z jednego, stabilnego interfejsu.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

# Kolejnosc narozników zwracana przez ArUco: TL, TR, BR, BL (zgodnie z ruchem
# wskazowek zegara, patrzac na marker od frontu).
CORNER_NAMES = ("top-left", "top-right", "bottom-right", "bottom-left")

DEFAULT_DICTIONARY = "DICT_4X4_50"


class MarkerNotFoundError(RuntimeError):
    """Podnoszony, gdy na zdjeciu nie ma markera o oczekiwanym ID."""


@dataclass(frozen=True)
class MarkerDetection:
    """Wynik detekcji pojedynczego markera."""

    marker_id: int
    corners: np.ndarray  # (4, 2) float32, kolejnosc CORNER_NAMES

    @property
    def edge_lengths_px(self) -> np.ndarray:
        """Dlugosci czterech bokow markera w pikselach zdjecia zrodlowego."""
        rolled = np.roll(self.corners, -1, axis=0)
        return np.linalg.norm(rolled - self.corners, axis=1)

    @property
    def mean_edge_px(self) -> float:
        return float(self.edge_lengths_px.mean())

    @property
    def min_edge_px(self) -> float:
        return float(self.edge_lengths_px.min())

    @property
    def skew_ratio(self) -> float:
        """Stosunek najdluzszego boku do najkrotszego - miara ukosu ujecia.

        1.0 oznacza ujecie idealnie prostopadle. Powyzej ~1.6 blad rektyfikacji
        zaczyna szybko rosnac.
        """
        edges = self.edge_lengths_px
        return float(edges.max() / max(edges.min(), 1e-6))

    @property
    def center_px(self) -> tuple[float, float]:
        cx, cy = self.corners.mean(axis=0)
        return float(cx), float(cy)

    def source_mm_per_px(self, marker_size_mm: float) -> float:
        """Ile milimetrow sciany przypada na 1 piksel zdjecia zrodlowego."""
        return marker_size_mm / max(self.mean_edge_px, 1e-6)


def resolve_dictionary(name: str):
    """Zwraca slownik ArUco po nazwie, np. ``DICT_4X4_50``."""
    key = name.upper()
    if not hasattr(cv2.aruco, key):
        raise ValueError(f"Nieznany slownik ArUco: {name!r}")
    dict_id = getattr(cv2.aruco, key)
    if hasattr(cv2.aruco, "getPredefinedDictionary"):
        return cv2.aruco.getPredefinedDictionary(dict_id)
    return cv2.aruco.Dictionary_get(dict_id)  # OpenCV <= 4.6


def _build_detector_params(refine_window: int):
    if hasattr(cv2.aruco, "DetectorParameters"):
        params = cv2.aruco.DetectorParameters()
    else:  # OpenCV <= 4.6
        params = cv2.aruco.DetectorParameters_create()

    # Wbudowana subpikselowa korekta narozników ArUco.
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    params.cornerRefinementWinSize = int(refine_window)
    params.cornerRefinementMaxIterations = 60
    params.cornerRefinementMinAccuracy = 0.01

    # Zdjecia z telefonu bywaja duze i nierownomiernie oswietlone - szerszy
    # zakres okien progowania podnosi skutecznosc detekcji.
    params.adaptiveThreshWinSizeMin = 3
    params.adaptiveThreshWinSizeMax = 53
    params.adaptiveThreshWinSizeStep = 10
    return params


def _detect_raw(gray: np.ndarray, dictionary, params):
    """Wywolanie detekcji odporne na roznice API OpenCV."""
    if hasattr(cv2.aruco, "ArucoDetector"):  # OpenCV >= 4.7
        detector = cv2.aruco.ArucoDetector(dictionary, params)
        corners, ids, _ = detector.detectMarkers(gray)
    else:  # OpenCV <= 4.6
        corners, ids, _ = cv2.aruco.detectMarkers(gray, dictionary, parameters=params)
    return corners, ids


def _to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def _enhance(gray: np.ndarray) -> np.ndarray:
    """Wyrownanie histogramu (CLAHE) - druga proba przy trudnym oswietleniu."""
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def refine_corners_subpix(gray: np.ndarray, corners: np.ndarray) -> np.ndarray:
    """Dodatkowy przebieg ``cv2.cornerSubPix`` na narozynikach markera.

    Okno wyszukiwania dobierane jest z dlugosci najkrotszego boku, aby nigdy
    nie objelo sasiedniego naroznika (co przyciagneloby wynik w zle miejsce).
    """
    corners = np.ascontiguousarray(corners.reshape(-1, 1, 2).astype(np.float32))
    rolled = np.roll(corners.reshape(-1, 2), -1, axis=0)
    min_edge = float(np.linalg.norm(rolled - corners.reshape(-1, 2), axis=1).min())

    half = int(min_edge / 10.0)
    half = max(3, min(half, 15))
    if 2 * half + 1 >= min_edge:  # marker zbyt maly na bezpieczna korekte
        return corners.reshape(-1, 2)

    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 60, 0.001)
    refined = cv2.cornerSubPix(gray, corners, (half, half), (-1, -1), criteria)
    return refined.reshape(-1, 2)


def detect_marker(
    image: np.ndarray,
    marker_id: int = 0,
    dictionary_name: str = DEFAULT_DICTIONARY,
    refine_window: int = 5,
) -> MarkerDetection:
    """Znajduje marker o zadanym ID i zwraca jego narozniki z dokladnoscia < 1 px.

    ``marker_id`` rowne -1 oznacza "dowolny pierwszy znaleziony marker".
    """
    if image is None or image.size == 0:
        raise ValueError("Pusty obraz wejsciowy.")

    dictionary = resolve_dictionary(dictionary_name)
    params = _build_detector_params(refine_window)
    gray = _to_gray(image)

    found: list[tuple[int, np.ndarray]] = []
    for candidate_gray in (gray, _enhance(gray)):
        corners, ids = _detect_raw(candidate_gray, dictionary, params)
        if ids is None or len(ids) == 0:
            continue
        found = [(int(i), c.reshape(4, 2)) for i, c in zip(ids.flatten(), corners)]
        gray = candidate_gray  # subpix liczymy na tym samym obrazie co detekcje
        break

    if not found:
        raise MarkerNotFoundError(
            f"Nie znaleziono zadnego markera ze slownika {dictionary_name}. "
            "Sprawdz, czy marker jest w calosci widoczny, ostry i nie odbija swiatla."
        )

    if marker_id < 0:
        selected_id, raw_corners = found[0]
    else:
        matches = [item for item in found if item[0] == marker_id]
        if not matches:
            other = ", ".join(str(i) for i, _ in found)
            raise MarkerNotFoundError(
                f"Na zdjeciu nie ma markera o ID {marker_id}. "
                f"Wykryte ID: {other}. Wskaz wlasciwe ID markera."
            )
        if len(matches) > 1:
            raise MarkerNotFoundError(
                f"Wykryto {len(matches)} markery o ID {marker_id}. "
                "Na zdjeciu moze byc tylko jeden marker referencyjny."
            )
        selected_id, raw_corners = matches[0]

    refined = refine_corners_subpix(gray, raw_corners)
    return MarkerDetection(marker_id=selected_id, corners=refined.astype(np.float64))


def quality_warnings(detection: MarkerDetection, marker_size_mm: float) -> list[str]:
    """Lista ostrzezen o jakosci ujecia (puste = ujecie poprawne)."""
    warnings: list[str] = []
    if detection.mean_edge_px < 80:
        warnings.append(
            f"Marker zajmuje tylko ~{detection.mean_edge_px:.0f} px boku - "
            "podejdz blizej lub uzyj wiekszej rozdzielczosci, bo precyzja spadnie."
        )
    if detection.skew_ratio > 1.6:
        warnings.append(
            f"Duzy ukos ujecia (stosunek bokow markera {detection.skew_ratio:.2f}). "
            "Zrob zdjecie bardziej prostopadle do sciany."
        )
    mm_per_src_px = detection.source_mm_per_px(marker_size_mm)
    if mm_per_src_px > 3.0:
        warnings.append(
            f"1 piksel zdjecia to az {mm_per_src_px:.1f} mm sciany - "
            "pomiar bedzie zgrubny."
        )
    return warnings


def format_detection_report(detection: MarkerDetection, marker_size_mm: float) -> str:
    corners = ", ".join(
        f"{name}=({x:.2f}, {y:.2f})"
        for name, (x, y) in zip(CORNER_NAMES, detection.corners)
    )
    return (
        f"Marker ID {detection.marker_id} ({marker_size_mm:.1f} mm): "
        f"bok sredni {detection.mean_edge_px:.1f} px, "
        f"ukos {detection.skew_ratio:.2f}, "
        f"rozdzielczosc zrodla {detection.source_mm_per_px(marker_size_mm):.2f} mm/px\n"
        f"  Narozniki: {corners}"
    )
