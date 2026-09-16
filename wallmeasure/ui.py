"""Interaktywne okno pomiarowe (OpenCV HighGUI).

Obraz wyprostowany bywa wielokrotnie wiekszy niz ekran, dlatego okno ma wlasny
viewport: stala wielkosc, wlasny zoom i przesuwanie. Wspolrzedne klikniec sa
przeliczane z powrotem na piksele obrazu wyprostowanego, wiec precyzja pomiaru
nie zalezy od aktualnego powiekszenia.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from . import render
from .export import export_results
from .session import MeasurementSession

WINDOW_NAME = "Wymiarowanie sciany - ArUco"

# Kody klawiszy strzalek roznia sie miedzy backendami HighGUI (GTK / Qt / Win32).
# Porownujemy pelny kod z waitKeyEx - maskowanie do 8 bitow dawaloby 81-84,
# czyli kolizje z literami Q, R, S, T.
_ARROW_KEYS = {
    "left": {65361, 63234, 2424832},
    "up": {65362, 63232, 2490368},
    "right": {65363, 63235, 2555904},
    "down": {65364, 63233, 2621440},
}
_X11_ARROWS = {0xFF51: "left", 0xFF52: "up", 0xFF53: "right", 0xFF54: "down"}


def arrow_direction(key: int) -> str | None:
    """Rozpoznaje strzalke po pelnym kodzie klawisza (albo zwraca None)."""
    for name, codes in _ARROW_KEYS.items():
        if key in codes:
            return name
    if key > 255:
        # Czesc backendow X11 dokleja bity stanu w gornej czesci kodu.
        return _X11_ARROWS.get(key & 0xFFFF)
    return None


class MeasurementUI:
    """Petla interakcji: klikniecia myszy, zoom, przesuwanie i zapis wynikow."""

    def __init__(
        self,
        session: MeasurementSession,
        source_name: str,
        output_image: str | Path = "wynik_pomiaru.png",
        output_json: str | Path = "wymiary.json",
        view_max_px: int = 1100,
    ) -> None:
        self.session = session
        self.source_name = source_name
        self.output_image = Path(output_image)
        self.output_json = Path(output_json)

        image_h, image_w = session.rect.image.shape[:2]
        fit = min(view_max_px / image_w, view_max_px / image_h, 1.0)
        self.view_w = max(200, int(round(image_w * fit)))
        self.view_h = max(150, int(round(image_h * fit)))
        self.min_scale = min(self.view_w / image_w, self.view_h / image_h)
        self.scale = self.min_scale
        self.max_scale = max(self.min_scale * 2.0, 8.0)
        self.offset = [0.0, 0.0]

        self._view_cache: np.ndarray | None = None
        self._crop = (0, 0, image_w, image_h)
        self._cursor_px: tuple[float, float] | None = None
        self._panning_from: tuple[int, int] | None = None
        self._status = "Gotowe. Kliknij LPM w punkt instalacyjny."
        self._needs_view = True

    # --- przeliczenia wspolrzednych --------------------------------------
    def _view_to_image(self, x: int, y: int) -> tuple[float, float]:
        x0, y0, x1, y1 = self._crop
        crop_w = max(1, x1 - x0)
        crop_h = max(1, y1 - y0)
        return (
            x0 + x * crop_w / self.view_w,
            y0 + y * crop_h / self.view_h,
        )

    def _image_to_view(self, px: tuple[float, float]) -> tuple[float, float]:
        x0, y0, x1, y1 = self._crop
        crop_w = max(1, x1 - x0)
        crop_h = max(1, y1 - y0)
        return (
            (px[0] - x0) * self.view_w / crop_w,
            (px[1] - y0) * self.view_h / crop_h,
        )

    def _clamp_offset(self) -> None:
        image_h, image_w = self.session.rect.image.shape[:2]
        region_w = self.view_w / self.scale
        region_h = self.view_h / self.scale
        self.offset[0] = float(np.clip(self.offset[0], 0.0, max(0.0, image_w - region_w)))
        self.offset[1] = float(np.clip(self.offset[1], 0.0, max(0.0, image_h - region_h)))

    def _zoom(self, factor: float, focus_px: tuple[float, float] | None = None) -> None:
        image_h, image_w = self.session.rect.image.shape[:2]
        new_scale = float(np.clip(self.scale * factor, self.min_scale, self.max_scale))
        if abs(new_scale - self.scale) < 1e-9:
            return
        if focus_px is None:
            focus_px = (
                self.offset[0] + self.view_w / self.scale / 2,
                self.offset[1] + self.view_h / self.scale / 2,
            )
        # Punkt pod kursorem ma zostac w tym samym miejscu okna.
        view_x, view_y = self._image_to_view(focus_px)
        self.scale = new_scale
        self.offset[0] = focus_px[0] - view_x / self.scale
        self.offset[1] = focus_px[1] - view_y / self.scale
        self._clamp_offset()
        self._needs_view = True

    # --- obsluga myszy ----------------------------------------------------
    def _on_mouse(self, event: int, x: int, y: int, flags: int, _param) -> None:
        image_px = self._view_to_image(x, y)

        if event == cv2.EVENT_LBUTTONDOWN:
            point = self.session.add_point(image_px)
            self._status = (
                f"{point.name}: DX {point.dx_mm:+.1f} mm, DY {point.dy_mm:+.1f} mm, "
                f"w linii prostej {point.distance_mm:.1f} mm"
            )
            self._needs_view = True
        elif event == cv2.EVENT_RBUTTONDOWN:
            self.session.set_origin(image_px)
            self._status = "Nowy punkt bazowy (0,0). Wszystkie pomiary przeliczone."
            self._needs_view = True
        elif event == cv2.EVENT_MBUTTONDOWN:
            self._panning_from = (x, y)
        elif event == cv2.EVENT_MBUTTONUP:
            self._panning_from = None
        elif event == cv2.EVENT_MOUSEMOVE:
            self._cursor_px = image_px
            if self._panning_from is not None:
                dx = (x - self._panning_from[0]) / self.scale
                dy = (y - self._panning_from[1]) / self.scale
                self.offset[0] -= dx
                self.offset[1] -= dy
                self._panning_from = (x, y)
                self._clamp_offset()
                self._needs_view = True
        elif event == getattr(cv2, "EVENT_MOUSEWHEEL", -999):
            delta = cv2.getMouseWheelDelta(flags) if hasattr(cv2, "getMouseWheelDelta") else flags
            self._zoom(1.25 if delta > 0 else 1 / 1.25, image_px)

    def _invalidate(self) -> None:
        self._needs_view = True

    # --- renderowanie -----------------------------------------------------
    def _render_view(self) -> np.ndarray:
        """Skladaj klatke: wycinek obrazu + adnotacje + panel informacyjny.

        Adnotacje rysujemy dopiero na pomniejszonym wycinku, wiec ich grubosc
        jest stala na ekranie, a przy duzym zoomie nie zaslaniaja detalu.
        """
        source = self.session.rect.image
        if self._needs_view or self._view_cache is None:
            image_h, image_w = source.shape[:2]
            self._clamp_offset()
            x0 = int(round(self.offset[0]))
            y0 = int(round(self.offset[1]))
            x1 = min(image_w, x0 + max(1, int(round(self.view_w / self.scale))))
            y1 = min(image_h, y0 + max(1, int(round(self.view_h / self.scale))))
            x0 = max(0, min(x0, x1 - 1))
            y0 = max(0, min(y0, y1 - 1))
            self._crop = (x0, y0, x1, y1)
            interpolation = cv2.INTER_AREA if self.scale < 1.0 else cv2.INTER_NEAREST
            self._view_cache = cv2.resize(
                source[y0:y1, x0:x1], (self.view_w, self.view_h), interpolation=interpolation
            )
            self._needs_view = False

        frame = self._view_cache.copy()
        view_scale = render.drawing_scale(frame)
        render.draw_annotations(self.session, frame, self._image_to_view, view_scale)

        if self._cursor_px is not None:
            vx, vy = self._image_to_view(self._cursor_px)
            if 0 <= vx < self.view_w and 0 <= vy < self.view_h:
                cv2.line(frame, (int(vx), 0), (int(vx), self.view_h), (200, 200, 200), 1)
                cv2.line(frame, (0, int(vy)), (self.view_w, int(vy)), (200, 200, 200), 1)

        lines = render.hud_lines(
            self.session, self.source_name, self._cursor_px, self._status
        )
        lines.insert(4, f"ZOOM: {self.scale / self.min_scale:.2f}x")
        render.draw_hud(frame, lines, view_scale)
        render.draw_footer(frame, render.HELP_LINE, view_scale)
        return frame

    # --- klawiatura -------------------------------------------------------
    def _handle_key(self, key: int) -> bool:
        """Zwraca False, gdy nalezy zamknac okno."""
        pan_step = 60.0 / self.scale

        # Strzalki rozpoznajemy po pelnym kodzie, zanim zinterpretujemy klawisz
        # jako znak ASCII - inaczej "strzalka w lewo" udawalaby litere Q.
        arrow = arrow_direction(key)
        if arrow is not None:
            axis = 0 if arrow in ("left", "right") else 1
            self.offset[axis] += pan_step if arrow in ("right", "down") else -pan_step
            self._clamp_offset()
            self._needs_view = True
            return True

        char = chr(key).lower() if 0 <= key < 128 else ""
        if key == 27 or char == "q":
            return False
        if char == "s":
            image_path, json_path = export_results(
                self.session, self.output_image, self.output_json, self.source_name
            )
            self._status = f"Zapisano: {image_path} oraz {json_path}"
            self._needs_view = True
        elif char == "u":
            removed = self.session.undo()
            self._status = (
                f"Cofnieto punkt {removed.name}." if removed else "Brak punktow do cofniecia."
            )
            self._needs_view = True
        elif char == "c":
            self.session.clear()
            self._status = "Wyczyszczono wszystkie punkty."
            self._needs_view = True
        elif char == "r":
            self.session.reset_origin()
            self._status = "Punkt bazowy wrocil na lewy gorny rog markera."
            self._needs_view = True
        elif char in ("+", "="):
            self._zoom(1.25, self._cursor_px)
        elif char in ("-", "_"):
            self._zoom(1 / 1.25, self._cursor_px)
        elif char == "0":
            self.scale = self.min_scale
            self.offset = [0.0, 0.0]
            self._needs_view = True
        return True

    # --- petla glowna -----------------------------------------------------
    def run(self) -> None:
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback(WINDOW_NAME, self._on_mouse)
        try:
            while True:
                cv2.imshow(WINDOW_NAME, self._render_view())
                key = cv2.waitKeyEx(20)
                if key != -1 and not self._handle_key(key):
                    break
                # Zamkniecie okna krzyzykiem.
                if cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                    break
        finally:
            cv2.destroyWindow(WINDOW_NAME)
            cv2.waitKey(1)
