#!/usr/bin/env python3
"""Asystent pomiarowy stolarza - wymiarowanie sciany ze zdjecia (Proof of Concept).

Punkt wejscia aplikacji. Cala logika mieszka w pakiecie ``wallmeasure``.

Przyklad uruchomienia:

    python measure_wall.py --image sciana.jpg --marker-size-mm 180
"""

from __future__ import annotations

import sys

from wallmeasure.cli import run

if __name__ == "__main__":
    sys.exit(run())
