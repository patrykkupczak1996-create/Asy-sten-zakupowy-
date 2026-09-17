"""Diagnostyka sieci: pod jakim adresem telefon zobaczy serwer.

Najczestsze powody, dla ktorych telefon "nie moze otworzyc strony", nie maja
nic wspolnego z kodem aplikacji: zapora blokuje port, telefon jest w innej
sieci niz komputer, albo uzytkownik wpisal adres petli zwrotnej. Ten modul
zbiera wszystko, co pozwala to rozstrzygnac w kilkanascie sekund.
"""

from __future__ import annotations

import ipaddress
import platform
import socket
import sys
from dataclasses import dataclass

# Zakresy adresow, pod ktorymi telefon w tej samej sieci realnie dotrze
# do serwera (RFC 1918 + CGNAT operatorski).
ZAKRESY_LAN = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
)


@dataclass(frozen=True)
class Adres:
    ip: str
    rodzaj: str      # 'lan' | 'wirtualny' | 'loopback' | 'link-local' | 'publiczny' | 'inny'
    opis: str

    @property
    def uzyteczny(self) -> bool:
        return self.rodzaj == "lan"

    def url(self, port: int) -> str:
        return f"http://{self.ip}:{port}"


def _adres_domyslnej_trasy() -> str | None:
    """Adres karty, ktora komputer wychodzi na zewnatrz.

    To niemal zawsze prawdziwa karta sieciowa (Wi-Fi albo kabel), a nie
    wirtualny przelacznik Hyper-V, WSL czy VirtualBoksa. Gniazdo UDP niczego
    nie wysyla - sluzy wylacznie do odczytania trasy.
    """
    for cel in ("8.8.8.8", "1.1.1.1"):
        gniazdo = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            gniazdo.connect((cel, 53))
            return gniazdo.getsockname()[0]
        except OSError:
            continue
        finally:
            gniazdo.close()
    return None


def _wyglada_na_wirtualna(ip: ipaddress.IPv4Address) -> bool:
    """Czy adres wyglada na karte wirtualnej maszyny.

    Hyper-V, WSL, VirtualBox i VMware tworza wlasne podsieci, w ktorych
    komputer pelni role bramy - i dlatego ma adres konczacy sie na .1.
    Karta z DHCP prawie nigdy takiego adresu nie dostaje, bo .1 nalezy
    do routera.
    """
    return int(ip) & 0xFF == 1


def _sklasyfikuj(ip_tekst: str, domyslny: str | None = None) -> Adres:
    try:
        ip = ipaddress.ip_address(ip_tekst)
    except ValueError:
        return Adres(ip_tekst, "inny", "nierozpoznany adres")

    if ip.is_loopback:
        return Adres(ip_tekst, "loopback", "tylko ten komputer - telefon tu nie dotrze")
    if ip.is_link_local:
        return Adres(ip_tekst, "link-local", "brak adresu z routera - sprawdz polaczenie Wi-Fi")
    if any(ip in siec for siec in ZAKRESY_LAN):
        if ip_tekst == domyslny:
            return Adres(ip_tekst, "lan", "karta, ktora komputer laczy sie z siecia - TEGO ADRESU UZYJ")
        if _wyglada_na_wirtualna(ip):
            return Adres(
                ip_tekst,
                "wirtualny",
                "prawdopodobnie karta wirtualna (Hyper-V, WSL, VirtualBox) - telefon tu nie dotrze",
            )
        return Adres(ip_tekst, "lan", "siec lokalna - moze zadzialac, jesli pierwszy adres zawiedzie")
    if ip.is_private:
        return Adres(ip_tekst, "inny", "adres specjalny, raczej nie zadziala")
    return Adres(ip_tekst, "publiczny", "adres publiczny - nie wystawiaj serwera do internetu")


def adresy_lokalne() -> list[Adres]:
    """Wszystkie adresy IPv4 tej maszyny, posortowane wedlug przydatnosci."""
    znalezione: set[str] = set()

    # Adres interfejsu, ktorym maszyna wychodzi w strone danej sieci.
    # Gniazdo UDP niczego nie wysyla - sluzy tylko do odczytania trasy.
    for cel in ("192.168.1.1", "10.0.0.1", "172.16.0.1", "100.64.0.1", "8.8.8.8"):
        gniazdo = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            gniazdo.connect((cel, 1))
            znalezione.add(gniazdo.getsockname()[0])
        except OSError:
            pass
        finally:
            gniazdo.close()

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            znalezione.add(info[4][0])
    except (socket.gaierror, UnicodeError):
        pass

    domyslny = _adres_domyslnej_trasy()
    if domyslny:
        znalezione.add(domyslny)

    kolejnosc = {"lan": 0, "wirtualny": 1, "publiczny": 2, "inny": 3, "link-local": 4, "loopback": 5}
    adresy = [_sklasyfikuj(ip, domyslny) for ip in znalezione]
    # Adres domyslnej trasy zawsze na samej gorze listy.
    return sorted(
        adresy,
        key=lambda a: (a.ip != domyslny, kolejnosc.get(a.rodzaj, 9), a.ip),
    )


