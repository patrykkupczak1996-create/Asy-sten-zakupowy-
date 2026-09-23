/* Miarka ze zdjecia - warstwa interakcji.
 *
 * Zalozenie prowadzace caly interfejs: uzytkownik nie czyta instrukcji.
 * Na kazdym ekranie ma byc jedna oczywista rzecz do zrobienia, a program
 * mowi wprost, co sie dzieje i co bedzie dalej.
 *
 * Pomiar dziala na nieruchomym celowniku w srodku kadru - palec nigdy nie
 * zaslania mierzonego detalu. Wszystkie odczyty licza sie lokalnie z trzech
 * liczb otrzymanych z serwera (skala, osnowa, rozmiar obrazu), wiec
 * przesuwanie i zoom nie generuja ruchu sieciowego.
 */
'use strict';

const $ = (id) => document.getElementById(id);

const ETYKIETY = ['Gniazdko', 'Włącznik', 'Woda', 'Odpływ', 'Wentylacja',
                  'Narożnik', 'Krawędź', 'Wysokość', 'Szerokość'];
const TOLERANCJA_ORTHO = 4;        // stopnie - przy tylu prostujemy do osi
const ZOOM_MAX = 16;

const stan = {
  sesja: null,
  obraz: null,
  skala: 1,
  skalaMin: 1,
  dpr: 1,
  srodek: { x: 0, y: 0 },
  zero: { x: 0, y: 0 },
  zrodloX: 'marker',
  zrodloY: 'marker',
  odcinki: [],
  nastepnyOdcinek: 1,
  poczatek: null,
  punkty: [],
  nastepnyPunkt: 1,
  nazywany: null,
  crop: null,
};

/* --- drobiazgi ------------------------------------------------------------ */

const fmt = (v, znak) => {
  const t = Math.abs(v).toFixed(1).replace('.', ',');
  return znak ? (v < 0 ? '−' : '+') + t : t;
};

/* Polska odmiana liczebnikow - "2 wymiary", nie "2 wymiarow". */
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

function pokazEkran(nazwa) {
  ['start', 'pomiar', 'wynik'].forEach((e) => { $('ekran-' + e).hidden = e !== nazwa; });
  if (nazwa === 'pomiar') requestAnimationFrame(rysuj);
}

function status(id, tekst, klasa = '') {
  const el = $(id);
  el.textContent = tekst;
  el.className = `status ${klasa}`;
}

/* --- przeliczenia --------------------------------------------------------- */

function mmNaPiksel() { return stan.sesja.mm_na_piksel; }
const wGore = () => $('os-y-gora').checked;

function roznica(od, doP) {
  const mm = mmNaPiksel();
  const dx = (doP.x - od.x) * mm;
  const dy = (doP.y - od.y) * mm * (wGore() ? -1 : 1);
  return { dx, dy, l: Math.hypot(dx, dy) };
}

function odZera(px) { return roznica(stan.zero, px); }

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

/* --- rysowanie ------------------------------------------------------------ */

const plotno = $('plotno');
const ctx = plotno.getContext('2d');

/* Bufor rysowania musi nadazac za rozmiarem elementu - panel z pomiarami
 * rosnie i skraca plotno, a niedopasowany bufor zostawia duchy klatki. */
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

function naEkran(px, r) {
  return {
    x: r.width / 2 + (px.x - stan.srodek.x) * stan.skala,
    y: r.height / 2 + (px.y - stan.srodek.y) * stan.skala,
  };
}

/* Etykieta trzymana w kadrze - przy krawedzi zdjecia opis wymiaru
 * wyjezdzal poza plotno i stawal sie nieczytelny. */
