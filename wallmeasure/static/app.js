/* Pomiar na telefonie.
 *
 * Zasada: celownik jest nieruchomy na srodku ekranu, a uzytkownik przesuwa pod
 * nim obraz. Palec nigdy nie zasłania mierzonego detalu - to jedyny sposob na
 * precyzyjne wskazanie punktu na dotykowym ekranie.
 *
 * Wszystkie odczyty licza sie lokalnie z trzech liczb otrzymanych z serwera:
 * pozycji osnowy, skali mm/px i rozmiaru obrazu. Serwer odpytywany jest tylko
 * przy wysylce zdjecia i przy zapisie wyniku.
 */
'use strict';

const $ = (id) => document.getElementById(id);

const stan = {
  sesja: null,        // odpowiedz z /api/rectify
  obraz: null,        // HTMLImageElement z wyprostowana sciana
  skala: 1,           // powiekszenie obrazu na ekranie
  skalaMin: 1,
  srodek: { x: 0, y: 0 },   // punkt obrazu (px) pod celownikiem
  osnowa: { x: 0, y: 0 },   // punkt (0,0) w px obrazu
  zrodloX: 'marker',   // 'marker' albo 'wskazany' - osobno dla kazdej osi
  zrodloY: 'marker',
  tryb: 'miarka',      // 'miarka' (punkt-punkt) albo 'punkty' (tabela wspolrzednych)
  odcinki: [],
  nastepnyOdcinek: 1,
  poczatek: null,      // pierwszy koniec odcinka, gdy pomiar jest w toku
  punkty: [],
  nastepneId: 1,
  odniesienie: null,   // nazwa punktu, od ktorego liczy zywy odczyt (null = od zera)
};

/* --- przeliczenia --------------------------------------------------------- */

function roznicaMm(od, do_) {
  const mm = stan.sesja.mm_na_piksel;
  const dx = (do_.x - od.x) * mm;
  const dy = (do_.y - od.y) * mm * ($('os-y-gora').checked ? -1 : 1);
  return { dx, dy, l: Math.hypot(dx, dy) };
}

/* Przyciaganie do poziomu i pionu, jak ORTHO w programach CAD: gdy odcinek
 * jest blisko kata prostego, prostujemy go dokladnie i mowimy o tym wprost.
 * Bez tego trafienie w rowne 0 albo 90 stopni palcem jest praktycznie
 * niemozliwe, a przy montazu to najczestszy przypadek. */
const TOLERANCJA_ORTHO = 4;   // stopnie

function katOdcinka(a, b) {
  const kat = Math.atan2(-(b.y - a.y), b.x - a.x) * 180 / Math.PI;
  if (kat > 90) return kat - 180;
  if (kat <= -90) return kat + 180;
  return kat;
}

function koniecOdcinka(od, kursor) {
  const dx = kursor.x - od.x, dy = kursor.y - od.y;
  if (!$('ortho').checked || (dx === 0 && dy === 0)) {
    return { px: { ...kursor }, os: null };
  }
  const kat = Math.abs(Math.atan2(dy, dx) * 180 / Math.PI);   // 0..180
  if (kat <= TOLERANCJA_ORTHO || kat >= 180 - TOLERANCJA_ORTHO) {
    return { px: { x: kursor.x, y: od.y }, os: 'poziom' };
  }
  if (Math.abs(kat - 90) <= TOLERANCJA_ORTHO) {
    return { px: { x: od.x, y: kursor.y }, os: 'pion' };
  }
  return { px: { ...kursor }, os: null };
}

function dlugoscMm(a, b) {
  return Math.hypot(b.x - a.x, b.y - a.y) * stan.sesja.mm_na_piksel;
}

function punktOdniesienia() {
  return stan.punkty.find((p) => p.nazwa === stan.odniesienie) || null;
}

function naMilimetry(px) {
  const mm = stan.sesja.mm_na_piksel;
  const dx = (px.x - stan.osnowa.x) * mm;
  const dy = (px.y - stan.osnowa.y) * mm * ($('os-y-gora').checked ? -1 : 1);
  return { dx, dy, l: Math.hypot(dx, dy) };
}

