"""Punkty charakterystyczne zdjecia, do ktorych przyciaga celownik.

Odpowiednik trybu OSNAP z programow CAD, tyle ze "geometria" jest tu samo
zdjecie: narozniki sciany, puszek, plytek czy ram okiennych. Wykrywamy je raz,
przy prostowaniu, i wysylamy do telefonu jako liste wspolrzednych - samo
przyciaganie liczy sie juz w przegladarce, bez ruchu sieciowego.
"""

from __future__ import annotations

import cv2
import numpy as np


def punkty_charakterystyczne(
    obraz: np.ndarray,
    obrys_zdjecia: np.ndarray,
    narozniki_markera: np.ndarray,
    maks: int = 600,
    min_odstep: float = 10.0,
    min_sila: float = 20.0,
    min_ksztalt: float = 0.2,
) -> list[list[float]]:
    """Narozniki wykryte na wyprostowanym zdjeciu, z dokladnoscia subpikselowa.

    Args:
        obraz: wyprostowane zdjecie (BGR) w ukladzie, w ktorym beda uzyte punkty.
        obrys_zdjecia: (4, 2) granice oryginalnego kadru w tym samym ukladzie.
            Poza nimi lezy wypelnienie, a na jego styku ze zdjeciem powstaja
            sztuczne narozniki - te trzeba odrzucic.
        narozniki_markera: (4, 2) narozniki markera. Marker jest wycinany w
            calosci: jego szachownica to szum, nie punkty montazowe, a jego
            rogi telefon zna i tak. Poza tym czern na bieli bylaby najsilniejszym
            naroznikiem w kadrze i zagluszylaby slabsze, prawdziwe krawedzie.
        maks: gorny limit liczby punktow.
        min_odstep: minimalna odleglosc miedzy punktami w pikselach obrazu.
        min_sila: bezwzgledny prog sily naroznika (minimalna wartosc wlasna).
            Faktura tynku daje ok. 2, rog bialej ramki na jasnej scianie ok. 100.
            Bez tego progu na gladkiej scianie wybralibysmy najsilniejszy szum.
        min_ksztalt: minimalny stosunek mniejszej do wiekszej wartosci wlasnej
            w otoczeniu punktu. Prawdziwy rog ma oba kierunki zmian podobnie
            silne (0.5-1.0), a punkt na krawedzi, luku czy rozmytej linii - jeden
            dominujacy (ponizej 0.05). Takie punkty tylko by przeszkadzaly,
            bo przyciagalyby celownik w przypadkowe miejsca wzdluz krawedzi.
    """
    szary = cv2.cvtColor(obraz, cv2.COLOR_BGR2GRAY) if obraz.ndim == 3 else obraz
    szary = cv2.GaussianBlur(szary, (3, 3), 0)

    maska = np.zeros(szary.shape, np.uint8)
    cv2.fillPoly(maska, [np.round(obrys_zdjecia).astype(np.int32)], 255)
    maska = cv2.erode(maska, np.ones((9, 9), np.uint8))
    srodek = narozniki_markera.mean(axis=0)
    otoczenie = srodek + (narozniki_markera - srodek) * 1.12
    cv2.fillPoly(maska, [np.round(otoczenie).astype(np.int32)], 0)

    rogi = cv2.goodFeaturesToTrack(
        szary, maxCorners=maks, qualityLevel=0.01, minDistance=min_odstep,
        mask=maska, blockSize=5,
    )
    if rogi is None:
        return []
    sila = cv2.cornerMinEigenVal(szary.astype(np.float32), 5, 3)
    rogi = rogi.reshape(-1, 2)
    wiersze = np.clip(np.round(rogi[:, 1]).astype(int), 0, szary.shape[0] - 1)
    kolumny = np.clip(np.round(rogi[:, 0]).astype(int), 0, szary.shape[1] - 1)
    wlasne = cv2.cornerEigenValsAndVecs(szary.astype(np.float32), 9, 3)[wiersze, kolumny, :2]
    ksztalt = wlasne.min(axis=1) / np.maximum(wlasne.max(axis=1), 1e-9)
    rogi = rogi[(sila[wiersze, kolumny] >= min_sila) & (ksztalt >= min_ksztalt)]
    if not len(rogi):
        return []
    rogi = rogi[~_na_sznurku(rogi, 3.5 * min_odstep)]
    if not len(rogi):
        return []
    kryteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.01)
    rogi = cv2.cornerSubPix(szary, rogi.reshape(-1, 1, 2).astype(np.float32), (4, 4), (-1, -1), kryteria)
    return [[round(float(x), 2), round(float(y), 2)] for x, y in rogi.reshape(-1, 2)]


def _na_sznurku(punkty: np.ndarray, promien: float, tolerancja: float = 2.5) -> np.ndarray:
    """Maska punktow tworzacych gesty "sznurek" wzdluz jednej prostej.

    Tak wyglada artefakt, nie geometria: mocno rozciagnieta perspektywicznie
    krawedz (albo bloki kompresji JPEG) rozpada sie na zabki, z ktorych kazdy
    wyglada jak maly naroznik. Prawdziwe narozniki - rogi puszki, plytki,
    ramy - leza od siebie znacznie dalej niz sasiednie zabki.
    """
    wynik = np.zeros(len(punkty), bool)
    for i, p in enumerate(punkty):
        odl = np.hypot(*(punkty - p).T)
        sasiedzi = punkty[(odl > 0) & (odl < promien)]
        if len(sasiedzi) < 2:
            continue
        grupa = np.vstack([p, sasiedzi])
        srodek = grupa.mean(axis=0)
        # najmniejsza wartosc osobliwa = rozrzut w poprzek najlepszej prostej
        rozrzut = np.linalg.svd(grupa - srodek, compute_uv=False)[-1] / np.sqrt(len(grupa))
        wynik[i] = rozrzut < tolerancja
    return wynik


def obrys_kadru(homografia: np.ndarray, rozmiar_zrodla: tuple[int, int], skala: float = 1.0) -> np.ndarray:
    """Granice oryginalnego zdjecia po wyprostowaniu, przeskalowane o `skala`."""
    szer, wys = rozmiar_zrodla
    rogi = np.array([[[0, 0]], [[szer, 0]], [[szer, wys]], [[0, wys]]], dtype=np.float64)
    return cv2.perspectiveTransform(rogi, homografia).reshape(-1, 2) * skala