function etykieta(tekst, x, y, kolor) {
  ctx.font = '700 13px -apple-system, "Segoe UI", Roboto, sans-serif';
  const w = ctx.measureText(tekst).width;
  const r = plotno.getBoundingClientRect();
  x = Math.min(Math.max(x, 8), Math.max(8, r.width - w - 8));
  y = Math.min(Math.max(y, 19), r.height - 8);
  ctx.fillStyle = 'rgba(10,13,18,.88)';
  ctx.fillRect(x - 6, y - 15, w + 12, 21);
  ctx.fillStyle = kolor;
  ctx.fillText(tekst, x, y);
}

function krzyzyk(p, kolor, promien) {
  const luka = promien * .38;
  ctx.strokeStyle = kolor; ctx.lineWidth = 2; ctx.lineCap = 'round';
  ctx.beginPath();
  ctx.arc(p.x, p.y, promien, 0, Math.PI * 2);
  ctx.moveTo(p.x - promien - luka, p.y); ctx.lineTo(p.x - luka, p.y);
  ctx.moveTo(p.x + luka, p.y); ctx.lineTo(p.x + promien + luka, p.y);
  ctx.moveTo(p.x, p.y - promien - luka); ctx.lineTo(p.x, p.y - luka);
  ctx.moveTo(p.x, p.y + luka); ctx.lineTo(p.x, p.y + promien + luka);
  ctx.stroke();
}

function odcinekNaEkranie(a, b, kolor, tekst, r) {
  const pa = naEkran(a, r), pb = naEkran(b, r);
  ctx.strokeStyle = kolor; ctx.lineWidth = 3; ctx.lineCap = 'butt';
  ctx.beginPath(); ctx.moveTo(pa.x, pa.y); ctx.lineTo(pb.x, pb.y); ctx.stroke();
  const dx = pb.x - pa.x, dy = pb.y - pa.y, dl = Math.hypot(dx, dy);
  if (dl > 1) {
    const nx = -dy / dl * 10, ny = dx / dl * 10;
    ctx.beginPath();
    ctx.moveTo(pa.x - nx, pa.y - ny); ctx.lineTo(pa.x + nx, pa.y + ny);
    ctx.moveTo(pb.x - nx, pb.y - ny); ctx.lineTo(pb.x + nx, pb.y + ny);
    ctx.stroke();
  }
  if (tekst) etykieta(tekst, (pa.x + pb.x) / 2 + 13, (pa.y + pb.y) / 2 - 9, kolor);
}

