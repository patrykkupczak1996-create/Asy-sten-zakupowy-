"""Bezdotykowe wymiarowanie sciany ze zdjecia z markerem ArUco.

Modul dzieli sie na warstwy:

* :mod:`wallmeasure.detect`   - detekcja markera z dokladnoscia subpikselowa,
* :mod:`wallmeasure.rectify`  - homografia, prostowanie perspektywy, skala mm/px,
* :mod:`wallmeasure.session`  - stan pomiaru (osnowa i punkty instalacyjne),
* :mod:`wallmeasure.render`   - warstwa graficzna (celowniki, wymiary, panel),
* :mod:`wallmeasure.export`   - zapis PNG oraz JSON,
* :mod:`wallmeasure.ui`       - interaktywne okno OpenCV HighGUI,
* :mod:`wallmeasure.cli`      - argumenty wiersza polecen i spiecie calosci.
"""

from .detect import MarkerDetection, MarkerNotFoundError, detect_marker
from .rectify import Rectification, rectify_wall
from .session import MeasuredPoint, MeasurementSession

__version__ = "0.1.0"
__all__ = [
    "MarkerDetection",
    "MarkerNotFoundError",
    "detect_marker",
    "Rectification",
    "rectify_wall",
    "MeasuredPoint",
    "MeasurementSession",
    "__version__",
]
