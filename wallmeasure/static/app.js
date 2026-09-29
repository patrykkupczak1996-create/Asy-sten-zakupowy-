/* Miarka ze zdjecia - warstwa interakcji.
 *
 * Zalozenia:
 *  - uzytkownik nie czyta instrukcji; na kazdym ekranie jest jedna oczywista
 *    rzecz do zrobienia, a program mowi wprost, co sie dzieje,
 *  - celownik stoi nieruchomo na srodku, pod nim przesuwa sie zdjecie, wiec
 *    palec nigdy nie zaslania mierzonego miejsca,
 *  - wymiary pokazujemy w pelnych milimetrach; dokladnosc metody to 1-2 mm,
 *    wiec dziesiate czesci sugerowalyby precyzje, ktorej nie ma. Pelna
 *    precyzja trafia do eksportu JSON.
 *
 * Odczyty licza sie lokalnie z trzech liczb otrzymanych z serwera (skala,
 * osnowa, rozmiar obrazu), wiec przesuwanie i zoom nie obciazaja sieci.
 *
 * window.MIARKA_DEMO uruchamia podglad bez serwera: gotowe, wyprostowane
 * zdjecie i wynik liczony w przegladarce.
 */
'use strict';

const $ = (id) => document.getElementById(id);
const DEMO = window.MIARKA_DEMO || null;

const ETYKIETY = ['Gniazdko', 'Włącznik', 'Woda', 'Odpływ', 'Wentylacja',
                  'Narożnik', 'Krawędź', 'Wysokość', 'Szerokość', 'Wnęka'];
const TOLERANCJA_ORTHO = 4;   // stopnie
const ZOOM_MAX = 16;
const CZCIONKA = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
const KOLOR = {
  linia: '#FFFFFF',
  obwodka: 'rgba(15, 23, 42, .5)',
  tasma: '#FFC53D',
  ok: '#16A34A',
  tekst: '#0F172A',
};
const IKONA = {
  usun: '<svg viewBox="0 0 24 24"><path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  olowek: '<svg viewBox="0 0 24 24"><path d="M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>',
};

const stan = {
  sesja: null, obraz: null,
  skala: 1, skalaMin: 1, dpr: 1,
  srodek: { x: 0, y: 0 },
  tryb: 'miarka',                 // 'miarka' albo 'wspolrzedne'
  zero: { x: 0, y: 0 }, zrodloX: 'marker', zrodloY: 'marker',
  odcinki: [], nastepnyOdcinek: 1, poczatek: null,
  punkty: [], nastepnyPunkt: 1,
  nazywany: null,
  wyrozniony: null,
};

/* ================================================================ formaty */

const liczba = new Intl.NumberFormat('pl-PL', { maximumFractionDigits: 0 });
const mm = (v) => liczba.format(Math.round(Math.abs(v)));
function mmZnak(v) {
  const r = Math.round(v);
  if (r === 0) return '0';
  return (r < 0 ? '−' : '+') + liczba.format(Math.abs(r));
}
const stopnie = (v) => `${v < 0 ? '−' : ''}${Math.abs(v).toFixed(1).replace('.', ',')}°`;

/* Polska odmiana liczebnikow: 1 wymiar, 2 wymiary, 5 wymiarow. */
function odmiana(ile, jeden, dwa, wiele) {
  const n = Math.abs(ile);
  if (n === 1) return jeden;
  const reszta = n % 10, setki = n % 100;
  if (reszta >= 2 && reszta <= 4 && (setki < 12 || setki > 14)) return dwa;
  return wiele;
}

function pamietaj(klucz, wartosc) {
  try { localStorage.setItem(klucz, wartosc); } catch (e) { /* tryb prywatny */ }
}
function pamietane(klucz) {
  try { return localStorage.getItem(klucz); } catch (e) { return null; }
}

/* ================================================================ nawigacja */

function pokazEkran(nazwa) {
  ['start', 'pomiar', 'wynik'].forEach((e) => { $('ekran-' + e).hidden = e !== nazwa; });
  window.scrollTo(0, 0);
  if (nazwa === 'pomiar') requestAnimationFrame(rysuj);
}

function otworz(id) { $(id).hidden = false; }
function zamknij(id) { $(id).hidden = true; }

document.querySelectorAll('.arkusz-tlo').forEach((tlo) => {
  tlo.addEventListener('click', (e) => { if (e.target === tlo) tlo.hidden = true; });
});
document.querySelectorAll('[data-zamknij]').forEach((b) => {
  b.addEventListener('click', () => { b.closest('.arkusz-tlo').hidden = true; });
});
const arkuszOtwarty = () => [...document.querySelectorAll('.arkusz-tlo')].some((a) => !a.hidden);

function komunikat(id, tekst, klasa = '') {
  const el = $(id);
  el.textContent = tekst;
  el.className = `komunikat ${klasa}`;
}

/* Chwilowy komunikat w miejscu podpowiedzi - potwierdzenie albo ostrzezenie. */
let toastDo = 0;
function toast(tekst, klasa = 'ok', ms = 1800) {
  const p = $('podpowiedz');
  p.textContent = tekst;
  p.className = `podpowiedz ${klasa}`;
  toastDo = Date.now() + ms;
  setTimeout(() => { if (Date.now() >= toastDo) odswiezOdczyt(); }, ms + 20);
}

