#!/usr/bin/env python3
"""Serwer wersji mobilnej - telefon mierzy, Python liczy.

Uruchom na laptopie w tej samej sieci Wi-Fi co telefon, a nastepnie wejdz
w przegladarce telefonu pod adres wypisany przy starcie.

    python serve_wall.py
"""

from __future__ import annotations

import argparse
import socket
import sys

from wallmeasure.server import MAX_SESSIONS, SessionStore, create_app


def adresy_lokalne(port: int) -> list[str]:
    """Adresy, pod ktorymi serwer bedzie widoczny w sieci lokalnej."""
    adresy = [f"http://127.0.0.1:{port}"]
    gniazdo = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Nic nie wysylamy - to tylko sposob na odczytanie adresu interfejsu,
        # ktorym maszyna wychodzi do sieci lokalnej.
        gniazdo.connect(("192.168.255.255", 1))
        adresy.append(f"http://{gniazdo.getsockname()[0]}:{port}")
    except OSError:
        pass
    finally:
        gniazdo.close()
    return adresy


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
    parser.add_argument("--debug", action="store_true", help="tryb debugowania Flask")
    args = parser.parse_args(argv)

    app = create_app(SessionStore(limit=max(1, args.max_sessions)))

    print("Asystent pomiarowy - wersja mobilna")
    for adres in adresy_lokalne(args.port):
        print(f"  {adres}")
    print(
        "\nWejdz na powyzszy adres w przegladarce telefonu (ten sam Wi-Fi).\n"
        "Serwer nie ma uwierzytelniania - uzywaj go tylko w zaufanej sieci.\n"
    )
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
