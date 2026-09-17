"""Eksport wynikow: obraz z naniesionymi wymiarami oraz tabela punktow w JSON."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from .render import render_for_export
from .session import MeasurementSession


def save_image(image: np.ndarray, path: str | Path) -> Path:
    path = Path(path)
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image):
        raise IOError(f"Nie udalo sie zapisac obrazu: {path}")
    return path


def save_json(session: MeasurementSession, path: str | Path, source_image: str = "") -> Path:
    path = Path(path)
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "wersja": 1,
        "data": datetime.now().isoformat(timespec="seconds"),
        "metadane": session.metadata(source_image),
        "odcinki": session.segment_rows(),
        "punkty": session.rows(),
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def export_results(
    session: MeasurementSession,
    image_path: str | Path,
    json_path: str | Path,
    source_image: str = "",
) -> tuple[Path, Path]:
    """Zapisuje komplet wynikow i zwraca sciezki zapisanych plikow."""
    canvas = render_for_export(session, source_image or Path(image_path).name)
    return save_image(canvas, image_path), save_json(session, json_path, source_image)