/* ================================================================ geometria */

const mmNaPiksel = () => stan.sesja.mm_na_piksel;
const wGore = () => $('os-y-gora').checked;

function roznica(od, doP) {
  const k = mmNaPiksel();
  const dx = (doP.x - od.x) * k;
  const dy = (doP.y - od.y) * k * (wGore() ? -1 : 1);
  return { dx, dy, l: Math.hypot(dx, dy) };
}

function katOdcinka(a, b) {
  const k = Math.atan2(-(b.y - a.y), b.x - a.x) * 180 / Math.PI;
  return k > 90 ? k - 180 : k <= -90 ? k + 180 : k;
}

/* Prostowanie do poziomu i pionu, jak ORTHO w programach CAD. Trafienie
 * palcem w rowne zero stopni jest nieosiagalne, a przy montazu to
 * najczestszy przypadek. */
function koniecOdcinka(od, kursor) {
  const dx = kursor.x - od.x, dy = kursor.y - od.y;
  if (!$('ortho').checked || (dx === 0 && dy === 0)) return { px: { ...kursor }, os: null };
  const k = Math.abs(Math.atan2(dy, dx) * 180 / Math.PI);
  if (k <= TOLERANCJA_ORTHO || k >= 180 - TOLERANCJA_ORTHO) return { px: { x: kursor.x, y: od.y }, os: 'poziom' };
  if (Math.abs(k - 90) <= TOLERANCJA_ORTHO) return { px: { x: od.x, y: kursor.y }, os: 'pion' };
  return { px: { ...kursor }, os: null };
}

const opisOsi = (os) => (os === 'poziom' ? 'poziomo' : 'pionowo');

/* ================================================================ rysowanie */

const plotno = $('plotno');
const ctx = plotno.getContext('2d');

/* Bufor rysowania musi nadazac za rozmiarem elementu, inaczej po zmianie
 * rozmiaru zostaja duchy poprzedniej klatki. */
function synchronizuj() {
  const r = plotno.getBoundingClientRect();
  stan.dpr = window.devicePixelRatio || 1;
  const w = Math.max(1, Math.round(r.width * stan.dpr));
  const h = Math.max(1, Math.round(r.height * stan.dpr));
  if (plotno.width !== w || plotno.height !== h) { plotno.width = w; plotno.height = h; }
  if (stan.sesja) {
    stan.skalaMin = Math.max(r.width / stan.sesja.obraz.szerokosc, r.height / stan.sesja.obraz.wysokosc);
    if (stan.skala < stan.skalaMin) stan.skala = stan.skalaMin;
  }
  return r;
}

const naEkran = (px, r) => ({
  x: r.width / 2 + (px.x - stan.srodek.x) * stan.skala,
  y: r.height / 2 + (px.y - stan.srodek.y) * stan.skala,
});

function sciezkaZaokraglona(x, y, w, h, promien) {
  ctx.beginPath();
  ctx.moveTo(x + promien, y);
  ctx.arcTo(x + w, y, x + w, y + h, promien);
  ctx.arcTo(x + w, y + h, x, y + h, promien);
  ctx.arcTo(x, y + h, x, y, promien);
  ctx.arcTo(x, y, x + w, y, promien);
  ctx.closePath();
}

/* Etykieta w bialej pastylce, trzymana w granicach kadru. */
function pastylka(tekst, cx, cy, r, tlo = '#fff', kolor = KOLOR.tekst) {
  ctx.font = `700 14px ${CZCIONKA}`;
  const w = ctx.measureText(tekst).width + 22, h = 28;
  const x = Math.min(Math.max(cx - w / 2, 8), r.width - w - 8);
  const y = Math.min(Math.max(cy - h / 2, 8), r.height - h - 8);
  ctx.save();
  ctx.shadowColor = 'rgba(15, 23, 42, .28)'; ctx.shadowBlur = 10; ctx.shadowOffsetY = 2;
  ctx.fillStyle = tlo;
  sciezkaZaokraglona(x, y, w, h, h / 2);
  ctx.fill();
  ctx.restore();
  ctx.fillStyle = kolor;
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillText(tekst, x + w / 2, y + h / 2 + 1);
  ctx.textAlign = 'start'; ctx.textBaseline = 'alphabetic';
}

/* Linia z ciemna obwodka - czytelna na jasnym tynku i na ciemnej fudze. */
function linia(a, b, kolor, grubosc) {
  ctx.lineCap = 'round';
  ctx.strokeStyle = KOLOR.obwodka; ctx.lineWidth = grubosc + 3;
  ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
  ctx.strokeStyle = kolor; ctx.lineWidth = grubosc;
  ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
}

function kropka(p, kolor, promien = 5) {
  ctx.beginPath(); ctx.arc(p.x, p.y, promien + 1.8, 0, Math.PI * 2);
  ctx.fillStyle = KOLOR.obwodka; ctx.fill();
  ctx.beginPath(); ctx.arc(p.x, p.y, promien, 0, Math.PI * 2);
  ctx.fillStyle = kolor; ctx.fill();
}

