"""Stan sesji pomiarowej: punkt bazowy (osnowa) i lista punktow instalacyjnych."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .rectify import Rectification


@dataclass
class MeasuredPoint:
    """Pojedynczy zmierzony punkt instalacyjny."""

    point_id: int
    px: tuple[float, float]      # wspolrzedne w obrazie wyprostowanym
    dx_mm: float
    dy_mm: float
    label: str = ""

    @property
    def distance_mm(self) -> float:
        return math.hypot(self.dx_mm, self.dy_mm)

    @property
    def name(self) -> str:
        return self.label or f"P{self.point_id}"

    def to_dict(self) -> dict:
        return {
            "id": self.point_id,
            "nazwa": self.name,
            "x_mm": round(self.dx_mm, 2),
            "y_mm": round(self.dy_mm, 2),
            "odleglosc_mm": round(self.distance_mm, 2),
            "piksel_x": round(self.px[0], 2),
            "piksel_y": round(self.px[1], 2),
        }


@dataclass
class MeasuredSegment:
    """Odcinek miedzy dwoma wskazanymi punktami - zwykla miarka.

    Nie zalezy od osnowy: interesuje nas sama odleglosc, np. od gniazdka
    do naroznika sciany.
    """

    segment_id: int
    a_px: tuple[float, float]
    b_px: tuple[float, float]
    mm_per_px: float
    y_up: bool = False
    label: str = ""

    @property
    def name(self) -> str:
        return self.label or f"O{self.segment_id}"

    @property
    def dx_mm(self) -> float:
        return (self.b_px[0] - self.a_px[0]) * self.mm_per_px

    @property
    def dy_mm(self) -> float:
        dy = (self.b_px[1] - self.a_px[1]) * self.mm_per_px
        return -dy if self.y_up else dy

    @property
    def length_mm(self) -> float:
        return math.hypot(self.dx_mm, self.dy_mm)

    @property
    def angle_deg(self) -> float:
        """Nachylenie odcinka wzgledem poziomu, w stopniach.

        Zakres (-90, 90]: 0 to linia pozioma, 90 pionowa, wartosc dodatnia
        oznacza wznoszenie sie w prawo. Liczone z pikseli, niezaleznie od
        ustawienia zwrotu osi Y, zeby kat zawsze znaczyl to samo.
        """
        dx = self.b_px[0] - self.a_px[0]
        dy_w_gore = -(self.b_px[1] - self.a_px[1])
        kat = math.degrees(math.atan2(dy_w_gore, dx))
        if kat > 90:
            kat -= 180
        elif kat <= -90:
            kat += 180
        return 0.0 if kat == 0 else kat   # bez ujemnego zera w eksporcie

    @property
    def is_orthogonal(self) -> bool:
        """Czy odcinek jest dokladnie poziomy albo pionowy."""
        kat = abs(self.angle_deg)
        return kat < 1e-6 or abs(kat - 90) < 1e-6

    def to_dict(self) -> dict:
        return {
            "id": self.segment_id,
            "nazwa": self.name,
            "dlugosc_mm": round(self.length_mm, 2),
            "kat_stopnie": round(self.angle_deg, 2),
            "dx_mm": round(self.dx_mm, 2),
            "dy_mm": round(self.dy_mm, 2),
            "poczatek_px": [round(self.a_px[0], 2), round(self.a_px[1], 2)],
            "koniec_px": [round(self.b_px[0], 2), round(self.b_px[1], 2)],
        }


class MeasurementSession:
    """Zbiera punkty pomiarowe i przelicza je wzgledem aktualnej osnowy.

    Wspolrzedne punktow trzymane sa w pikselach obrazu wyprostowanego, dzieki
    czemu zmiana punktu bazowego automatycznie przelicza wszystkie pomiary.
    """

    def __init__(self, rect: Rectification, y_up: bool = False) -> None:
        self.rect = rect
        self.y_up = y_up
        self._origin_px: tuple[float, float] = rect.marker_origin_px
        self._origin_is_marker = True
        self._points: list[MeasuredPoint] = []
        self._next_id = 1
        self._segments: list[MeasuredSegment] = []
        self._next_segment_id = 1

    # --- osnowa -----------------------------------------------------------
    @property
    def origin_px(self) -> tuple[float, float]:
        return self._origin_px

    @property
    def origin_is_marker(self) -> bool:
        return self._origin_is_marker

    @property
    def origin_description(self) -> str:
        if self._origin_is_marker:
            return "lewy gorny rog markera"
        return "punkt wskazany przez uzytkownika"

    def set_origin(self, point_px: tuple[float, float]) -> None:
        """Ustawia nowy punkt (0,0) - np. krawedz sciany albo linia posadzki."""
        self._origin_px = (float(point_px[0]), float(point_px[1]))
        self._origin_is_marker = False
        self._recompute()

    def reset_origin(self) -> None:
        self._origin_px = self.rect.marker_origin_px
        self._origin_is_marker = True
        self._recompute()

    # --- punkty pomiarowe -------------------------------------------------
    @property
    def points(self) -> list[MeasuredPoint]:
        return list(self._points)

    @property
    def last_point(self) -> MeasuredPoint | None:
        return self._points[-1] if self._points else None

    def to_mm(self, point_px: tuple[float, float]) -> tuple[float, float]:
        return self.rect.px_to_mm(point_px, self._origin_px, self.y_up)

    def distance_mm(self, point_px: tuple[float, float]) -> float:
        dx_mm, dy_mm = self.to_mm(point_px)
        return math.hypot(dx_mm, dy_mm)

    def add_point(self, point_px: tuple[float, float], label: str = "") -> MeasuredPoint:
        dx_mm, dy_mm = self.to_mm(point_px)
        point = MeasuredPoint(
            point_id=self._next_id,
            px=(float(point_px[0]), float(point_px[1])),
            dx_mm=dx_mm,
            dy_mm=dy_mm,
            label=label,
        )
        self._points.append(point)
        self._next_id += 1
        return point

    def undo(self) -> MeasuredPoint | None:
        if not self._points:
            return None
        removed = self._points.pop()
        self._next_id = removed.point_id
        return removed

    def clear(self) -> None:
        self._points.clear()
        self._next_id = 1

    # --- odcinki (pomiar od punktu do punktu) ---------------------------
    @property
    def segments(self) -> list[MeasuredSegment]:
        return list(self._segments)

    def add_segment(
        self,
        a_px: tuple[float, float],
        b_px: tuple[float, float],
        label: str = "",
    ) -> MeasuredSegment:
        segment = MeasuredSegment(
            segment_id=self._next_segment_id,
            a_px=(float(a_px[0]), float(a_px[1])),
            b_px=(float(b_px[0]), float(b_px[1])),
            mm_per_px=self.rect.mm_per_px,
            y_up=self.y_up,
            label=label,
        )
        self._segments.append(segment)
        self._next_segment_id += 1
        return segment

    def clear_segments(self) -> None:
        self._segments.clear()
        self._next_segment_id = 1

    def segment_rows(self) -> list[dict]:
        return [segment.to_dict() for segment in self._segments]

    def _recompute(self) -> None:
        for point in self._points:
            point.dx_mm, point.dy_mm = self.to_mm(point.px)

    # --- eksport ----------------------------------------------------------
    def metadata(self, source_image: str = "") -> dict:
        origin_x_mm, origin_y_mm = self.rect.px_to_mm(
            self._origin_px, self.rect.marker_origin_px, self.y_up
        )
        return {
            "zrodlo": source_image,
            "marker_mm": self.rect.marker_size_mm,
            "mm_na_piksel": self.rect.mm_per_px,
            "obraz_wyprostowany_px": list(self.rect.size),
            "obraz_wyprostowany_mm": [
                round(self.rect.width_mm, 1),
                round(self.rect.height_mm, 1),
            ],
            "osnowa": {
                "opis": self.origin_description,
                "piksel_x": round(self._origin_px[0], 2),
                "piksel_y": round(self._origin_px[1], 2),
                "wzgledem_markera_x_mm": round(origin_x_mm, 2),
                "wzgledem_markera_y_mm": round(origin_y_mm, 2),
            },
            "os_y": "w gore dodatnia" if self.y_up else "w dol dodatnia",
            "liczba_punktow": len(self._points),
            "liczba_odcinkow": len(self._segments),
        }

    def rows(self) -> list[dict]:
        """Tabela punktow z dodatkowym rozstawem wzgledem punktu poprzedniego.

        Wspolrzedne x/y licza sie zawsze od osnowy - przy trasowaniu kazdy otwor
        odmierza sie od tej samej bazy, zeby bledy sie nie sumowaly. Rozstaw
        wzgledem poprzednika to osobna informacja: tego potrzeba przy sprawdzaniu
        odleglosci miedzy gniazdkami.
        """
        wiersze = []
        for indeks, punkt in enumerate(self._points):
            wiersz = punkt.to_dict()
            if indeks:
                poprzedni = self._points[indeks - 1]
                dx_mm = punkt.dx_mm - poprzedni.dx_mm
                dy_mm = punkt.dy_mm - poprzedni.dy_mm
                wiersz["od_poprzedniego"] = {
                    "nazwa": poprzedni.name,
                    "dx_mm": round(dx_mm, 2),
                    "dy_mm": round(dy_mm, 2),
                    "odleglosc_mm": round(math.hypot(dx_mm, dy_mm), 2),
                }
            wiersze.append(wiersz)
        return wiersze