function rysuj() {
  const r = synchronizuj();
  ctx.setTransform(stan.dpr, 0, 0, stan.dpr, 0, 0);
  ctx.clearRect(0, 0, r.width, r.height);
  if (!stan.obraz) return;

  ctx.imageSmoothingEnabled = stan.skala < 1;
  ctx.drawImage(stan.obraz,
    r.width / 2 - stan.srodek.x * stan.skala,
    r.height / 2 - stan.srodek.y * stan.skala,
    stan.sesja.obraz.szerokosc * stan.skala,
    stan.sesja.obraz.wysokosc * stan.skala);

  // marker referencyjny
  ctx.strokeStyle = '#ffb020'; ctx.lineWidth = 2;
  ctx.beginPath();
  stan.sesja.marker.narozniki_px.forEach(([x, y], i) => {
    const p = naEkran({ x, y }, r);
    i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y);
  });
  ctx.closePath(); ctx.stroke();

  // zmierzone odcinki
  stan.odcinki.forEach((o) => {
    odcinekNaEkranie(o.a, o.b, '#ff8a4e', `${o.nazwa}  ${fmt(roznica(o.a, o.b).l)} mm`, r);
  });

  // odcinek w trakcie mierzenia
  if (stan.poczatek) {
    const k = koniecOdcinka(stan.poczatek, stan.srodek);
    const dl = roznica(stan.poczatek, k.px).l;
    if (k.os) {
      const a = naEkran(stan.poczatek, r), b = naEkran(k.px, r), c = naEkran(stan.srodek, r);
      const dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) || 1;
      ctx.strokeStyle = 'rgba(70,207,122,.45)'; ctx.lineWidth = 1; ctx.setLineDash([5, 6]);
      ctx.beginPath();
      ctx.moveTo(a.x - dx / d * 3000, a.y - dy / d * 3000);
      ctx.lineTo(b.x + dx / d * 3000, b.y + dy / d * 3000);
      ctx.stroke();
      ctx.strokeStyle = 'rgba(70,207,122,.8)'; ctx.setLineDash([2, 4]);
      ctx.beginPath(); ctx.moveTo(b.x, b.y); ctx.lineTo(c.x, c.y); ctx.stroke();
      ctx.setLineDash([]);
    }
    ctx.setLineDash([9, 6]);
    odcinekNaEkranie(stan.poczatek, k.px, k.os ? '#46cf7a' : '#ffb020',
      k.os ? `${fmt(dl)} mm  ${k.os === 'poziom' ? 'POZIOM' : 'PION'}`
           : `${fmt(dl)} mm  ${fmt(katOdcinka(stan.poczatek, k.px), true)}°`, r);
    ctx.setLineDash([]);
    krzyzyk(naEkran(stan.poczatek, r), '#ffb020', 11);
  }

  // punkty trybu wspolrzednych
  stan.punkty.forEach((p) => {
    const e = naEkran(p.px, r);
    krzyzyk(e, '#46cf7a', 11);
    etykieta(p.nazwa, e.x + 16, e.y - 9, '#46cf7a');
  });
  if (stan.zrodloX !== 'marker' || stan.zrodloY !== 'marker') {
    krzyzyk(naEkran(stan.zero, r), '#ff6b6b', 14);
  }

  // celownik - zawsze na srodku kadru
  const sx = r.width / 2, sy = r.height / 2;
  const ramie = () => {
    ctx.beginPath();
    ctx.moveTo(sx - 21, sy); ctx.lineTo(sx - 9, sy);
    ctx.moveTo(sx + 9, sy); ctx.lineTo(sx + 21, sy);
    ctx.moveTo(sx, sy - 21); ctx.lineTo(sx, sy - 9);
    ctx.moveTo(sx, sy + 9); ctx.lineTo(sx, sy + 21);
    ctx.arc(sx, sy, 4, 0, Math.PI * 2);
    ctx.stroke();
  };
  ctx.lineCap = 'round';
  ctx.strokeStyle = 'rgba(5,7,10,.85)'; ctx.lineWidth = 5.5; ramie();
  ctx.strokeStyle = '#ffb020'; ctx.lineWidth = 2; ramie();

  odswiezOdczyt();
}

function odswiezOdczyt() {
  const znacznik = $('znacznik');
  znacznik.classList.remove('zlapane', 'mierzy');

  if (stan.poczatek) {
    const k = koniecOdcinka(stan.poczatek, stan.srodek);
    const { dx, dy, l } = roznica(stan.poczatek, k.px);
    znacznik.classList.add(k.os ? 'zlapane' : 'mierzy');
    znacznik.textContent = k.os
      ? (k.os === 'poziom' ? 'poziom' : 'pion')
      : `${fmt(katOdcinka(stan.poczatek, k.px), true)}°`;
    $('odczyt-x').textContent = fmt(dx, true);
    $('odczyt-y').textContent = fmt(dy, true);
    $('odczyt-l').textContent = fmt(l);
    $('wskazowka').classList.toggle('zlapane', Boolean(k.os));
    $('wskazowka').innerHTML = k.os
      ? `Odcinek wyrównany do ${k.os === 'poziom' ? 'poziomu' : 'pionu'}. Naciśnij <b>Wskaż drugi punkt</b>.`
      : 'Naprowadź celownik na drugi punkt.';
    return;
  }

  const m = odZera(stan.srodek);
  znacznik.textContent = 'od markera';
  if (stan.zrodloX !== 'marker' || stan.zrodloY !== 'marker') znacznik.textContent = 'od zera';
  $('odczyt-x').textContent = fmt(m.dx, true);
  $('odczyt-y').textContent = fmt(m.dy, true);
  $('odczyt-l').textContent = fmt(m.l);
}