/* Pastylka odsunieta prostopadle od odcinka, zeby go nie zaslaniala. */
function opisOdcinka(a, b, tekst, r, tlo, kolor) {
  const dx = b.x - a.x, dy = b.y - a.y, dl = Math.hypot(dx, dy) || 1;
  let nx = -dy / dl, ny = dx / dl;
  if (ny > 0 || (ny === 0 && nx > 0)) { nx = -nx; ny = -ny; }
  pastylka(tekst, (a.x + b.x) / 2 + nx * 22, (a.y + b.y) / 2 + ny * 22, r, tlo, kolor);
}

function celownik(r, kolor) {
  const x = r.width / 2, y = r.height / 2;
  const okrag = (promien) => { ctx.beginPath(); ctx.arc(x, y, promien, 0, Math.PI * 2); ctx.stroke(); };
  ctx.strokeStyle = KOLOR.obwodka; ctx.lineWidth = 5; okrag(17);
  ctx.strokeStyle = kolor; ctx.lineWidth = 2.5; okrag(17);
  ctx.beginPath(); ctx.arc(x, y, 4.2, 0, Math.PI * 2); ctx.fillStyle = KOLOR.obwodka; ctx.fill();
  ctx.beginPath(); ctx.arc(x, y, 2.6, 0, Math.PI * 2); ctx.fillStyle = kolor; ctx.fill();
}

function rysuj() {
  const r = synchronizuj();
  ctx.setTransform(stan.dpr, 0, 0, stan.dpr, 0, 0);
  ctx.clearRect(0, 0, r.width, r.height);
  if (!stan.obraz) return;

  // tlo poza zdjeciem - jasne i neutralne, wyraznie "nie zdjecie"
  ctx.fillStyle = '#D9DDE3';
  ctx.fillRect(0, 0, r.width, r.height);

  const ox = r.width / 2 - stan.srodek.x * stan.skala;
  const oy = r.height / 2 - stan.srodek.y * stan.skala;
  const ow = stan.sesja.obraz.szerokosc * stan.skala;
  const oh = stan.sesja.obraz.wysokosc * stan.skala;
  ctx.imageSmoothingEnabled = stan.skala < 1.5;
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(stan.obraz, ox, oy, ow, oh);
  ctx.strokeStyle = 'rgba(15, 23, 42, .35)'; ctx.lineWidth = 1;
  ctx.strokeRect(Math.round(ox) - .5, Math.round(oy) - .5, Math.round(ow) + 1, Math.round(oh) + 1);

  // marker referencyjny - dyskretny obrys
  ctx.save();
  ctx.setLineDash([6, 5]); ctx.lineWidth = 1.5; ctx.strokeStyle = 'rgba(255, 255, 255, .9)';
  ctx.beginPath();
  stan.sesja.marker.narozniki_px.forEach(([x, y], i) => {
    const p = naEkran({ x, y }, r);
    i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y);
  });
  ctx.closePath(); ctx.stroke();
  ctx.restore();

  // zmierzone odcinki
  stan.odcinki.forEach((o, i) => {
    const a = naEkran(o.a, r), b = naEkran(o.b, r);
    const wyrozniony = stan.wyrozniony && stan.wyrozniony.typ === 'odcinek' && stan.wyrozniony.i === i;
    const kolor = wyrozniony ? KOLOR.tasma : KOLOR.linia;
    linia(a, b, kolor, 3);
    kropka(a, kolor); kropka(b, kolor);
    opisOdcinka(a, b, `${mm(roznica(o.a, o.b).l)} mm`, r, wyrozniony ? KOLOR.tasma : '#fff');
  });

  // punkty trybu wspolrzednych
  stan.punkty.forEach((p, i) => {
    const e = naEkran(p.px, r);
    const m = roznica(stan.zero, p.px);
    const wyrozniony = stan.wyrozniony && stan.wyrozniony.typ === 'punkt' && stan.wyrozniony.i === i;
    kropka(e, wyrozniony ? KOLOR.tasma : '#fff', 6);
    pastylka(`${mmZnak(m.dx)} × ${mmZnak(m.dy)}`, e.x, e.y - 26, r, wyrozniony ? KOLOR.tasma : '#fff');
  });
  if (stan.tryb === 'wspolrzedne') {
    const z = naEkran(stan.zero, r);
    ctx.strokeStyle = KOLOR.obwodka; ctx.lineWidth = 4;
    ctx.beginPath(); ctx.moveTo(z.x - 12, z.y); ctx.lineTo(z.x + 12, z.y);
    ctx.moveTo(z.x, z.y - 12); ctx.lineTo(z.x, z.y + 12); ctx.stroke();
    ctx.strokeStyle = KOLOR.tasma; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(z.x - 12, z.y); ctx.lineTo(z.x + 12, z.y);
    ctx.moveTo(z.x, z.y - 12); ctx.lineTo(z.x, z.y + 12); ctx.stroke();
  }

  // odcinek w trakcie mierzenia
  let kolorCelownika = '#fff';
  if (stan.poczatek) {
    const k = koniecOdcinka(stan.poczatek, stan.srodek);
    const kolor = k.os ? KOLOR.ok : KOLOR.tasma;
    const a = naEkran(stan.poczatek, r), b = naEkran(k.px, r);
    if (k.os) {
      // linia sledzaca wzdluz zlapanej osi
      const dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) || 1;
      ctx.save();
      ctx.setLineDash([4, 6]); ctx.lineWidth = 1.5; ctx.strokeStyle = 'rgba(22, 163, 74, .75)';
      ctx.beginPath();
      ctx.moveTo(a.x - dx / d * 4000, a.y - dy / d * 4000);
      ctx.lineTo(b.x + dx / d * 4000, b.y + dy / d * 4000);
      ctx.stroke();
      ctx.restore();
    }
    linia(a, b, kolor, 4);
    kropka(a, kolor, 6);
    kolorCelownika = kolor;
  }

  celownik(r, kolorCelownika);
  odswiezOdczyt();
}