const fmt = (wartosc, znak) => {
  const tekst = Math.abs(wartosc).toFixed(1).replace('.', ',');
  if (!znak) return tekst;
  return (wartosc < 0 ? '−' : '+') + tekst;
};

/* --- rysowanie ------------------------------------------------------------ */

const plotno = $('plotno');
const ctx = plotno.getContext('2d');
let dpr = 1;

/* Bufor rysowania musi nadazac za rozmiarem elementu. Panel z punktami rosnie
 * przy kazdym pomiarze i skraca plotno - gdyby bufor zostal wiekszy, dolna jego
 * czesc nigdy nie byłaby czyszczona i pokazywalaby duchy poprzedniej klatki. */
function synchronizujRozmiar() {
  const prostokat = plotno.getBoundingClientRect();
  dpr = window.devicePixelRatio || 1;
  const szerokosc = Math.max(1, Math.round(prostokat.width * dpr));
  const wysokosc = Math.max(1, Math.round(prostokat.height * dpr));
  if (plotno.width !== szerokosc || plotno.height !== wysokosc) {
    plotno.width = szerokosc;
    plotno.height = wysokosc;
  }
  if (stan.sesja) {
    stan.skalaMin = Math.min(
      prostokat.width / stan.sesja.obraz.szerokosc,
      prostokat.height / stan.sesja.obraz.wysokosc,
    );
    if (stan.skala < stan.skalaMin) stan.skala = stan.skalaMin;
  }
  return prostokat;
}

function dopasujPlotno() {
  if (stan.obraz) rysuj();
}

new ResizeObserver(() => { if (stan.obraz) rysuj(); }).observe(plotno);

// Punkt obrazu -> wspolrzedne ekranu (CSS px, srodek plotna = stan.srodek).
function naEkran(px) {
  const prostokat = plotno.getBoundingClientRect();
  return {
    x: prostokat.width / 2 + (px.x - stan.srodek.x) * stan.skala,
    y: prostokat.height / 2 + (px.y - stan.srodek.y) * stan.skala,
  };
}

function krzyzyk(punkt, kolor, promien, grubosc) {
  const p = naEkran(punkt);
  const luka = promien * 0.35;
  ctx.strokeStyle = kolor;
  ctx.lineWidth = grubosc;
  ctx.beginPath();
  ctx.arc(p.x, p.y, promien, 0, Math.PI * 2);
  ctx.moveTo(p.x - promien - luka, p.y); ctx.lineTo(p.x - luka, p.y);
  ctx.moveTo(p.x + luka, p.y); ctx.lineTo(p.x + promien + luka, p.y);
  ctx.moveTo(p.x, p.y - promien - luka); ctx.lineTo(p.x, p.y - luka);
  ctx.moveTo(p.x, p.y + luka); ctx.lineTo(p.x, p.y + promien + luka);
  ctx.stroke();
  return p;
}

function etykieta(tekst, x, y, kolor) {
  ctx.font = '600 13px system-ui, sans-serif';
  const szerokosc = ctx.measureText(tekst).width;
  ctx.fillStyle = 'rgba(20,23,28,.82)';
  ctx.fillRect(x - 5, y - 15, szerokosc + 10, 20);
  ctx.fillStyle = kolor;
  ctx.fillText(tekst, x, y);
}

