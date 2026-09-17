# CS2-pelivoimat — toimeksiannot Cowork-agenteille

Anna nämä yksi kerrallaan, järjestyksessä. Älä anna tehtävää N+1 ennen kuin
tehtävän N hyväksymiskriteeri on oikeasti täyttynyt.

---

## Tehtävä 0 — Kertoimien keruu (KÄYNNISTÄ ENSIN)

**Toimeksianto:**
Rakenna skripti, joka hakee CS2-otteluiden kertoimet kahdesti: heti kun linja avautuu
ja mahdollisimman lähellä ottelun alkua. Tallenna CSV/SQLite-tauluun: ottelun tunniste,
joukkueet, turnaus, formaatti (Bo1/Bo3), aikaleima, lähde, molempien kerroin.
Aja se ajastettuna päivittäin, ei käsin.

**Tuotos:** toimiva ajastettu keruu + tietokanta, joka karttuu joka päivä.

**Hyväksymiskriteeri:** näet 7 päivän jälkeen tietokannassa sekä avaus- että
sulkeutuvat kertoimet vähintään 50 ottelulle.

**Huom agentille:** selvitä ensin mitkä lähteet ovat teknisesti ja käyttöehdoiltaan
käytettävissä. Jos jokin lähde estää automaattiset pyynnöt, raportoi se, älä kierrä sitä.

---

## Tehtävä 1 — Historiadata

**Toimeksianto:**
Kokoa 24 kuukauden historia: ottelut, kartat, karttakohtaiset kierrostulokset
(myös puoliaikatulos CT/T-erottelua varten), turnaus, LAN/online, päivämäärä,
molempien joukkueiden kokoonpanot ottelun hetkellä.

**Lähteet, joita agentin pitää arvioida:**
- `github.com/ValveSoftware/counter-strike_regional_standings` — julkinen kaava ja
  päivittäiset snapshotit, koneluettava, ilmainen
- `cs2api` (pip) — bo3.gg-wrapper
- Liquipedia — rosterit ja transferit

**Hyväksymiskriteeri:** taulukko, jossa vähintään 5000 karttariviä, ja jokaisella
rivillä kummankin joukkueen kokoonpano sen päivän mukaan. Ilman kokoonpanoja data on
arvotonta rosterimuutosten takia.

---

## Tehtävä 2 — Backtest-harness (ENNEN MALLIA)

**Toimeksianto:**
Rakenna arviointikehikko, joka ottaa sisään minkä tahansa ennustefunktion ja palauttaa:
log loss, Brier, kalibrointikäyrä 10 korissa, sekä samat luvut markkinan
marginaalipoistetulle todennäköisyydelle. Walk-forward: fittaus ennen hetkeä T,
testi T:n jälkeen, liukuva ikkuna.

**Hyväksymiskriteeri:** ajat harnessin kahdella tyhmällä vertailumallilla
— (a) aina 50/50, (b) korkeampi VRS-sija voittaa — ja saat järkevät luvut.
Markkinan log lossin pitää olla selvästi molempia parempi. Jos ei ole, harness on rikki.

---

## Tehtävä 3 — Kierrostason Elo

**Toimeksianto:**
Toteuta kierrostason Elo: p_r = 1/(1+e^(−d/s)), päivitys voitettujen kierrosten
osuudella, eksponentiaalinen aikavaimennus. Fittaa s, K ja puoliintumisaika
walk-forward-harnessilla. Ei vielä karttoja.

**Hyväksymiskriteeri:** log loss parempi kuin molemmat vertailumallit.
Ero markkinaan raportoidaan, ei piiloteta.

---

## Tehtävä 4 — Karttapoikkeamat ja veto

**Toimeksianto:**
Lisää karttakohtaiset poikkeamat kutistuksella m̂ = (n/(n+k))·m_raaka.
Rakenna veto-malli, joka ennustaa mitkä kartat pelataan.
Lisää ottelun sisäinen Bayes-päivitys kartan 1 jälkeen.

**Hyväksymiskriteeri:** jokainen lisäys erikseen — paransiko log lossia
walk-forwardissa? Jos ei, se otetaan pois. Agentin pitää raportoida myös ne
lisäykset, jotka eivät auttaneet.

---

## Tehtävä 5 — Formipaino λ

**Toimeksianto:**
Aja kaksi rinnakkaista ratingia (puoliintumisajat n. 180 vrk ja 21 vrk).
PV = R_hidas + λ·(R_nopea − R_hidas). Hae λ ruudukosta 0…1.
Raportoi jokaiselle λ:lle sekä log loss että CLV verrattuna sulkeutuvaan kertoimeen.

**Hyväksymiskriteeri:** taulukko λ vs. molemmat mittarit.
Jos paras λ on 0, agentin pitää sanoa se suoraan eikä etsiä kiertotietä.

---

## Tehtävä 6 — Päivittäinen raportti

**Toimeksianto:**
Tuota päivittäin: (A) 30 joukkueen pelivoimataulukko karttapoikkeamineen,
(B) tulevien otteluiden näkymä p_malli / o_reilu / markkinakerroin / EV / Kelly,
(C) kumulatiivinen seurantaloki CLV:llä.