/* ================================================================ odczyt */

function odswiezOdczyt() {
  const odczyt = $('odczyt'), fab = $('mierz'), podpowiedz = $('podpowiedz');
  const toastTrwa = Date.now() < toastDo;
  fab.classList.remove('w-toku', 'zlapane');
  odczyt.classList.remove('zlapane');
  if (!toastTrwa) podpowiedz.className = 'podpowiedz';

  if (stan.tryb === 'wspolrzedne') {
    const m = roznica(stan.zero, stan.srodek);
    odczyt.hidden = false;
    $('odczyt-wartosc').textContent = `${mmZnak(m.dx)} × ${mmZnak(m.dy)}`;
    $('odczyt-opis').textContent = 'szerokość × wysokość [mm]';
    fab.setAttribute('aria-label', 'Zapisz punkt');
    if (!toastTrwa) podpowiedz.textContent = 'Wyceluj i stuknij +, aby zapisać punkt';
    return;
  }

  fab.setAttribute('aria-label', stan.poczatek ? 'Zakończ wymiar' : 'Zacznij wymiar');
  if (stan.poczatek) {
    const k = koniecOdcinka(stan.poczatek, stan.srodek);
    const m = roznica(stan.poczatek, k.px);
    odczyt.hidden = false;
    odczyt.classList.toggle('zlapane', Boolean(k.os));
    fab.classList.add(k.os ? 'zlapane' : 'w-toku');
    $('odczyt-wartosc').textContent = `${mm(m.l)} mm`;
    $('odczyt-opis').textContent = k.os ? opisOsi(k.os) : `pod kątem ${stopnie(katOdcinka(stan.poczatek, k.px))}`;
    if (!toastTrwa) {
      podpowiedz.textContent = k.os
        ? `Wyrównano ${k.os === 'poziom' ? 'do poziomu' : 'do pionu'} — stuknij +`
        : 'Wyceluj w drugi punkt i stuknij +';
      podpowiedz.classList.toggle('zlapane', Boolean(k.os));
    }
    return;
  }

  odczyt.hidden = true;
  if (!toastTrwa) {
    podpowiedz.textContent = stan.odcinki.length
      ? 'Wyceluj w kolejny punkt, aby dodać wymiar'
      : 'Wyceluj w pierwszy punkt i stuknij +';
  }
}

function odswiezPrzyciski() {
  const ile = stan.odcinki.length + stan.punkty.length;
  $('licznik').hidden = ile === 0;
  $('licznik').textContent = ile;
  $('zakoncz').disabled = ile === 0;
  $('zapisz-z-listy').disabled = ile === 0;
  $('cofnij').disabled = !stan.poczatek && ile === 0;
}

/* ================================================================ gesty */

const dotyki = new Map();
let bazaPinch = null;

/* Celownik stoi na srodku ekranu, wiec musi dac sie doprowadzic do kazdego
 * piksela zdjecia - rowniez do narozy i krawedzi, bo wlasnie do nich
 * najczesciej sie mierzy. Srodek ograniczamy wiec do granic obrazu, a nie
 * do granic widocznego wycinka; obszar poza zdjeciem rysujemy jako
 * neutralne tlo z wyrazna krawedzia. */
function ogranicz() {
  stan.srodek.x = Math.min(Math.max(stan.srodek.x, 0), stan.sesja.obraz.szerokosc);
  stan.srodek.y = Math.min(Math.max(stan.srodek.y, 0), stan.sesja.obraz.wysokosc);
}

function zoom(mnoznik) {
  stan.skala = Math.min(Math.max(stan.skala * mnoznik, stan.skalaMin), stan.skalaMin * ZOOM_MAX);
  ogranicz(); rysuj();
}