function rysuj() {
  const prostokat = synchronizujRozmiar();
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, prostokat.width, prostokat.height);
  if (!stan.obraz) return;

  // Obraz sciany.
  const lewo = prostokat.width / 2 - stan.srodek.x * stan.skala;
  const gora = prostokat.height / 2 - stan.srodek.y * stan.skala;
  ctx.imageSmoothingEnabled = stan.skala < 1;
  ctx.drawImage(
    stan.obraz, lewo, gora,
    stan.sesja.obraz.szerokosc * stan.skala,
    stan.sesja.obraz.wysokosc * stan.skala,
  );

  // Marker referencyjny.
  const rogi = stan.sesja.marker.narozniki_px;
  ctx.strokeStyle = '#ffb020';
  ctx.lineWidth = 2;
  ctx.beginPath();
  rogi.forEach(([x, y], i) => {
    const p = naEkran({ x, y });
    i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y);
  });
  ctx.closePath();
  ctx.stroke();

  // Linie wymiarowe do ostatniego punktu.
  const ostatni = stan.punkty[stan.punkty.length - 1];
  if (ostatni) {
    const o = naEkran(stan.osnowa);
    const c = naEkran(ostatni.px);
    ctx.strokeStyle = '#4ea1ff';
    ctx.lineWidth = 2;
    ctx.setLineDash([7, 6]);
    ctx.beginPath();
    ctx.moveTo(o.x, o.y); ctx.lineTo(c.x, o.y); ctx.lineTo(c.x, c.y);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(o.x, o.y); ctx.lineTo(c.x, c.y);
    ctx.stroke();
  }

  // Odcinki mierzone od punktu do punktu.
  const odcinekNaEkranie = (a, b, kolor, tekst) => {
    const pa = naEkran(a), pb = naEkran(b);
    ctx.strokeStyle = kolor;
    ctx.lineWidth = 3;
    ctx.beginPath(); ctx.moveTo(pa.x, pa.y); ctx.lineTo(pb.x, pb.y); ctx.stroke();
    const dx = pb.x - pa.x, dy = pb.y - pa.y;
    const dl = Math.hypot(dx, dy);
    if (dl > 1) {
      const px = -dy / dl * 9, py = dx / dl * 9;
      ctx.beginPath();
      ctx.moveTo(pa.x - px, pa.y - py); ctx.lineTo(pa.x + px, pa.y + py);
      ctx.moveTo(pb.x - px, pb.y - py); ctx.lineTo(pb.x + px, pb.y + py);
      ctx.stroke();
    }
    if (tekst) etykieta(tekst, (pa.x + pb.x) / 2 + 12, (pa.y + pb.y) / 2 - 8, kolor);
  };

  stan.odcinki.forEach((odc) => {
    odcinekNaEkranie(odc.a, odc.b, '#ff8a4e', `${odc.nazwa}  ${fmt(dlugoscMm(odc.a, odc.b))} mm`);
  });

  // Odcinek w trakcie mierzenia - drugi koniec podaza za celownikiem.
  if (stan.poczatek) {
    const k = koniecOdcinka(stan.poczatek, stan.srodek);
    const kolor = k.os ? '#4fd07a' : '#ffb020';
    const kat = katOdcinka(stan.poczatek, k.px);
    const opis = k.os
      ? `${fmt(dlugoscMm(stan.poczatek, k.px))} mm  ${k.os === 'poziom' ? 'POZIOM' : 'PION'}`
      : `${fmt(dlugoscMm(stan.poczatek, k.px))} mm  ${fmt(kat, true)}\u00B0`;

    if (k.os) {
      // Linia sledzaca przedluzona poza koniec - sygnal, ze os jest zlapana.
      const a = naEkran(stan.poczatek), b = naEkran(k.px);
      const dx = b.x - a.x, dy = b.y - a.y, dl = Math.hypot(dx, dy) || 1;
      ctx.strokeStyle = 'rgba(79, 208, 122, .5)';
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 5]);
      ctx.beginPath();
      ctx.moveTo(a.x - dx / dl * 2000, a.y - dy / dl * 2000);
      ctx.lineTo(b.x + dx / dl * 2000, b.y + dy / dl * 2000);
      ctx.stroke();
      ctx.setLineDash([]);
      // Cienka odnoga do celownika, zeby bylo widac, ze punkt zostal wyprostowany.
      const c = naEkran(stan.srodek);
      ctx.strokeStyle = 'rgba(79, 208, 122, .75)';
      ctx.setLineDash([2, 4]);
      ctx.beginPath(); ctx.moveTo(b.x, b.y); ctx.lineTo(c.x, c.y); ctx.stroke();
      ctx.setLineDash([]);
    }

    ctx.setLineDash([8, 6]);
    odcinekNaEkranie(stan.poczatek, k.px, kolor, opis);
    ctx.setLineDash([]);
  }

  // Zmierzone punkty.
  stan.punkty.forEach((punkt) => {
    const p = krzyzyk(punkt.px, '#4fd07a', 13, 2);
    etykieta(punkt.nazwa, p.x + 17, p.y - 9, '#4fd07a');
  });

  // Punkt bazowy.
  krzyzyk(stan.osnowa, '#ff6b6b', 16, 2);

  // Celownik - zawsze dokladnie na srodku ekranu. Rysowany dwukrotnie:
  // ciemna obwodka pod spodem sprawia, ze jest czytelny i na jasnym tynku,
  // i na ciemnej fudze.
  const sx = prostokat.width / 2;
  const sy = prostokat.height / 2;
  // Celownik jest celowo maly (promien 20 px). Dluzsze ramiona zachodzilyby na
  // obrys puszki gniazdka - czyli zaslanialyby to, w co uzytkownik celuje.
  const ramiona = () => {
    ctx.beginPath();
    ctx.moveTo(sx - 20, sy); ctx.lineTo(sx - 9, sy);
    ctx.moveTo(sx + 9, sy); ctx.lineTo(sx + 20, sy);
    ctx.moveTo(sx, sy - 20); ctx.lineTo(sx, sy - 9);
    ctx.moveTo(sx, sy + 9); ctx.lineTo(sx, sy + 20);
    ctx.arc(sx, sy, 4, 0, Math.PI * 2);
    ctx.stroke();
  };
  ctx.lineCap = 'round';
  ctx.strokeStyle = 'rgba(12,14,18,.85)';
  ctx.lineWidth = 5;
  ramiona();
  ctx.strokeStyle = '#ffb020';
  ctx.lineWidth = 2;
  ramiona();
  ctx.lineCap = 'butt';

  odswiezOdczyt();
}