/* --- gesty ---------------------------------------------------------------- */

const dotyki = new Map();
let bazaPinch = null;

/* Kadr nie moze wyjechac poza zdjecie - inaczej przy krawedzi polowa ekranu
 * robi sie czarna i wyglada na usterke. Ograniczamy srodek tak, by widoczny
 * wycinek zawsze lezal w obrazie. */
function ogranicz() {
  const r = plotno.getBoundingClientRect();
  const polSzer = (r.width / 2) / stan.skala;
  const polWys = (r.height / 2) / stan.skala;
  const W = stan.sesja.obraz.szerokosc, H = stan.sesja.obraz.wysokosc;
  stan.srodek.x = W <= 2 * polSzer ? W / 2
    : Math.min(Math.max(stan.srodek.x, polSzer), W - polSzer);
  stan.srodek.y = H <= 2 * polWys ? H / 2
    : Math.min(Math.max(stan.srodek.y, polWys), H - polWys);
}

function zoom(mnoznik) {
  stan.skala = Math.min(Math.max(stan.skala * mnoznik, stan.skalaMin), stan.skalaMin * ZOOM_MAX);
  ogranicz();
  rysuj();
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
    if (Math.abs(teraz.x - prev.x) + Math.abs(teraz.y - prev.y) > 2) schowajInstruktaz();
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
  e.preventDefault(); zoom(e.deltaY < 0 ? 1.18 : 1 / 1.18);
}, { passive: false });

$('zoom-plus').onclick = () => zoom(1.6);
$('zoom-minus').onclick = () => zoom(1 / 1.6);

/* --- instruktaz przy pierwszym uruchomieniu -------------------------------- */

function schowajInstruktaz() {
  if ($('instruktaz').hidden) return;
  $('instruktaz').hidden = true;
  pamietaj('instruktaz-widziany', '1');
}
$('instruktaz-ok').onclick = schowajInstruktaz;

/* --- odcinki -------------------------------------------------------------- */

function odswiezPrzyciskMierzenia() {
  $('mierz').textContent = stan.poczatek ? 'Wskaż drugi punkt' : 'Wskaż pierwszy punkt';
  $('mierz-anuluj').hidden = !stan.poczatek;
  if (!stan.poczatek) {
    $('wskazowka').classList.remove('zlapane');
    $('wskazowka').textContent = stan.odcinki.length
      ? 'Wyceluj w kolejny punkt, żeby zmierzyć następny wymiar.'
      : 'Celownik na środku zdjęcia pokazuje mierzone miejsce.';
  }
}

function odswiezPomiary() {
  const lista = $('lista-pomiarow');
  lista.textContent = '';
  stan.odcinki.forEach((o, i) => {
    const m = roznica(o.a, o.b);
    const li = document.createElement('li');

    const nazwa = document.createElement('button');
    nazwa.type = 'button'; nazwa.className = 'nazwa'; nazwa.textContent = o.nazwa;
    nazwa.onclick = () => otworzNazwe('odcinek', i);

    const wymiar = document.createElement('span');
    wymiar.className = 'wymiar'; wymiar.textContent = `${fmt(m.l)} mm`;

    const szczegoly = document.createElement('span');
    szczegoly.className = 'szczegoly';
    szczegoly.textContent = `szer. ${fmt(m.dx, true)}   wys. ${fmt(m.dy, true)}   ` +
      (o.os ? (o.os === 'poziom' ? 'poziomo' : 'pionowo') : `${fmt(katOdcinka(o.a, o.b), true)}°`);

    const usun = document.createElement('button');
    usun.className = 'usun'; usun.textContent = '✕';
    usun.setAttribute('aria-label', 'Usuń ' + o.nazwa);
    usun.onclick = () => { stan.odcinki.splice(i, 1); odswiezPomiary(); rysuj(); };

    li.append(nazwa, wymiar, szczegoly, usun);
    lista.appendChild(li);
  });
  $('pusto').hidden = stan.odcinki.length > 0;
  $('zakoncz').disabled = stan.odcinki.length === 0 && stan.punkty.length === 0;
  odswiezPrzyciskMierzenia();
}

