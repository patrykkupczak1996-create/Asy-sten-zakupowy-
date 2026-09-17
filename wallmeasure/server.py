"""Serwer HTTP dla wersji mobilnej - telefon jako przyrzad pomiarowy.

Cala matematyka (detekcja markera, homografia, skala mm/px, eksport) zostaje
po stronie Pythona - dokladnie ta sama, ktora obsluguje wersje desktopowa.
Telefon dostaje wyprostowany obraz plus komplet parametrow ukladu wspolrzednych
i mierzy juz lokalnie, bez odpytywania serwera przy kazdym dotknieciu.

Uwaga: to serwer deweloperski bez uwierzytelniania. Uruchamiaj go wylacznie
w zaufanej sieci (domowy router, hotspot telefonu), nigdy nie wystawiaj
bezposrednio do internetu.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import mkdtemp

import cv2
import numpy as np
from flask import Flask, jsonify, request, send_file, send_from_directory

from .detect import MarkerNotFoundError, detect_marker, format_detection_report, quality_warnings
from .export import export_results
from .rectify import Rectification, rectify_wall, reprojection_error_mm
from .session import MeasurementSession

# Telefon nie potrzebuje pelnej rozdzielczosci obrazu wyprostowanego - wystarczy
# tyle, by zoom byl ostry. Pomiar i tak przelicza sie na pelna skale przy zapisie.
MAX_DISPLAY_PX = 2400
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MAX_SESSIONS = 4

EXPORT_NAMES = {
    "png": "wynik_pomiaru.png",
    "json": "wymiary.json",
}


@dataclass
class ServerSession:
    """Jedno zdjecie przepuszczone przez rektyfikacje, gotowe do pomiaru."""

    session_id: str
    rect: Rectification
    view_scale: float          # piksel wyswietlany / piksel obrazu wyprostowanego
    jpeg: bytes
    source_name: str
    report: str
    warnings: list[str]
    export_dir: Path
    created_at: float = field(default_factory=time.time)

    @property
    def display_size(self) -> tuple[int, int]:
        width, height = self.rect.size
        return round(width * self.view_scale), round(height * self.view_scale)

    @property
    def mm_per_display_px(self) -> float:
        return self.rect.mm_per_px / self.view_scale

    def display_point(self, point_px: tuple[float, float]) -> list[float]:
        return [point_px[0] * self.view_scale, point_px[1] * self.view_scale]

    def to_rect_px(self, display_px) -> tuple[float, float]:
        return float(display_px[0]) / self.view_scale, float(display_px[1]) / self.view_scale

    def describe(self) -> dict:
        width, height = self.display_size
        return {
            "session_id": self.session_id,
            "obraz": {
                "url": f"/api/session/{self.session_id}/image.jpg",
                "szerokosc": width,
                "wysokosc": height,
            },
            "mm_na_piksel": self.mm_per_display_px,
            "marker": {
                "rozmiar_mm": self.rect.marker_size_mm,
                "osnowa_px": self.display_point(self.rect.marker_origin_px),
                "narozniki_px": [
                    self.display_point(corner) for corner in self.rect.marker_corners_px
                ],
            },
            "sciana_mm": [round(self.rect.width_mm, 1), round(self.rect.height_mm, 1)],
            "raport": self.report,
            "ostrzezenia": self.warnings,
            "zrodlo": self.source_name,
        }


class SessionStore:
    """Kilka ostatnich sesji w pamieci - starsze sa usuwane wraz z plikami."""

    def __init__(self, limit: int = MAX_SESSIONS) -> None:
        self._limit = limit
        self._lock = threading.Lock()
        self._sessions: OrderedDict[str, ServerSession] = OrderedDict()

    def add(self, session: ServerSession) -> None:
        with self._lock:
            self._sessions[session.session_id] = session
            while len(self._sessions) > self._limit:
                _, evicted = self._sessions.popitem(last=False)
                self._cleanup(evicted)

    def get(self, session_id: str) -> ServerSession | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                self._sessions.move_to_end(session_id)
            return session

    @staticmethod
    def _cleanup(session: ServerSession) -> None:
        for path in session.export_dir.glob("*"):
            path.unlink(missing_ok=True)
        session.export_dir.rmdir()


def _decode_upload(data: bytes) -> np.ndarray:
    buffer = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(
            "Nie udalo sie odczytac zdjecia. Jesli telefon zapisuje w formacie HEIC, "
            "przelacz aparat na 'Najbardziej zgodny' (JPEG) albo przeslij plik JPG/PNG."
        )
    return image


def _build_display(rect: Rectification) -> tuple[bytes, float]:
    width, height = rect.size
    scale = min(1.0, MAX_DISPLAY_PX / max(width, height))
    image = rect.image
    if scale < 1.0:
        image = cv2.resize(
            image, (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        raise RuntimeError("Nie udalo sie zakodowac obrazu wyprostowanego.")
    return encoded.tobytes(), scale


def _float_arg(form, name: str, default: float) -> float:
    raw = form.get(name)
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except ValueError as error:
        raise ValueError(f"Pole {name} musi byc liczba.") from error


def create_app(store: SessionStore | None = None) -> Flask:
    static_dir = Path(__file__).resolve().parent / "static"
    app = Flask(__name__, static_folder=str(static_dir), static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    app.config["SESSION_STORE"] = store or SessionStore()

    @app.get("/")
    def index():
        return send_from_directory(static_dir, "index.html")

    @app.post("/api/rectify")
    def api_rectify():
        upload = request.files.get("image")
        if upload is None or not upload.filename:
            return jsonify(blad="Nie przeslano zdjecia."), 400

        try:
            marker_size_mm = _float_arg(request.form, "marker_size_mm", 180.0)
            mm_per_px = _float_arg(request.form, "mm_per_px", 0.5)
            marker_id = int(_float_arg(request.form, "marker_id", 0.0))
            image = _decode_upload(upload.read())
        except ValueError as error:
            return jsonify(blad=str(error)), 400

        try:
            detection = detect_marker(image, marker_id=marker_id)
        except MarkerNotFoundError as error:
            return jsonify(blad=str(error)), 422
        except ValueError as error:
            return jsonify(blad=str(error)), 400

        try:
            rect = rectify_wall(
                image, detection, marker_size_mm=marker_size_mm, mm_per_px=mm_per_px
            )
            jpeg, view_scale = _build_display(rect)
        except (ValueError, RuntimeError) as error:
            return jsonify(blad=str(error)), 400

        report = (
            f"{format_detection_report(detection, marker_size_mm)}\n"
            f"  Blad odwzorowania markera: {reprojection_error_mm(rect, detection):.3f} mm"
        )

        session = ServerSession(
            session_id=uuid.uuid4().hex,
            rect=rect,
            view_scale=view_scale,
            jpeg=jpeg,
            source_name=Path(upload.filename).name,
            report=report,
            warnings=quality_warnings(detection, marker_size_mm),
            export_dir=Path(mkdtemp(prefix="wallmeasure-")),
        )
        app.config["SESSION_STORE"].add(session)
        return jsonify(session.describe())

    @app.get("/api/session/<session_id>/image.jpg")
    def api_image(session_id: str):
        session = app.config["SESSION_STORE"].get(session_id)
        if session is None:
            return jsonify(blad="Sesja wygasla. Wyslij zdjecie ponownie."), 404
        response = app.response_class(session.jpeg, mimetype="image/jpeg")
        response.headers["Cache-Control"] = "private, max-age=3600"
        return response

    @app.post("/api/session/<session_id>/export")
    def api_export(session_id: str):
        server_session = app.config["SESSION_STORE"].get(session_id)
        if server_session is None:
            return jsonify(blad="Sesja wygasla. Wyslij zdjecie ponownie."), 404

        payload = request.get_json(silent=True) or {}
        points = payload.get("punkty") or []
        if not points:
            return jsonify(blad="Brak punktow do zapisania."), 400

        measurement = MeasurementSession(
            server_session.rect, y_up=bool(payload.get("os_y_w_gore"))
        )
        origin = payload.get("osnowa_px")
        if origin:
            measurement.set_origin(server_session.to_rect_px(origin))

        try:
            for point in points:
                measurement.add_point(
                    server_session.to_rect_px(point["px"]), label=str(point.get("nazwa", ""))
                )
        except (KeyError, TypeError, ValueError):
            return jsonify(blad="Nieprawidlowy format punktow."), 400

        image_path, json_path = export_results(
            measurement,
            server_session.export_dir / EXPORT_NAMES["png"],
            server_session.export_dir / EXPORT_NAMES["json"],
            server_session.source_name,
        )
        return jsonify(
            png=f"/api/session/{session_id}/download/png",
            json=f"/api/session/{session_id}/download/json",
            rozmiar_png=image_path.stat().st_size,
            liczba_punktow=len(measurement.points),
            punkty=measurement.rows(),
            json_nazwa=json_path.name,
        )

    @app.get("/api/session/<session_id>/download/<kind>")
    def api_download(session_id: str, kind: str):
        session = app.config["SESSION_STORE"].get(session_id)
        if session is None or kind not in EXPORT_NAMES:
            return jsonify(blad="Nie ma takiego pliku."), 404
        # Nazwa pliku pochodzi ze slownika, nigdy z adresu - brak ryzyka wyjscia
        # poza katalog sesji.
        path = session.export_dir / EXPORT_NAMES[kind]
        if not path.is_file():
            return jsonify(blad="Najpierw zapisz pomiar."), 404
        return send_file(path, as_attachment=True, download_name=path.name)

    @app.errorhandler(413)
    def too_large(_error):
        limit_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        return jsonify(blad=f"Zdjecie jest wieksze niz {limit_mb} MB."), 413

    return app