function odswiezOdczyt() {
  if (stan.poczatek) {
    const k = koniecOdcinka(stan.poczatek, stan.srodek);
    const { dx, dy, l } = roznicaMm(stan.poczatek, k.px);
    const znacznik = $('odczyt-skad');
    znacznik.classList.toggle('zlapane', Boolean(k.os));
    znacznik.textContent = k.os
      ? (k.os === 'poziom' ? 'poziom' : 'pion')
      : `${fmt(katOdcinka(stan.poczatek, k.px), true)}\u00B0`;
    $('odczyt-x').textContent = `X ${fmt(dx, true)}`;
    $('odczyt-y').textContent = `Y ${fmt(dy, true)}`;
    $('odczyt-l').textContent = `L ${fmt(l)}`;
    return;
  }
  const odniesienie = punktOdniesienia();
  const { dx, dy, l } = odniesienie
    ? roznicaMm(odniesienie.px, stan.srodek)
    : naMilimetry(stan.srodek);
  $('odczyt-skad').classList.remove('zlapane');
  $('odczyt-skad').textContent = odniesienie ? `od ${odniesienie.nazwa}` : 'od zera';
  $('odczyt-x').textContent = `X ${fmt(dx, true)}`;
  $('odczyt-y').textContent = `Y ${fmt(dy, true)}`;
  $('odczyt-l').textContent = `L ${fmt(l)}`;
}

/* --- gesty: przesuwanie i pinch zoom -------------------------------------- */

const dotyki = new Map();
let bazaPinch = null;

function ogranicz() {
  const w = stan.sesja.obraz.szerokosc;
  const h = stan.sesja.obraz.wysokosc;
  stan.srodek.x = Math.min(Math.max(stan.srodek.x, 0), w);
  stan.srodek.y = Math.min(Math.max(stan.srodek.y, 0), h);
}