plotno.addEventListener('pointerdown', (e) => {
  plotno.setPointerCapture(e.pointerId);
  dotyki.set(e.pointerId, { x: e.clientX, y: e.clientY });
  bazaPinch = null;
});
plotno.addEventListener('pointermove', (e) => {
  const prev = dotyki.get(e.pointerId);
  if (!prev || !stan.obraz) return;
  const teraz = { x: e.clientX, y: e.clientY };
  dotyki.set(e.pointerId, teraz);
  if (dotyki.size === 1) {
    stan.srodek.x -= (teraz.x - prev.x) / stan.skala;
    stan.srodek.y -= (teraz.y - prev.y) / stan.skala;
    ogranicz(); rysuj();
  } else if (dotyki.size === 2) {
    const [a, b] = [...dotyki.values()];
    const rozstaw = Math.hypot(a.x - b.x, a.y - b.y);
    if (bazaPinch === null) { bazaPinch = { rozstaw, skala: stan.skala }; return; }
    if (bazaPinch.rozstaw > 0) {
      stan.skala = Math.min(Math.max(bazaPinch.skala * (rozstaw / bazaPinch.rozstaw),
        stan.skalaMin), stan.skalaMin * ZOOM_MAX);
      ogranicz(); rysuj();
    }
  }
});
const koniecDotyku = (e) => { dotyki.delete(e.pointerId); if (dotyki.size < 2) bazaPinch = null; };
plotno.addEventListener('pointerup', koniecDotyku);
plotno.addEventListener('pointercancel', koniecDotyku);
plotno.addEventListener('wheel', (e) => {
  if (!stan.obraz) return;
  e.preventDefault();
  zoom(e.deltaY < 0 ? 1.18 : 1 / 1.18);
}, { passive: false });

$('zoom-plus').onclick = () => zoom(1.6);
$('zoom-minus').onclick = () => zoom(1 / 1.6);

/* Klawiatura na komputerze: Enter lub spacja mierzy, Ctrl+Z cofa. */
document.addEventListener('keydown', (e) => {
  if ($('ekran-pomiar').hidden || arkuszOtwarty() || !$('instruktaz').hidden) return;
  if (e.target.tagName === 'INPUT') return;
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('mierz').click(); }
  else if ((e.key === 'z' && (e.ctrlKey || e.metaKey)) || e.key === 'Backspace') { e.preventDefault(); cofnij(); }
  else if (e.key === '+' || e.key === '=') zoom(1.3);
  else if (e.key === '-') zoom(1 / 1.3);
});

/* ================================================================ pomiar */

$('instruktaz-ok').onclick = () => { zamknij('instruktaz'); pamietaj('miarka-instruktaz', '1'); };

$('mierz').onclick = () => {
  stan.wyrozniony = null;
  if (stan.tryb === 'wspolrzedne') {
    stan.punkty.push({ nazwa: `Punkt ${stan.nastepnyPunkt++}`, px: { ...stan.srodek } });
    const m = roznica(stan.zero, stan.srodek);
    toast(`Zapisano punkt ${mmZnak(m.dx)} × ${mmZnak(m.dy)} mm`);
  } else if (!stan.poczatek) {
    stan.poczatek = { ...stan.srodek };
  } else {
    const k = koniecOdcinka(stan.poczatek, stan.srodek);
    const odcinek = { nazwa: `Wymiar ${stan.nastepnyOdcinek++}`, a: stan.poczatek, b: k.px, os: k.os };
    stan.odcinki.push(odcinek);
    stan.poczatek = null;
    toast(`Dodano ${mm(roznica(odcinek.a, odcinek.b).l)} mm`);
  }
  if (navigator.vibrate) navigator.vibrate(12);
  odswiezPrzyciski(); rysuj();
};

function cofnij() {
  if (stan.poczatek) {
    stan.poczatek = null;
  } else if (stan.tryb === 'wspolrzedne' && stan.punkty.length) {
    stan.punkty.pop(); stan.nastepnyPunkt--;
    toast('Usunięto ostatni punkt', '');
  } else if (stan.odcinki.length) {
    stan.odcinki.pop(); stan.nastepnyOdcinek--;
    toast('Usunięto ostatni wymiar', '');
  } else if (stan.punkty.length) {
    stan.punkty.pop(); stan.nastepnyPunkt--;
  }
  stan.wyrozniony = null;
  odswiezPrzyciski(); odswiezListe(); rysuj();
}
$('cofnij').onclick = cofnij;

/* ================================================================ lista */

function wiersz({ nazwa, wartosc, szczegoly, znak, onNazwa, onPokaz, onUsun }) {
  const li = document.createElement('li');
  li.className = 'wymiar-wiersz';

  const kropkaZnak = document.createElement('span');
  kropkaZnak.className = `wymiar-znak ${znak || ''}`;

  const tresc = document.createElement('div');
  tresc.className = 'wymiar-tresc';
  tresc.onclick = (e) => { if (!e.target.closest('.wymiar-nazwa')) onPokaz(); };

  const przyciskNazwy = document.createElement('button');
  przyciskNazwy.type = 'button'; przyciskNazwy.className = 'wymiar-nazwa';
  przyciskNazwy.innerHTML = IKONA.olowek;
  przyciskNazwy.prepend(document.createTextNode(nazwa));
  przyciskNazwy.setAttribute('aria-label', `Zmień nazwę: ${nazwa}`);
  przyciskNazwy.onclick = onNazwa;

  const opis = document.createElement('span');
  opis.className = 'wymiar-szczegoly'; opis.textContent = szczegoly;
  tresc.append(przyciskNazwy, opis);

  const w = document.createElement('span');
  w.className = 'wymiar-wartosc'; w.textContent = wartosc;

  const usun = document.createElement('button');
  usun.type = 'button'; usun.className = 'ikona-btn'; usun.innerHTML = IKONA.usun;
  usun.setAttribute('aria-label', `Usuń: ${nazwa}`);
  usun.onclick = onUsun;

  li.append(kropkaZnak, tresc, w, usun);
  return li;
}