def port_zajety(host: str, port: int) -> bool:
    """Czy port jest juz przez cos zajety (np. druga kopia serwera)."""
    gniazdo = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    gniazdo.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        gniazdo.bind((host if host != "0.0.0.0" else "", port))
        return False
    except OSError:
        return True
    finally:
        gniazdo.close()


def _wlacz_ansi_na_windows() -> bool:
    """Stary cmd.exe nie interpretuje sekwencji ANSI - bez tego QR bylby smieciem."""
    if platform.system() != "Windows":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        uchwyt = kernel32.GetStdHandle(-11)          # STD_OUTPUT_HANDLE
        tryb = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(uchwyt, ctypes.byref(tryb)):
            return False
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return bool(kernel32.SetConsoleMode(uchwyt, tryb.value | 0x0004))
    except Exception:
        return False


def kod_qr(tekst: str, kolorowy: bool | None = None) -> str | None:
    """Kod QR z adresem, gotowy do wydrukowania w terminalu.

    Zwraca None, gdy biblioteka ``qrcode`` nie jest zainstalowana albo wyjscie
    nie jest terminalem (w pliku kolory ANSI byly by tylko smieciem).
    """
    try:
        import qrcode
    except ImportError:
        return None

    if kolorowy is None:
        kolorowy = sys.stdout.isatty()
    if not kolorowy or not _wlacz_ansi_na_windows():
        return None

    kod = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_L)
    kod.add_data(tekst)
    kod.make(fit=True)

    # Tlo ustawiane jawnie (bialy modul jasny, ciemny czarny), zeby kod dal sie
    # zeskanowac niezaleznie od motywu terminala.
    jasny, ciemny = "\033[107m  \033[0m", "\033[40m  \033[0m"
    return "\n".join(
        "".join(ciemny if modul else jasny for modul in wiersz)
        for wiersz in kod.get_matrix()
    )


def wskazowki_zapory(port: int) -> list[str]:
    """Co zrobic, gdy serwer dziala, ale telefon nie laduje strony."""
    system = platform.system()
    if system == "Windows":
        return [
            "Windows przy pierwszym uruchomieniu pyta o dostep do sieci - jesli",
            "  klikniete zostalo 'Anuluj', Python jest zablokowany. Odblokuj port:",
            f'  PowerShell (jako administrator):  New-NetFirewallRule -DisplayName "Asystent pomiarowy" '
            f"-Direction Inbound -Protocol TCP -LocalPort {port} -Action Allow",
            "  Upewnij sie tez, ze siec Wi-Fi jest oznaczona jako 'Prywatna', a nie 'Publiczna'.",
        ]
    if system == "Darwin":
        return [
            "macOS: Ustawienia systemowe -> Siec -> Zapora -> Opcje ->",
            "  dodaj Pythona i pozwol na polaczenia przychodzace.",
        ]
    return [
        f"Linux: odblokuj port, np.  sudo ufw allow {port}/tcp",
        f"  albo  sudo firewall-cmd --add-port={port}/tcp",
    ]


def raport(port: int, host: str = "0.0.0.0") -> str:
    """Pelna diagnostyka do wypisania przy starcie albo na zadanie."""
    linie = [f"Komputer: {socket.gethostname()} ({platform.system()} {platform.release()})", ""]

    adresy = adresy_lokalne()
    uzyteczne = [adres for adres in adresy if adres.uzyteczny]

    linie.append("Adresy tego komputera:")
    for adres in adresy:
        znacznik = "->" if adres.uzyteczny else "  "
        linie.append(f"  {znacznik} {adres.url(port):<28} {adres.opis}")

    linie.append("")
    if not uzyteczne:
        linie += [
            "UWAGA: komputer nie ma adresu w sieci lokalnej.",
            "  Podlacz go do tej samej sieci Wi-Fi co telefon (albo wlacz hotspot",
            "  w telefonie i polacz z nim komputer) i uruchom serwer ponownie.",
        ]
    elif len(uzyteczne) > 1:
        linie += [
            "Komputer ma kilka adresow lokalnych (np. Wi-Fi i kabel albo VPN).",
            "  Uzyj pierwszego z listy; jesli zawiedzie, sprobuj kolejnego.",
        ]

    if any(adres.rodzaj == "wirtualny" for adres in adresy):
        linie += [
            "",
            "Pominieto adresy kart wirtualnych (Hyper-V, WSL, VirtualBox).",
            "  Wygladaja jak siec lokalna, ale prowadza do maszyn wirtualnych",
            "  na tym komputerze, a nie do sieci, w ktorej jest telefon.",
        ]

    if port_zajety(host, port):
        linie += [
            "",
            f"UWAGA: port {port} jest juz zajety - byc moze serwer dziala w innym oknie.",
            f"  Zamknij tamto okno albo uruchom z innym portem:  --port {port + 1}",
        ]

    linie += ["", "Jesli telefon nadal nie laduje strony:"]
    linie += [f"  {wskazowka}" for wskazowka in wskazowki_zapory(port)]
    linie += [
        "  Sprawdz, czy telefon ma wlaczone Wi-Fi (nie transmisje komorkowa)",
        "  i jest w tej samej sieci co komputer. Sieci dla gosci czesto blokuja",
        "  ruch miedzy urzadzeniami - wtedy uzyj hotspotu z telefonu.",
    ]
    return "\n".join(linie)