$('mierz').onclick = () => {
  schowajInstruktaz();
  if (!stan.poczatek) {
    stan.poczatek = { ...stan.srodek };
  } else {
    const k = koniecOdcinka(stan.poczatek, stan.srodek);
    const nazwa = `Wymiar ${stan.nastepnyOdcinek++}`;
    stan.odcinki.push({ nazwa, a: stan.poczatek, b: k.px, os: k.os });
    stan.poczatek = null;
    const dl = fmt(roznica(stan.odcinki[stan.odcinki.length - 1].a,
                           stan.odcinki[stan.odcinki.length - 1].b).l);
    status('status-pomiar', `Zapisano ${dl} mm. Dotknij nazwy, żeby ją zmienić.`, 'ok');
    odswiezPomiary();
  }
  odswiezPrzyciskMierzenia();
  rysuj();
};

$('mierz-anuluj').onclick = () => {
  stan.poczatek = null;
  odswiezPrzyciskMierzenia();
  rysuj();
};

/* --- nazywanie pomiarow --------------------------------------------------- */

function otworzNazwe(typ, indeks) {
  stan.nazywany = { typ, indeks };
  const biezaca = typ === 'odcinek' ? stan.odcinki[indeks].nazwa : stan.punkty[indeks].nazwa;
  const pojemnik = $('etykietki');
  pojemnik.textContent = '';
  ETYKIETY.forEach((e) => {
    const b = document.createElement('button');
    b.type = 'button'; b.textContent = e;
    if (e === biezaca) b.classList.add('wybrana');
    b.onclick = () => { $('nazwa-wlasna').value = e; zapiszNazwe(); };
    pojemnik.appendChild(b);
  });
  $('nazwa-wlasna').value = biezaca;
  $('arkusz-nazwa').hidden = false;
}

function zapiszNazwe() {
  if (!stan.nazywany) return;
  const nowa = $('nazwa-wlasna').value.trim();
  if (nowa) {
    const { typ, indeks } = stan.nazywany;
    if (typ === 'odcinek') stan.odcinki[indeks].nazwa = nowa;
    else stan.punkty[indeks].nazwa = nowa;
  }
  stan.nazywany = null;
  $('arkusz-nazwa').hidden = true;
  odswiezPomiary(); odswiezPunkty(); rysuj();
}
$('nazwa-zapisz').onclick = zapiszNazwe;
$('nazwa-anuluj').onclick = () => { stan.nazywany = null; $('arkusz-nazwa').hidden = true; };

/* --- arkusze pomocnicze --------------------------------------------------- */

$('pomoc-otworz').onclick = () => { $('arkusz-pomoc').hidden = false; };
$('pomoc-zamknij').onclick = () => { $('arkusz-pomoc').hidden = true; };
$('tryb-punkty-otworz').onclick = () => { $('arkusz-punkty').hidden = false; odswiezPunkty(); };
$('punkty-zamknij').onclick = () => { $('arkusz-punkty').hidden = true; };
[...document.querySelectorAll('.naklada.dolna')].forEach((n) => {
  n.addEventListener('click', (e) => { if (e.target === n) n.hidden = true; });
});

/* --- tryb wspolrzednych --------------------------------------------------- */

function opisZera() {
  const s = (z) => (z === 'marker' ? 'markera' : 'wskazanego miejsca');
  $('info-baza').textContent = `szerokość od ${s(stan.zrodloX)}, wysokość od ${s(stan.zrodloY)}`;
  $('zero-reset').disabled = stan.zrodloX === 'marker' && stan.zrodloY === 'marker';
}