function odswiezListe() {
  const lista = $('lista-pomiarow');
  lista.textContent = '';

  stan.odcinki.forEach((o, i) => {
    const m = roznica(o.a, o.b);
    lista.appendChild(wiersz({
      nazwa: o.nazwa,
      wartosc: `${mm(m.l)} mm`,
      szczegoly: o.os
        ? `${opisOsi(o.os)}`
        : `szer. ${mm(m.dx)} · wys. ${mm(m.dy)} · ${stopnie(katOdcinka(o.a, o.b))}`,
      znak: o.os,
      onNazwa: () => otworzNazwe('odcinek', i),
      onPokaz: () => pokazNaZdjeciu('odcinek', i),
      onUsun: () => { stan.odcinki.splice(i, 1); stan.wyrozniony = null; odswiezListe(); odswiezPrzyciski(); rysuj(); },
    }));
  });

  stan.punkty.forEach((p, i) => {
    const m = roznica(stan.zero, p.px);
    lista.appendChild(wiersz({
      nazwa: p.nazwa,
      wartosc: `${mmZnak(m.dx)} × ${mmZnak(m.dy)}`,
      szczegoly: 'współrzędne od zera [mm]',
      onNazwa: () => otworzNazwe('punkt', i),
      onPokaz: () => pokazNaZdjeciu('punkt', i),
      onUsun: () => { stan.punkty.splice(i, 1); stan.wyrozniony = null; odswiezListe(); odswiezPrzyciski(); rysuj(); },
    }));
  });

  $('pusto').hidden = stan.odcinki.length + stan.punkty.length > 0;
}

/* Dotkniecie wiersza pokazuje ten wymiar na zdjeciu. */
function pokazNaZdjeciu(typ, i) {
  const cel = typ === 'odcinek'
    ? { x: (stan.odcinki[i].a.x + stan.odcinki[i].b.x) / 2, y: (stan.odcinki[i].a.y + stan.odcinki[i].b.y) / 2 }
    : stan.punkty[i].px;
  stan.srodek = { ...cel };
  stan.wyrozniony = { typ, i };
  zamknij('arkusz-lista');
  ogranicz(); rysuj();
  setTimeout(() => { stan.wyrozniony = null; rysuj(); }, 2200);
}

$('lista-otworz').onclick = () => { odswiezListe(); otworz('arkusz-lista'); };
$('ortho').onchange = rysuj;

/* ================================================================ nazwy */

function otworzNazwe(typ, i) {
  stan.nazywany = { typ, i };
  const biezaca = typ === 'odcinek' ? stan.odcinki[i].nazwa : stan.punkty[i].nazwa;
  const pojemnik = $('etykietki');
  pojemnik.textContent = '';
  ETYKIETY.forEach((e) => {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'chip'; b.textContent = e;
    if (e === biezaca) b.classList.add('wybrany');
    b.onclick = () => { $('nazwa-wlasna').value = e; zapiszNazwe(); };
    pojemnik.appendChild(b);
  });
  $('nazwa-wlasna').value = /^(Wymiar|Punkt) \d+$/.test(biezaca) ? '' : biezaca;
  $('nazwa-wlasna').placeholder = biezaca;
  otworz('arkusz-nazwa');
}

function zapiszNazwe() {
  if (!stan.nazywany) return;
  const nowa = $('nazwa-wlasna').value.trim();
  if (nowa) {
    const { typ, i } = stan.nazywany;
    (typ === 'odcinek' ? stan.odcinki : stan.punkty)[i].nazwa = nowa;
  }
  stan.nazywany = null;
  zamknij('arkusz-nazwa');
  odswiezListe(); rysuj();
}
$('nazwa-zapisz').onclick = zapiszNazwe;
$('nazwa-wlasna').addEventListener('keydown', (e) => { if (e.key === 'Enter') zapiszNazwe(); });

/* ================================================================ wspolrzedne */

function opisZera() {
  const oba = stan.zrodloX !== 'marker' && stan.zrodloY !== 'marker';
  const tekst = stan.zrodloX === 'marker' && stan.zrodloY === 'marker' ? 'zero: marker'
    : oba ? 'zero: narożnik'
    : stan.zrodloX !== 'marker' ? 'zero: krawędź' : 'zero: posadzka';
  $('tryb-chip-zero').textContent = tekst;
}

function ustawTryb(tryb) {
  stan.tryb = tryb;
  stan.poczatek = null;
  $('tryb-wsp').checked = tryb === 'wspolrzedne';
  $('tryb-chip').hidden = tryb !== 'wspolrzedne';
  opisZera(); odswiezPrzyciski(); rysuj();
}

$('tryb-wsp').onchange = (e) => {
  ustawTryb(e.target.checked ? 'wspolrzedne' : 'miarka');
  zamknij('arkusz-lista');
  toast(e.target.checked ? 'Tryb współrzędnych — ustaw zero u góry' : 'Tryb zwykłej miarki', '');
};
$('tryb-chip').onclick = () => otworz('arkusz-zero');