plotno.addEventListener('pointerdown', (zdarzenie) => {
  plotno.setPointerCapture(zdarzenie.pointerId);
  dotyki.set(zdarzenie.pointerId, { x: zdarzenie.clientX, y: zdarzenie.clientY });
  bazaPinch = null;
});

plotno.addEventListener('pointermove', (zdarzenie) => {
  const poprzedni = dotyki.get(zdarzenie.pointerId);
  if (!poprzedni || !stan.obraz) return;
  const biezacy = { x: zdarzenie.clientX, y: zdarzenie.clientY };
  dotyki.set(zdarzenie.pointerId, biezacy);

  if (dotyki.size === 1) {
    // Przesuwanie: obraz jedzie za palcem, celownik stoi.
    stan.srodek.x -= (biezacy.x - poprzedni.x) / stan.skala;
    stan.srodek.y -= (biezacy.y - poprzedni.y) / stan.skala;
    ogranicz();
    rysuj();
    return;
  }

  if (dotyki.size === 2) {
    const [a, b] = [...dotyki.values()];
    const rozstaw = Math.hypot(a.x - b.x, a.y - b.y);
    if (bazaPinch === null) { bazaPinch = { rozstaw, skala: stan.skala }; return; }
    if (bazaPinch.rozstaw > 0) {
      ustawSkale(bazaPinch.skala * (rozstaw / bazaPinch.rozstaw));
    }
  }
});

function koniecDotyku(zdarzenie) {
  dotyki.delete(zdarzenie.pointerId);
  if (dotyki.size < 2) bazaPinch = null;
}
plotno.addEventListener('pointerup', koniecDotyku);
plotno.addEventListener('pointercancel', koniecDotyku);

plotno.addEventListener('wheel', (zdarzenie) => {
  if (!stan.obraz) return;
  zdarzenie.preventDefault();
  ustawSkale(stan.skala * (zdarzenie.deltaY < 0 ? 1.15 : 1 / 1.15));
}, { passive: false });

function ustawSkale(nowa) {
  // Celownik jest w srodku ekranu, wiec zoom zawsze dziala wzgledem mierzonego
  // punktu - stan.srodek nie wymaga korekty.
  stan.skala = Math.min(Math.max(nowa, stan.skalaMin), stan.skalaMin * 40);
  rysuj();
}

$('zoom-plus').onclick = () => ustawSkale(stan.skala * 1.6);
$('zoom-minus').onclick = () => ustawSkale(stan.skala / 1.6);

/* --- punkty --------------------------------------------------------------- */

function odswiezListe() {
  const lista = $('lista-punktow');
  lista.innerHTML = '';
  stan.punkty.forEach((punkt, indeks) => {
    const { dx, dy, l } = naMilimetry(punkt.px);
    const poprzedni = indeks ? stan.punkty[indeks - 1] : null;
    const rozstaw = poprzedni ? roznicaMm(poprzedni.px, punkt.px) : null;

    const element = document.createElement('li');
    if (punkt.nazwa === stan.odniesienie) element.classList.add('odniesienie');
    element.innerHTML =
      `<span class="nazwa">${punkt.nazwa}</span>` +
      `<button class="tresc-punktu" type="button">` +
        `<span class="wartosci">X ${fmt(dx, true)} &nbsp; Y ${fmt(dy, true)} &nbsp; L ${fmt(l)} mm</span>` +
        (rozstaw
          ? `<span class="rozstaw">od ${poprzedni.nazwa}: <b>${fmt(rozstaw.l)} mm</b>` +
            ` &nbsp;(${fmt(rozstaw.dx, true)} / ${fmt(rozstaw.dy, true)})</span>`
          : '') +
      `</button>` +
      `<button class="usun" aria-label="Usuń ${punkt.nazwa}">×</button>`;

    // Klikniecie wiersza przelacza zywy odczyt na pomiar od tego punktu.
    element.querySelector('.tresc-punktu').onclick = () => {
      stan.odniesienie = stan.odniesienie === punkt.nazwa ? null : punkt.nazwa;
      odswiezListe();
      rysuj();
    };
    element.querySelector('.usun').onclick = () => {
      if (stan.odniesienie === punkt.nazwa) stan.odniesienie = null;
      stan.punkty.splice(indeks, 1);
      odswiezListe();
      rysuj();
    };
    lista.appendChild(element);
  });
  $('licznik').textContent = `Zmierzone punkty: ${stan.punkty.length}`;
  $('cofnij').disabled = stan.punkty.length === 0;
  odswiezZapis();
}