function ustawZero(osie) {
  if (osie.includes('x')) { stan.zero.x = stan.srodek.x; stan.zrodloX = 'wskazany'; }
  if (osie.includes('y')) { stan.zero.y = stan.srodek.y; stan.zrodloY = 'wskazany'; }
  opisZera(); odswiezPunkty(); rysuj();
}
$('zero-xy').onclick = () => ustawZero('xy');
$('zero-x').onclick = () => ustawZero('x');
$('zero-y').onclick = () => ustawZero('y');
$('zero-reset').onclick = () => {
  stan.zero = { x: stan.sesja.marker.osnowa_px[0], y: stan.sesja.marker.osnowa_px[1] };
  stan.zrodloX = 'marker'; stan.zrodloY = 'marker';
  opisZera(); odswiezPunkty(); rysuj();
};
$('os-y-gora').onchange = () => { odswiezPomiary(); odswiezPunkty(); rysuj(); };
$('ortho').onchange = rysuj;

$('dodaj-punkt').onclick = () => {
  stan.punkty.push({ nazwa: `Punkt ${stan.nastepnyPunkt++}`, px: { ...stan.srodek } });
  odswiezPunkty(); odswiezPomiary(); rysuj();
};

function odswiezPunkty() {
  const lista = $('lista-punktow');
  lista.textContent = '';
  stan.punkty.forEach((p, i) => {
    const m = odZera(p.px);
    const li = document.createElement('li');
    const nazwa = document.createElement('button');
    nazwa.type = 'button'; nazwa.className = 'nazwa'; nazwa.textContent = p.nazwa;
    nazwa.onclick = () => otworzNazwe('punkt', i);
    const wymiar = document.createElement('span');
    wymiar.className = 'wymiar';
    wymiar.textContent = `${fmt(m.dx, true)} × ${fmt(m.dy, true)} mm`;
    const usun = document.createElement('button');
    usun.className = 'usun'; usun.textContent = '✕';
    usun.setAttribute('aria-label', 'Usuń ' + p.nazwa);
    usun.onclick = () => { stan.punkty.splice(i, 1); odswiezPunkty(); odswiezPomiary(); rysuj(); };
    li.append(nazwa, wymiar, usun);
    lista.appendChild(li);
  });
  $('zakoncz').disabled = stan.odcinki.length === 0 && stan.punkty.length === 0;
}

/* --- wysylka zdjecia ------------------------------------------------------ */

function wybranoPlik(plik) {
  if (!plik) return;
  $('nazwa-pliku').textContent = `${plik.name} · ${(plik.size / 1048576).toFixed(1)} MB`;
  status('status-start', '');
  wyslij(plik);
}
$('plik-aparat').onchange = (e) => wybranoPlik(e.target.files[0]);
$('plik-galeria').onchange = (e) => wybranoPlik(e.target.files[0]);

async function wyslij(plik) {
  const dane = new FormData();
  dane.append('image', plik);
  dane.append('marker_size_mm', $('marker-mm').value);
  dane.append('marker_id', $('marker-id').value);
  dane.append('mm_per_px', $('mm-px').value);

  $('postep').hidden = false;
  $('postep-tekst').textContent = 'Szukam markera na zdjęciu…';
  try {
    const odpowiedz = await fetch('/api/rectify', { method: 'POST', body: dane });
    $('postep-tekst').textContent = 'Prostuję perspektywę ściany…';
    const wynik = await odpowiedz.json();
    if (!odpowiedz.ok) throw new Error(wynik.blad || `Błąd serwera (${odpowiedz.status}).`);
    await uruchomPomiar(wynik);
  } catch (blad) {
    status('status-start', blad.message, 'blad');
  } finally {
    $('postep').hidden = true;
  }
}