function ustawZero(osie) {
  if (osie.includes('x')) { stan.zero.x = stan.srodek.x; stan.zrodloX = 'wskazany'; }
  if (osie.includes('y')) { stan.zero.y = stan.srodek.y; stan.zrodloY = 'wskazany'; }
  zamknij('arkusz-zero');
  opisZera(); odswiezListe(); rysuj();
  toast('Ustawiono zero w miejscu celownika');
}
$('zero-xy').onclick = () => ustawZero('xy');
$('zero-x').onclick = () => ustawZero('x');
$('zero-y').onclick = () => ustawZero('y');
$('zero-reset').onclick = () => {
  stan.zero = { x: stan.sesja.marker.osnowa_px[0], y: stan.sesja.marker.osnowa_px[1] };
  stan.zrodloX = 'marker'; stan.zrodloY = 'marker';
  zamknij('arkusz-zero');
  opisZera(); odswiezListe(); rysuj();
};
$('os-y-gora').onchange = () => { odswiezListe(); rysuj(); };

/* ================================================================ zdjecie */

$('ustawienia-otworz').onclick = () => otworz('arkusz-ustawienia');
$('pomoc-otworz').onclick = () => otworz('arkusz-pomoc');

function wybranoPlik(plik, input) {
  if (!plik) return;
  komunikat('status-start', '');
  wyslij(plik).finally(() => { input.value = ''; });
}
$('plik-aparat').onchange = (e) => wybranoPlik(e.target.files[0], e.target);
$('plik-galeria').onchange = (e) => wybranoPlik(e.target.files[0], e.target);

async function wyslij(plik) {
  const dane = new FormData();
  dane.append('image', plik);
  dane.append('marker_size_mm', $('marker-mm').value);
  dane.append('marker_id', $('marker-id').value);
  dane.append('mm_per_px', $('mm-px').value);

  $('postep-tekst').textContent = 'Szukam markera na zdjęciu…';
  otworz('postep');
  try {
    const odpowiedz = await fetch('/api/rectify', { method: 'POST', body: dane });
    $('postep-tekst').textContent = 'Prostuję perspektywę ściany…';
    const wynik = await odpowiedz.json();
    if (!odpowiedz.ok) throw new Error(wynik.blad || `Błąd serwera (${odpowiedz.status}).`);
    await uruchomPomiar(wynik);
  } catch (blad) {
    komunikat('status-start', blad.message, 'blad');
  } finally {
    zamknij('postep');
  }
}

function uruchomPomiar(sesja) {
  return new Promise((gotowe, blad) => {
    const obraz = new Image();
    obraz.onload = () => {
      Object.assign(stan, {
        sesja, obraz,
        zero: { x: sesja.marker.osnowa_px[0], y: sesja.marker.osnowa_px[1] },
        zrodloX: 'marker', zrodloY: 'marker',
        srodek: { x: sesja.obraz.szerokosc / 2, y: sesja.obraz.wysokosc / 2 },
        odcinki: [], nastepnyOdcinek: 1, poczatek: null,
        punkty: [], nastepnyPunkt: 1, wyrozniony: null,
      });
      pokazEkran('pomiar');
      requestAnimationFrame(() => {
        synchronizuj();
        stan.skala = stan.skalaMin * (sesja.powiekszenie_startowe || 1.4);
        if (sesja.srodek_startowy) stan.srodek = { x: sesja.srodek_startowy[0], y: sesja.srodek_startowy[1] };
        ogranicz();
        ustawTryb('miarka');
        odswiezListe();
        $('instruktaz').hidden = pamietane('miarka-instruktaz') === '1';
        const ostrzezenia = sesja.ostrzezenia || [];
        if (ostrzezenia.length) toast(ostrzezenia[0], 'uwaga', 6000);
        gotowe();
      });
    };
    obraz.onerror = () => blad(new Error('Nie udało się wczytać wyprostowanego zdjęcia.'));
    obraz.src = sesja.obraz.url;
  });
}

/* ================================================================ zapis */

function wynikLokalny() {
  return {
    liczba_odcinkow: stan.odcinki.length,
    liczba_punktow: stan.punkty.length,
    odcinki: stan.odcinki.map((o) => {
      const m = roznica(o.a, o.b);
      return { nazwa: o.nazwa, dlugosc_mm: m.l, dx_mm: m.dx, dy_mm: m.dy, kat_stopnie: katOdcinka(o.a, o.b) };
    }),
    punkty: stan.punkty.map((p) => {
      const m = roznica(stan.zero, p.px);
      return { nazwa: p.nazwa, x_mm: m.dx, y_mm: m.dy };
    }),
  };
}

