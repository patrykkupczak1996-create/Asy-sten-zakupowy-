#!/usr/bin/env python3
"""Serwer wersji mobilnej - telefon mierzy, Python liczy.

Uruchom na komputerze w tej samej sieci Wi-Fi co telefon, a nastepnie zeskanuj
telefonem kod QR wypisany przy starcie (albo wpisz adres recznie).

    python serve_wall.py               # start serwera
    python serve_wall.py --diagnose    # sama diagnostyka sieci, bez startu
"""

from __future__ import annotations

import argparse
import sys

from wallmeasure.netinfo import (
    adresy_lokalne,
    kod_qr,
    port_zajety,
    raport,
    wskazowki_zapory,
)
from wallmeasure.server import MAX_SESSIONS, SessionStore, create_app

KRESKA = "─" * 58


def _naglowek(port: int, pokaz_qr: bool) -> None:
    adresy = adresy_lokalne()
    lan = [adres for adres in adresy if adres.uzyteczny]

    print(KRESKA)
    print("  ASYSTENT POMIAROWY - WERSJA MOBILNA")
    print(KRESKA)

    if lan:
        glowny = lan[0]
        print("\n  Wejdz tym adresem w przegladarce telefonu:\n")
        print(f"      {glowny.url(port)}\n")
        if len(lan) > 1:
            print("  Gdyby nie dzialal, sprobuj kolejnego:")
            for adres in lan[1:]:
                print(f"      {adres.url(port)}")
            print()
        if pokaz_qr:
            obrazek = kod_qr(glowny.url(port))
            if obrazek:
                print("  Albo zeskanuj aparatem telefonu:\n")
                print(obrazek)
                print()
            else:
                print("  (kod QR: zainstaluj biblioteke 'qrcode', aby go zobaczyc)\n")
    else:
        print("\n  UWAGA: ten komputer nie ma adresu w sieci lokalnej,")
        print("  wiec telefon nie ma jak sie z nim polaczyc.\n")
        print(raport(port))
        print()

    print(f"  Na tym komputerze: http://127.0.0.1:{port}")
    print("  Telefon i komputer musza byc w tej samej sieci Wi-Fi.")
    print("  Serwer nie ma uwierzytelniania - uzywaj go tylko w zaufanej sieci.")
    print("  Zatrzymanie: Ctrl+C.   Problemy: python serve_wall.py --diagnose")
    print(KRESKA + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Serwer mobilnej wersji asystenta pomiarowego.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", default="0.0.0.0", help="interfejs nasluchiwania")
    parser.add_argument("--port", type=int, default=8000, help="port HTTP")
    parser.add_argument(
        "--max-sessions",
        type=int,
        default=MAX_SESSIONS,
        help="ile zdjec trzymac w pamieci jednoczesnie",
    )
    parser.add_argument("--no-qr", action="store_true", help="nie pokazuj kodu QR")
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="wypisz diagnostyke sieci i zakoncz, bez uruchamiania serwera",
    )
    parser.add_argument("--debug", action="store_true", help="tryb debugowania Flask")
    args = parser.parse_args(argv)

    if args.diagnose:
        print(raport(args.port, args.host))
        return 0

    if port_zajety(args.host, args.port):
        print(
            f"BLAD: port {args.port} jest juz zajety.\n"
            "  Serwer moze dzialac w innym oknie terminala - zamknij je,\n"
            f"  albo uruchom na innym porcie:  python serve_wall.py --port {args.port + 1}",
            file=sys.stderr,
        )
        return 1

    app = create_app(SessionStore(limit=max(1, args.max_sessions)))
    _naglowek(args.port, pokaz_qr=not args.no_qr)

    try:
        app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)
    except KeyboardInterrupt:
        print("\nZatrzymano serwer.")
    except OSError as blad:
        print(f"BLAD: nie udalo sie uruchomic serwera: {blad}", file=sys.stderr)
        for wskazowka in wskazowki_zapory(args.port):
            print(f"  {wskazowka}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