$('dodaj').onclick = () => {
  stan.punkty.push({ nazwa: `P${stan.nastepneId++}`, px: { ...stan.srodek } });
  odswiezListe();
  rysuj();
  pokazStatus('status-pomiar', `Dodano ${stan.punkty[stan.punkty.length - 1].nazwa}.`, 'ok');
};

$('cofnij').onclick = () => {
  const usuniety = stan.punkty.pop();
  if (usuniety) {
    stan.nastepneId--;
    if (stan.odniesienie === usuniety.nazwa) stan.odniesienie = null;
  }
  odswiezListe();
  rysuj();
};

/* --- tryb miarki: klikasz poczatek, klikasz koniec ------------------------ */

function ustawTryb(tryb) {
  stan.tryb = tryb;
  stan.poczatek = null;
  $('tryb-miarka').classList.toggle('aktywny', tryb === 'miarka');
  $('tryb-punkty').classList.toggle('aktywny', tryb === 'punkty');
  $('akcje-miarka').hidden = tryb !== 'miarka';
  $('akcje-punkty').hidden = tryb === 'miarka';
  $('panel-miarka').hidden = tryb !== 'miarka';
  $('panel-punkty').hidden = tryb === 'miarka';
  odswiezOdcinek();
  rysuj();
}

function odswiezOdcinek() {
  $('odcinek').textContent = stan.poczatek ? 'Koniec odcinka' : 'Początek odcinka';
  $('odcinek-anuluj').disabled = !stan.poczatek;
}

function odswiezListeOdcinkow() {
  const lista = $('lista-odcinkow');
  lista.innerHTML = '';
  stan.odcinki.forEach((odc, indeks) => {
    const { dx, dy, l } = roznicaMm(odc.a, odc.b);
    const element = document.createElement('li');
    element.innerHTML =
      `<span class="nazwa">${odc.nazwa}</span>` +
      `<span class="wartosci"><b>${fmt(l)} mm</b>` +
      `<span class="rozstaw">poziom ${fmt(dx, true)} &nbsp; pion ${fmt(dy, true)}` +
      `&nbsp; ${odc.os ? (odc.os === 'poziom' ? '— poziomo' : '| pionowo')
                       : fmt(katOdcinka(odc.a, odc.b), true) + '\u00B0'}</span></span>` +
      `<button class="usun" aria-label="Usuń ${odc.nazwa}">×</button>`;
    element.querySelector('.usun').onclick = () => {
      stan.odcinki.splice(indeks, 1);
      odswiezListeOdcinkow();
      rysuj();
    };
    lista.appendChild(element);
  });
  $('licznik-odcinkow').textContent = `Zmierzone odcinki: ${stan.odcinki.length}`;
  odswiezZapis();
}

$('tryb-miarka').onclick = () => ustawTryb('miarka');
$('tryb-punkty').onclick = () => ustawTryb('punkty');

$('odcinek').onclick = () => {
  if (!stan.poczatek) {
    stan.poczatek = { ...stan.srodek };
    pokazStatus('status-pomiar', 'Naprowadź celownik na drugi punkt.', '');
  } else {
    const k = koniecOdcinka(stan.poczatek, stan.srodek);
    const odc = { nazwa: `O${stan.nastepnyOdcinek++}`, a: stan.poczatek, b: k.px, os: k.os };
    stan.odcinki.push(odc);
    stan.poczatek = null;
    const jak = odc.os === 'poziom' ? ' w poziomie' : odc.os === 'pion' ? ' w pionie' : '';
    pokazStatus('status-pomiar',
      `${odc.nazwa}: ${fmt(dlugoscMm(odc.a, odc.b))} mm${jak}.`, 'ok');
    odswiezListeOdcinkow();
  }
  odswiezOdcinek();
  rysuj();
};