function uruchomPomiar(sesja) {
  return new Promise((gotowe, blad) => {
    const obraz = new Image();
    obraz.onload = () => {
      stan.sesja = sesja;
      stan.obraz = obraz;
      stan.zero = { x: sesja.marker.osnowa_px[0], y: sesja.marker.osnowa_px[1] };
      stan.zrodloX = 'marker'; stan.zrodloY = 'marker';
      stan.srodek = { x: sesja.obraz.szerokosc / 2, y: sesja.obraz.wysokosc / 2 };
      stan.odcinki = []; stan.nastepnyOdcinek = 1; stan.poczatek = null;
      stan.punkty = []; stan.nastepnyPunkt = 1;

      pokazEkran('pomiar');
      requestAnimationFrame(() => {
        synchronizuj();
        stan.skala = stan.skalaMin * 1.4;
        ogranicz();
        opisZera(); odswiezPomiary(); odswiezPunkty(); rysuj();
        $('instruktaz').hidden = pamietane('instruktaz-widziany') === '1';
        const ostrzezenia = sesja.ostrzezenia || [];
        status('status-pomiar', ostrzezenia.join(' '), ostrzezenia.length ? 'ostrzezenie' : '');
        gotowe();
      });
    };
    obraz.onerror = () => blad(new Error('Nie udało się pobrać wyprostowanego zdjęcia.'));
    obraz.src = sesja.obraz.url;
  });
}

/* --- zapis i ekran wyniku ------------------------------------------------- */

$('zakoncz').onclick = async () => {
  if (!stan.odcinki.length && !stan.punkty.length) return;
  $('zakoncz').disabled = true;
  $('postep').hidden = false;
  $('postep-tekst').textContent = 'Rysuję wymiary w pełnej rozdzielczości…';
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
    status('status-pomiar', blad.message, 'blad');
  } finally {
    $('postep').hidden = true;
    $('zakoncz').disabled = false;
  }
};

function pokazWynik(wynik) {
  $('link-png').href = wynik.png;
  $('link-json').href = wynik.json;
  const czesci = [];
  if (wynik.liczba_odcinkow) {
    czesci.push(`${wynik.liczba_odcinkow} ` +
      odmiana(wynik.liczba_odcinkow, 'wymiar', 'wymiary', 'wymiarów'));
  }
  if (wynik.liczba_punktow) {
    czesci.push(`${wynik.liczba_punktow} ` +
      odmiana(wynik.liczba_punktow, 'punkt', 'punkty', 'punktów'));
  }
  $('wynik-podsumowanie').textContent =
    `${czesci.join(' i ')} · rysunek ${(wynik.rozmiar_png / 1048576).toFixed(1)} MB`;

  const lista = $('wynik-lista');
  lista.textContent = '';
  (wynik.odcinki || []).forEach((o) => {
    const li = document.createElement('li');
    li.innerHTML = `<span class="nazwa stala">${o.nazwa}</span>` +
      `<span class="wymiar">${fmt(o.dlugosc_mm)} mm</span>` +
      `<span class="szczegoly">szer. ${fmt(o.dx_mm, true)}   wys. ${fmt(o.dy_mm, true)}</span>`;
    lista.appendChild(li);
  });
  (wynik.punkty || []).forEach((p) => {
    const li = document.createElement('li');
    li.innerHTML = `<span class="nazwa stala">${p.nazwa}</span>` +
      `<span class="wymiar">${fmt(p.x_mm, true)} × ${fmt(p.y_mm, true)} mm</span>`;
    lista.appendChild(li);
  });
  pokazEkran('wynik');
}

$('wroc').onclick = () => pokazEkran('pomiar');

function nowePomiary() {
  stan.sesja = null; stan.obraz = null;
  $('plik-aparat').value = ''; $('plik-galeria').value = '';
  $('nazwa-pliku').textContent = '';
  status('status-start', '');
  pokazEkran('start');
}
$('nowe').onclick = nowePomiary;
$('nowe-2').onclick = nowePomiary;

new ResizeObserver(() => { if (stan.obraz) rysuj(); }).observe(plotno);
window.addEventListener('orientationchange', () => setTimeout(rysuj, 260));

odswiezPomiary();