**Pakollinen jokaiseen raporttiin:** mallin rullaava log loss ja markkinan log loss
rinnakkain viimeisen 100 ottelun ajalta. Jos malli häviää markkinalle, se näkyy
raportin yläreunassa, ei liitteessä.

---

## Tehtävä 7 — Tuottavuuden seuranta-agentti (pyörii jatkuvasti)

**Toimeksianto:**
Simuloi joka päivä, miten malli olisi tuottanut sen päivän otteluissa, ja kerää
tuloksista aikasarja, josta näkyy paraneeko suorituskyky datan karttuessa.

**Päivittäinen sykli:**

1. Ennen päivän otteluita: jäädytä mallin tila snapshotiksi (`malli_2026-09-12`).
2. Tuota ennuste ja EV jokaiselle ottelulle **sillä kertoimella, joka on tarjolla
   päätöshetkellä** — ei sulkeutuvalla.
3. Kirjaa jokainen ottelu lokiin riippumatta siitä ylittyikö EV-kynnys.
   Merkitse erikseen: olisi lyöty / ei olisi lyöty.
4. Otteluiden jälkeen: kirjaa tulos, toteutunut tuotto, sulkeutuva kerroin ja CLV.

**Kaksi virhettä, jotka mitätöivät koko seurannan — agentin pitää estää nämä:**

- **Lookahead.** Vanhoja päiviä ei saa arvioida nykyisellä mallilla. Käytä aina sen
  päivän jäädytettyä snapshotia. Jos malli on nähnyt ottelun tuloksen, tulos on fiktiota.
- **Väärä hinta.** Jos simuloinnissa käytetään sulkeutuvaa kerrointa panoshintana,
  tuotto näyttää systemaattisesti paremmalta kuin se oikeasti olisi.

**Mittarit, tärkeysjärjestyksessä:**

| Mittari | Mitä kertoo | Kuinka nopeasti luotettava |
|---|---|---|
| CLV | Onko edge todellinen | ~100–200 vetoa |
| Log loss vs. markkina | Onko malli tarkempi | ~200 ottelua |
| Toteutunut ROI € | Lopullinen totuus | ~1000+ vetoa |

Raportoi ROI aina yhdessä varianssihaarukan kanssa. 3 %:n edgellä 500 vedon jälkeen
tappiollinen tulos on täysin normaali — älä anna agentin tulkita sitä mallin viaksi
eikä voitollista tulosta todisteeksi.

**Oppimiskäyrä (tämä vastaa varsinaiseen kysymykseesi):**

- Laske kaikki kolme mittaria rullaavina 100 vedon kohorteina ja piirrä aikajanalle.
- Aja rinnakkain **kaksi versiota:** (a) malli, joka on fitattu vain 6 kk historialla,
  (b) malli, joka on fitattu kaikella siihen mennessä kertyneellä datalla.
  Ero näiden välillä on se, mitä lisädata oikeasti tuo. Pelkkä "tulos paranee ajan myötä"
  ei erota datan hyötyä siitä, että markkina tai meta muuttui.
- Odotusarvo: log loss paranee nopeasti ensimmäisten kuukausien aikana ja tasaantuu
  sitten. Jos se paranee edelleen 12 kk:n jälkeen, jokin fitataan liian löysästi.

**Segmentointi — tästä löytyy se, missä edge asuu:**

Pilko kaikki mittarit vähintään näin:

- turnaustaso (S / A / B)
- LAN vs. online
- Bo1 vs. Bo3
- suosikki (p > 0,65) vs. tasainen vs. altavastaaja
- Δforma-korit (esim. alle −50, −50…+50, yli +50)
- rosterimuutosliput päällä / pois

Δforma-korit ovat suoraan teesisi testi: jos edge on positiivinen vain korissa,
jossa formipoikkeama on iso, teesi pitää. Jos se on tasaisesti jakautunut,
formikomponentti ei ole se, mikä tuottaa.

**Hyväksymiskriteeri:** kuukausiraportti, jossa on oppimiskäyrä, segmenttitaulukko ja
selkeä lause siitä, voittaako malli sulkeutuvan linjan vai ei. Ei pelkkää tuottokäyrää.

**Pysäytyssääntö, joka annetaan agentille etukäteen:**
Jos CLV on 300 vedon jälkeen ≤ 0, agentin pitää raportoida tämä otsikkotasolla
ja esittää projektin lopettamista, ei ehdottaa lisäparametrien virittämistä.

---

## Mitä et voi ulkoistaa

- **Päätös panoskoosta.** Agentti laskee Kellyn, sinä päätät kertoimen ja katon.
- **Päätös lopettamisesta.** Jos CLV on 300 vedon jälkeen nolla tai negatiivinen,
  projekti on epäonnistunut. Agentti ei kerro tätä oma-aloitteisesti — kysy se.
- **Sen huomaaminen, että agentti on miellyttänyt sinua.** Ohjeista jokaiseen
  toimeksiantoon: "raportoi myös se, mikä ei toiminut, ja vertaa aina markkinaan."