$('odcinek-anuluj').onclick = () => {
  stan.poczatek = null;
  odswiezOdcinek();
  rysuj();
};

function odswiezZapis() {
  $('zapisz').disabled = stan.punkty.length === 0 && stan.odcinki.length === 0;
  $('pobieranie').classList.add('ukryty');
}

function osnowaWlasna() {
  return stan.zrodloX !== 'marker' || stan.zrodloY !== 'marker';
}

function odswiezOpisZera() {
  const opis = (zrodlo) => (zrodlo === 'marker' ? 'od markera' : 'wskazane');
  $('info-baza').textContent =
    `X ${opis(stan.zrodloX)}, Y ${opis(stan.zrodloY)}`;
  $('zero-reset').disabled = !osnowaWlasna();
}

function ustawZero(osie) {
  if (osie.includes('x')) { stan.osnowa.x = stan.srodek.x; stan.zrodloX = 'wskazany'; }
  if (osie.includes('y')) { stan.osnowa.y = stan.srodek.y; stan.zrodloY = 'wskazany'; }
  const nazwy = { x: 'Poziom (X)', y: 'Pion (Y)', xy: 'Oba wymiary' };
  pokazStatus('status-pomiar', `${nazwy[osie]} liczony od wskazanego miejsca.`, 'ok');
  odswiezOpisZera(); odswiezListe(); rysuj();
}

$('zero-xy').onclick = () => ustawZero('xy');
$('zero-x').onclick = () => ustawZero('x');
$('zero-y').onclick = () => ustawZero('y');
$('zero-reset').onclick = () => {
  stan.osnowa = { x: stan.sesja.marker.osnowa_px[0], y: stan.sesja.marker.osnowa_px[1] };
  stan.zrodloX = 'marker'; stan.zrodloY = 'marker';
  pokazStatus('status-pomiar', 'Zero wróciło na róg markera.', 'ok');
  odswiezOpisZera(); odswiezListe(); rysuj();
};

$('ortho').addEventListener('change', () => rysuj());

$('os-y-gora').addEventListener('change', () => {
  if (stan.sesja) { odswiezListe(); rysuj(); }
});

/* --- komunikacja z serwerem ----------------------------------------------- */

function pokazStatus(id, tekst, klasa = '') {
  const element = $(id);
  element.textContent = tekst;
  element.className = `status ${klasa}`;
}

$('plik').addEventListener('change', (zdarzenie) => {
  const plik = zdarzenie.target.files[0];
  $('nazwa-pliku').textContent = plik ? `${plik.name} (${(plik.size / 1048576).toFixed(1)} MB)` : '';
  $('wyslij').disabled = !plik;
  pokazStatus('status-start', '');
});

$('wyslij').onclick = async () => {
  const plik = $('plik').files[0];
  if (!plik) return;

  const dane = new FormData();
  dane.append('image', plik);
  dane.append('marker_size_mm', $('marker-mm').value);
  dane.append('marker_id', $('marker-id').value);
  dane.append('mm_per_px', $('mm-px').value);

  $('wyslij').disabled = true;
  pokazStatus('status-start', 'Szukam markera i prostuję perspektywę…');
  try {
    const odpowiedz = await fetch('/api/rectify', { method: 'POST', body: dane });
    const wynik = await odpowiedz.json();
    if (!odpowiedz.ok) throw new Error(wynik.blad || `Błąd serwera (${odpowiedz.status}).`);
    await uruchomPomiar(wynik);
  } catch (blad) {
    pokazStatus('status-start', blad.message, 'blad');
  } finally {
    $('wyslij').disabled = false;
  }
};