async function zapisz() {
  if (!stan.odcinki.length && !stan.punkty.length) return;
  zamknij('arkusz-lista');
  if (DEMO) { pokazWynik(wynikLokalny()); return; }

  $('postep-tekst').textContent = 'Rysuję wymiary w pełnej rozdzielczości…';
  otworz('postep');
  try {
    const wlasneZero = stan.zrodloX !== 'marker' || stan.zrodloY !== 'marker';
    const odpowiedz = await fetch(`/api/session/${stan.sesja.session_id}/export`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        osnowa_px: wlasneZero ? [stan.zero.x, stan.zero.y] : null,
        os_y_w_gore: wGore(),
        punkty: stan.punkty.map((p) => ({ nazwa: p.nazwa, px: [p.px.x, p.px.y] })),
        odcinki: stan.odcinki.map((o) => ({ nazwa: o.nazwa, a: [o.a.x, o.a.y], b: [o.b.x, o.b.y] })),
      }),
    });
    const wynik = await odpowiedz.json();
    if (!odpowiedz.ok) throw new Error(wynik.blad || `Błąd serwera (${odpowiedz.status}).`);
    pokazWynik(wynik);
  } catch (blad) {
    toast(blad.message, 'uwaga', 5000);
  } finally {
    zamknij('postep');
  }
}
$('zakoncz').onclick = zapisz;
$('zapisz-z-listy').onclick = zapisz;

function pokazWynik(wynik) {
  const czesci = [];
  if (wynik.liczba_odcinkow) {
    czesci.push(`${wynik.liczba_odcinkow} ${odmiana(wynik.liczba_odcinkow, 'wymiar', 'wymiary', 'wymiarów')}`);
  }
  if (wynik.liczba_punktow) {
    czesci.push(`${wynik.liczba_punktow} ${odmiana(wynik.liczba_punktow, 'punkt', 'punkty', 'punktów')}`);
  }
  const plik = wynik.rozmiar_png ? ` · rysunek ${(wynik.rozmiar_png / 1048576).toFixed(1).replace('.', ',')} MB` : '';
  $('wynik-podsumowanie').textContent = czesci.join(' i ') + plik;

  const lista = $('wynik-lista');
  lista.textContent = '';
  const dodaj = (nazwa, wartosc, szczegoly) => {
    const li = document.createElement('li');
    li.className = 'wymiar-wiersz';
    li.innerHTML = '<div class="wymiar-tresc"><span class="wymiar-nazwa"></span>' +
      '<span class="wymiar-szczegoly"></span></div><span class="wymiar-wartosc"></span>';
    li.querySelector('.wymiar-nazwa').textContent = nazwa;
    li.querySelector('.wymiar-szczegoly').textContent = szczegoly;
    li.querySelector('.wymiar-wartosc').textContent = wartosc;
    lista.appendChild(li);
  };
  (wynik.odcinki || []).forEach((o) => {
    const prosty = Math.abs(o.kat_stopnie) < 1e-6 || Math.abs(Math.abs(o.kat_stopnie) - 90) < 1e-6;
    dodaj(o.nazwa, `${mm(o.dlugosc_mm)} mm`,
      prosty ? opisOsi(Math.abs(o.kat_stopnie) < 1e-6 ? 'poziom' : 'pion')
             : `szer. ${mm(o.dx_mm)} · wys. ${mm(o.dy_mm)}`);
  });
  (wynik.punkty || []).forEach((p) => dodaj(p.nazwa, `${mmZnak(p.x_mm)} × ${mmZnak(p.y_mm)}`, 'współrzędne [mm]'));

  $('wynik-pliki').hidden = Boolean(DEMO);
  $('wynik-demo').hidden = !DEMO;
  if (!DEMO) { $('link-png').href = wynik.png; $('link-json').href = wynik.json; }
  pokazEkran('wynik');
}

$('wroc').onclick = () => pokazEkran('pomiar');

function noweZdjecie() {
  zamknij('arkusz-wyjscie');
  stan.sesja = null; stan.obraz = null;
  komunikat('status-start', '');
  pokazEkran('start');
}
$('nowe').onclick = noweZdjecie;
$('wyjdz').onclick = () => {
  if (stan.odcinki.length || stan.punkty.length) otworz('arkusz-wyjscie');
  else noweZdjecie();
};
$('wyjdz-potwierdz').onclick = noweZdjecie;

/* ================================================================ start */

new ResizeObserver(() => { if (stan.obraz && !$('ekran-pomiar').hidden) { ogranicz(); rysuj(); } }).observe(plotno);

if (DEMO) {
  $('akcje-plik').hidden = true;
  $('ustawienia-otworz').hidden = true;
  $('akcje-demo').hidden = false;
  $('pomoc-demo').hidden = false;
  const lista = $('pomoc-demo-lista');
  (DEMO.znane || []).forEach(([opis, wartosc]) => {
    const w = document.createElement('div');
    w.className = 'wiersz-dystans';
    w.innerHTML = '<span></span><b></b>';
    w.querySelector('span').textContent = opis;
    w.querySelector('b').textContent = `${mm(wartosc)} mm`;
    lista.appendChild(w);
  });
  $('demo-start').onclick = () => {
    $('postep-tekst').textContent = 'Prostuję perspektywę ściany…';
    otworz('postep');
    setTimeout(() => {
      uruchomPomiar({ ...DEMO.sesja, obraz: { ...DEMO.sesja.obraz, url: DEMO.obraz } })
        .finally(() => zamknij('postep'));
    }, 450);
  };
}

odswiezPrzyciski();