function uruchomPomiar(sesja) {
  return new Promise((gotowe, blad) => {
    const obraz = new Image();
    obraz.onload = () => {
      stan.sesja = sesja;
      stan.obraz = obraz;
      stan.osnowa = { x: sesja.marker.osnowa_px[0], y: sesja.marker.osnowa_px[1] };
      stan.zrodloX = 'marker';
      stan.zrodloY = 'marker';
      stan.srodek = { x: sesja.obraz.szerokosc / 2, y: sesja.obraz.wysokosc / 2 };
      stan.punkty = [];
      stan.nastepneId = 1;
      stan.odniesienie = null;
      stan.odcinki = [];
      stan.nastepnyOdcinek = 1;
      stan.poczatek = null;
      stan.skala = 1;

      $('raport').textContent = sesja.raport;
      odswiezOpisZera();
      $('ekran-start').classList.add('ukryty');
      $('ekran-pomiar').classList.remove('ukryty');

      requestAnimationFrame(() => {
        dopasujPlotno();
        stan.skala = stan.skalaMin;
        rysuj();
        odswiezListe();
        odswiezListeOdcinkow();
        ustawTryb('miarka');
        const ostrzezenia = sesja.ostrzezenia || [];
        pokazStatus(
          'status-pomiar',
          ostrzezenia.length
            ? ostrzezenia.join(' ')
            : 'Celownik na pierwszy punkt, potem "Początek odcinka".',
          ostrzezenia.length ? 'ostrzezenie' : '',
        );
        gotowe();
      });
    };
    obraz.onerror = () => blad(new Error('Nie udało się pobrać wyprostowanego obrazu.'));
    obraz.src = sesja.obraz.url;
  });
}

$('zapisz').onclick = async () => {
  if (!stan.punkty.length && !stan.odcinki.length) return;
  $('zapisz').disabled = true;
  pokazStatus('status-pomiar', 'Zapisuję w pełnej rozdzielczości…');
  try {
    const odpowiedz = await fetch(`/api/session/${stan.sesja.session_id}/export`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        osnowa_px: osnowaWlasna() ? [stan.osnowa.x, stan.osnowa.y] : null,
        os_y_w_gore: $('os-y-gora').checked,
        punkty: stan.punkty.map((punkt) => ({ nazwa: punkt.nazwa, px: [punkt.px.x, punkt.px.y] })),
        odcinki: stan.odcinki.map((odc) => ({
          nazwa: odc.nazwa, a: [odc.a.x, odc.a.y], b: [odc.b.x, odc.b.y],
        })),
      }),
    });
    const wynik = await odpowiedz.json();
    if (!odpowiedz.ok) throw new Error(wynik.blad || `Błąd serwera (${odpowiedz.status}).`);
    $('link-png').href = wynik.png;
    $('link-json').href = wynik.json;
    $('pobieranie').classList.remove('ukryty');
    pokazStatus(
      'status-pomiar',
      `Zapisano ${wynik.liczba_odcinkow} odcinków i ${wynik.liczba_punktow} punktów ` +
      `(PNG ${(wynik.rozmiar_png / 1048576).toFixed(1)} MB).`,
      'ok',
    );
    // Przy dluzszej liscie punktow przyciski pobierania sa ponizej krawedzi
    // panelu - bez tego uzytkownik klika "Zapisz" i nie widzi zadnej reakcji.
    $('pobieranie').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  } catch (blad) {
    pokazStatus('status-pomiar', blad.message, 'blad');
  } finally {
    $('zapisz').disabled = false;
  }
};

$('nowe').onclick = () => {
  $('ekran-pomiar').classList.add('ukryty');
  $('ekran-start').classList.remove('ukryty');
  $('plik').value = '';
  $('nazwa-pliku').textContent = '';
  $('wyslij').disabled = true;
  pokazStatus('status-start', '');
};

window.addEventListener('resize', dopasujPlotno);
window.addEventListener('orientationchange', () => setTimeout(dopasujPlotno, 250));
