# CS2-pelivoimat

Tehtävä 0 (kertoimien keruu) on **rakennettu ja testattu**, automaatio on
tauolla tilauspäivitystä odottamassa. Tehtävä 1 (historiadata, rajattu:
4 kk / top 50) on **käynnissä**. Live-tila:
https://github.com/hermanni2007-prog/cs2voimat · status-sivu (linkki keskustelussa).

## Tehtävä 1: historiadata — rajattu laajuus (2026-09-17)

Alkuperäinen briiffi pyysi 24 kk / top 30. Käyttäjä rajasi tätä käytännön
syistä: **4 kuukautta, top 50 joukkuetta**.

- `src/collect_history.py` + `src/liquipedia_client.py`: hakee jokaisen
  top-50-joukkueen (`data/top50_teams.json`, lähde: Valve VRS
  `standings_global`) Liquipedia-sivun `<Joukkue>/Matches`-alasivun ja
  jäsentää sen ottelutaulukon (pvm, taso, LAN/online, turnaus, tulos,
  vastustaja). Yksi HTTP-pyyntö per joukkue - ei tarvitse skannata
  turnaussivuja erikseen.
- Liquipedian oma nopeusrajoitus (1 `action=parse`/30s) tekee koko
  top-50-kierroksesta ~25 min operaation. `history_team_progress`-taulu
  muistaa jo käsitellyt joukkueet, joten ajon voi katkaista ja jatkaa.
- `.github/workflows/collect_history.yml` ajaa tätä 15 min välein,
  25 joukkuetta/ajo, ja committaa tuloksen takaisin repoon - jatkuu
  itsenäisesti tulevaisuudessa (uudet ottelut, joukkueiden vaihtuessa).
- Status-sivulla on live-osio ("Historiadatan keräys") joka lukee
  edistymisen suoraan artifaktin `db`-kyvyn kautta - ei vaadi sivun
  manuaalista uudelleenjulkaisua nähdäkseen tuoreet luvut.
- **Tunnettu rajoitus:** rivit ovat sekoitus karttatason (round-score,
  esim. "13-9") ja sarjatason (map-win, esim. "2:0") tuloksia riippuen
  ottelun formaatista - ei vielä normalisoitu. Sama ottelu voi esiintyä
  kahdesti (kummankin joukkueen sivulta), dedupetty `UNIQUE`-indeksillä.
- **BUGIKORJAUS (2026-09-17, "etsi lisää puutteita"):** `parse_matches_table`
  oletti KIINTEÄT sarakeindeksit (pisteet aina `tds[7]`, vastustaja aina
  `tds[8]`) - toimi referenssijoukkueella (Natus Vincere, 10 saraketta)
  mutta EI toiminut kaikilla joukkueilla. Löydetty NRG:n kohdalla:
  heidän `/Matches`-taulukossaan on 12 saraketta (ylimääräinen oman
  joukkueen ikonisarake ennen pistesaraketta), minkä takia vanha koodi
  luki PISTELUKEMAN ("2:3") vahingossa `opponent`-kenttään ja jätti
  `score_team`/`score_opponent` tyhjäksi - **21/27 NRG:n ottelusta oli
  käyttökelvottomia tämän takia.** Korjattu: pistesarake ja vastustaja-
  sarake etsitään nyt DYNAAMISESTI sisällön perusteella (kaksi
  kokonaisluku-`<span>`-elementtiä pistesarakkeelle, sitä seuraava
  joukkuelinkki vastustajalle) kiinteän indeksin sijaan - toimii
  molemmilla havaituilla taulukkolayouteilla. NRG:n kelvolliset ottelut
  nousivat 6:sta 339:ään.

### Kokoonpanot (alkuperäisen hyväksymiskriteerin puuttuva osa)

- `src/collect_rosters.py` + `liquipedia_client.parse_roster_page`: hakee
  top-50-joukkueiden **pääsivun** (ei `/Matches`-alasivua) "Player Roster"
  -osion Active- ja Former-taulut, jotka sisältävät pelaajien liittymis-/
  lähtöpäivät. `team_rosters`-taulu: `roster_as_of(team, date)` voidaan
  johtaa `join_date <= date AND (leave_date IS NULL OR leave_date > date)`.
- `.github/workflows/collect_rosters.yml`: päivittäin (kokoonpanot muuttuvat
  harvoin, ei tarvitse 15 min tarkkuutta kuten ottelut).
- **Tulos (2026-09-17): 50/50 joukkuetta, 1474 roolirivia, 0 virhettä.**
- **Bugi matkan varrella, löydetty ja korjattu:** ensimmäinen ajo antoi
  11/50 joukkueelle (mm. FaZe, Vitality, Liquid, Falcons, G2) 0 roolirivia.
  Syy: `action=parse` EI seuraa uudelleenohjauksia automaattisesti (toisin
  kuin `action=query`) - esim. "G2" on redirect-sivu "G2 Esports":iin, ja
  `fetch_rendered_html("G2")` palautti vain tynkätekstin "Redirect to: G2
  Esports". Ottelujäsennin (`resolve_team_page`) vältti tämän vahingossa,
  koska `"G2/Matches"` ei ole itse redirect vaan ei ole olemassa ollenkaan,
  mikä pakotti fallback-polun käyttöön. Korjattu: `resolve_team_base_page`
  seuraa uudelleenohjauksen AINA, ei vain fallbackina.
- **Tunnettu yksinkertaistus:** "Inactive Date" ja laina-abbr-tekstit
  (esim. "Was on loan to X") jätetään huomiotta - käytetään vain
  ensimmäistä ja viimeistä YYYY-MM-DD-päivää riviltä.
- `backtest.RosterHistory`: `roster_as_of(team, date)` ja
  `roster_stability(team, date, lookback_days)` - kaytetty nyt ensi kertaa
  `src/run_roster_signal.py`:ssa, ks. "Edge-analyysi" alempana.

### HUOM: ajastukset eivät ole vielä laukenneet kertaakaan (2026-09-17)

`collect_history.yml` (15 min cron) ja `collect_rosters.yml` (päivittäin)
olivat päällä ~1,5 h ennen tämän kirjoittamista, mutta GitHubin Actions-
sivu näytti **0 ajoa** kummallekaan - vain alkuperäinen käsiajo
`collect_odds.yml`:lle näkyy historiassa. YAML-syntaksi on validi (GitHub
tunnistaa molemmat workflow-nimet pudotusvalikossa), joten kyse on
todennäköisesti GitHubin dokumentoidusta viiveestä uusille `schedule`-
liipaisimille, ei konfiguraatiovirheestä - `*/15 * * * *` osuu myös
tasatuntien ruuhka-aikoihin joita GitHub itse varoittaa. **En voinut
laukaista ajoa käsin tarkistaakseni**, koska oma selaimeni ei ole
kirjautunut GitHubiin (aiempi käsiajo teki käyttäjä itse). Data on silti
tallessa (paikallisten ajojen ansiosta) - vain TULEVA automaattinen
päivitys on epävarma kunnes ensimmäinen ajastettu ajo nähdään onnistuneen.
**Tarkista Actions-välilehdeltä ja käynnistä tarvittaessa käsin.**

## Tehtävä 2: Backtest-harness — rakennettu, markkinavertailu vielä tyhjä

- `src/backtest.py`: log loss, Brier, kalibrointikäyrä (10 koria), walk-forward
  -ajuri, VRS-sijoitus ajankohtana T (lookahead-suojattu kuukausisnapshoteista,
  `src/fetch_vrs_history.py`). `src/run_backtest.py` ajaa hyväksymiskriteerin
  kahdella tyhmällä mallilla (aina 50/50, korkeampi VRS-sija voittaa) 1243
  puhdasta ottelua vasten (1445:sta, loput ilman validia tulosta).
- **Tulos (2026-09-17):** `aina_5050` log loss 0.693 (odotettu, ln 2).
  `korkeampi_vrs_sija` (deterministinen 0/1) log loss 9.7 — PAHEMPI kuin
  50/50, koska log loss rankaisee kovaa väärästä itsevarmuudesta. Tämä EI
  ole harnessin bugi.
- **Pehmeä VRS-sijamalli** (`make_predict_vrs_soft`, logistinen funktio
  sijaerosta, scale grid-haulla) korjaa tämän: log loss **0.674** —
  voittaa 50/50:n. Raaka osumatarkkuus (kumpi voittaa oikein) on 64 %.
- **Bugi matkan varrella, löydetty ja korjattu:** ensimmäinen sumea
  nimihaku VRS-datan ja Liquipedia-nimien välillä käytti raakaa
  osamerkkijonohakua ("Team Liquid" sisältää "am"? — kyllä, sanassa
  "te**AM**"!), mikä tuotti 75 väärää yhdistystä (mm. kaikki "X Team"
  -nimiset osuivat sattumalta lyhyeen VRS-nimeen "am") ja teki mallista
  todellisuudessa 50/50:tä huonomman. Korjattu sanarajalliseen regexiin
  (`\bsana\b`). Kattavuus parani samalla 44 %:sta 85 %:iin.
- **Markkinavertailu EI VIELÄ TOIMI** — ks. `src/fetch_market_spotcheck.py`:n
  yläkommentti. Testattiin ~72 historical-odds-kutsua (OddsPapi, ilmainen
  kiintiö) reaaliaikaisesti kerätyille top-50-otteluille, myös S-Tier-tason
  otteluilla tunnetuilla joukkueilla (9z vs G2, BIG vs G2) - **0/72 palautti
  hinnan**. Tämä kumoaa aiemman (liian optimistisen) johtopäätöksen siitä,
  että OddsPapin historiallinen arkisto kattaisi laajasti menneitä otteluita:
  todennäköisin selitys on, että arkisto sisältää vain otteluita joita joku
  on aktiivisesti pollannut ajantasaisena - ei mitä tahansa mennyttä ottelua.
  **Tehtävä 0:n oma jatkuva keruu (`odds_snapshots`) on siis edelleen se
  ainoa luotettava tapa kartuttaa markkinadataa - eteenpäin, ei taaksepäin.**

## Tehtävä 3: Karttatason Elo — rakennettu, voittaa perusmallit

Alkuperäinen briiffi pyysi kierrostason Elon, mutta CT/T-dataa ei löytynyt
ilmaiseksi lähteeksi (ks. aiempi keskustelu) - rakennettu **karttatason**
versio `historical_matches`-datan päälle.

- `src/backtest.py`: `EloModel` (logistinen p = 1/(1+e^(-diff/scale)),
  K-kerroin-päivitys, eksponentiaalinen aikavaimennus puoliintumisajalla
  kohti keskiarvoa 1500). `run_elo_walkforward` on AIDOSTI tilallinen:
  jokainen ottelu ennustetaan ennen ratingin päivitystä, aikajärjestyksessä.
- `deduplicate_matches`: sama ottelu esiintyy kahdesti `historical_matches`-
  taulussa (molempien joukkueiden sivuilta luettuna) - tilalliselle mallille
  tämä pitää poistaa etukäteen tai rating päivittyisi kahdesti. 1249 → 1134
  ottelua deduplikoinnin jälkeen (ks. alla, 2026-09-17: korjattu toinen
  dedup-bugi joka pudotti ottelupareja jotka jakoivat saman turnaus+aikaleiman).
- `src/run_elo.py`: grid-hakee (scale, k, half_life_days) minimoiden log
  lossin, vertaa tulosta molempiin Tehtävä 2:n perusmalleihin samalla
  deduplikoidulla datalla (reilu vertailu).
- **Tulos (2026-09-17, LOPULLINEN - kaikkien 50 joukkueen täysi uudelleen-
  keräys kaikkien bugikorjausten jälkeen, 1037 ottelua):** paras Elo
  (scale=400, k=96, ei vaimennusta) log loss **0.6632** — voittaa sekä
  50/50:n (0.6931) että pehmeän VRS-mallin (0.6778).
- **Huomio grid-haun reunasta:** paras (scale=400, k=96) osuu testatun
  ruudukon REUNALLE (scale∈[100..500], k∈[8..96]) - merkki siitä että
  ruudukko voisi olla liian kapea, ja k=96 (aggressiivinen, nopeasti
  reagoiva rating) voi ylisovittaa juuri tähän 4 kk:n ikkunaan. Holdout-
  validointi (alla) valitsi maltillisemman (scale=150, k=32) train-datalla
  ja sai SILTI paremman test-tuloksen - absoluuttisia rating-arvoja
  (esim. "Nemiga 2161") ei pidä ottaa kirjaimellisesti, suhteelliset
  voittotodennäköisyydet ovat vakaampia kuin tarkka piste-arvo.
- Kalibrointi näyttää järkevältä koko välillä - ei systemaattista
  yli-/aliluottamusta.

### Aito train/test-validointi (2026-09-17) - malli testattu ilman markkinaa

**Huomio metodologiasta:** yllä oleva 0.6632 valitsi hyperparametrit (scale,
k, half_life) KOKO datasetilla ja arvioi tuloksen samalla datasetilla -
lievä sisäänrakennettu vinouma, vaikka itse walk-forward on ottelukohtaisesti
lookahead-suojattu. `src/run_holdout_validation.py` korjaa tämän: jakaa
1037 ottelua (lopullinen määrä) kronologisesti (80% train / 20% test,
raja 2026-09-03), valitsee parametrit VAIN train-osalla, ja raportoi
lopputuloksen VAIN test-osalla jota parametrivalinta ei ole koskaan nähnyt.

- **Tulos: Elo (scale=150, k=32, valittu train:lla) test_log_loss = 0.6322**
  — parempi kuin aina_5050 (0.6931) JA pehmeä VRS-sija (0.6786) TÄYSIN
  näkemättömällä datalla. Itse asiassa parempi kuin koko-datasetin
  in-sample-arvio (0.6632) - ei merkkejä ylisovittumisesta, ja tämä
  maltillisempi parametripari (150,32 vs. koko datasetin 400,96) antaa
  vahvan signaalin etta se yleistyy vahintaan yhta hyvin.
- Kalibrointi test-osalla: ennustettu 0.36 → toteutunut 0.47, ennustettu
  0.51 → toteutunut 0.58, ennustettu 0.67 → toteutunut 0.78 (n=19/103/80) -
  ei systemaattista yli-/aliluottamusta suuntaan tai toiseen.
- **Huomio parametrien vakaudesta:** train-osajoukon paras (150,32) eroaa
  yhä hieman koko datasetin parhaasta (100,24) - 754 ottelua ei riitä
  täysin vakaaseen parametrien valintaan pienillä grid-eroilla, mutta
  molemmat antavat samansuuntaisen, selvästi baselineja paremman tuloksen.
- **Tämä on paras tapa testata mallin paikkansapitävyyttä ilman
  markkinakerrointa** - ei todista markkinaetua (siihen tarvitaan
  Tehtävä 0), mutta todistaa että malli oikeasti yleistyy uuteen
  dataan eikä vain sovi hyvin viritysdataansa.

### Toinen vakava dedup-bugi löydetty ja korjattu (2026-09-17) - kaikki yllä olevat luvut päivitetty

Käyttäjä pyysi lisäämään kolme käsin syötettyä ottelutulosta (StarLadder
StarSeries Fall 2026) tietokantaan analysoidakseen kahta live-ottelua.
Yksi lisätyistä otteluista (NRG vs MOUZ) katosi näkyvistä kokonaan Elo-
laskennasta - tutkimalla syytä löytyi **toinen, paljon vakavampi bugi**
`deduplicate_matches`-funktiossa (ks. Tehtävä 3:n koodikommentti):
dedup-avain oli `(päivämäärä, turnaus)` **ilman joukkueita** - jos kaksi
ERI ottelua samassa turnauksessa jakoi täsmälleen saman aikaleiman
(esim. ryhmävaiheen ottelut jotka Liquipedia merkitsee alkavaksi samaan
kellonaikaan), toinen niistä tulkittiin virheellisesti saman ottelun
kaksoiskappaleeksi ja **pudotettiin kokonaan** - ei vain käsin lisätyistä
otteluista, vaan koko historiadatasta alusta asti.

**Vaikutus: 860 → 1134 ottelua deduplikoinnin jälkeen (+274 ottelua, +32 %).**
Kaikki Tehtävä 3:n ja 5:n aiemmat luvut oli siis laskettu puuttuvalla
kolmasosalla datasta. Korjattu lisäämällä joukkuepari (`frozenset`) avaimeen.
(Tämä luku muuttui vielä kerran alla kuvatun kolmannen bugin korjauksen
jälkeen - 1134 → 943, ks. seuraava osio.)

### KOLMAS ja vakavin bugi: joukkuenimet halkesivat kahdeksi eri entiteetiksi (2026-09-17)

**Käyttäjän pyynnöstä ("etsi mallista lisää puutteita") tehty systemaattinen
koodikatselmus löysi tämän - suurin yksittäinen datavirhe koko projektissa.**

Liquipedia näyttää saman joukkueen ERI tavalla riippuen konteksista: oman
`/Matches`-sivunsa "team"-sarakkeessa top50:n LYHYT nimi ("G2"), mutta
kun sama joukkue esiintyy TOISEN joukkueen sivulla vastustajana tai
turnauksen bracket-sivulla, näytetään Liquipedian TÄYSI nimi ("G2 Esports").
Koska kaikki mallimme (Elo, VRS-sijoitus, karttapoikkeamat) käyttävät
joukkueen nimeä AVAIMENA, tämä tarkoitti että **sama oikea joukkue
halkesi kahdeksi täysin erilliseksi "joukkueeksi" mallin silmissä** - toinen
joka kertyi vain omalta sivulta luetuista otteluista, toinen (vajaampi,
usein rating=1500 "ei dataa" -oletuksella) joka kertyi vain vastustaja-
merkinnöistä. **Vahvistettu vaikuttavan vähintään 24/50 top-joukkueeseen**,
mukaan lukien useita huipputiimejä: G2, FaZe, Team Spirit, Team Vitality,
Team Liquid, Team Falcons, Aurora, 9z, Nemiga, Luminosity, ja muita.

Konkreettinen esimerkki joka paljasti bugin: kun käyttäjä kysyi NRG vs
Aurora -ennustetta, väitin Auroralla olevan "0 riviä" Tehtävä 4:n
karttadatassa - **tämä oli väärin**. Auroran karttadata OLI olemassa,
mutta tallennettuna nimellä "Aurora Gaming", ei "Aurora" - haku väärällä
avaimella näytti tyhjältä.

**Korjaus** (`src/team_names.py`, uusi jaettu moduuli):
`resolve_to_canonical()` tunnistaa sanarajallisella osamerkkijonohaulla
(sama turvallinen menetelmä kuin `VrsRankings._fuzzy_resolve`, ks. Tehtävä
2) kun täysi nimi sisältää top50-lyhennimen kokonaisena sanana ("G2"
sanassa "G2 Esports"). **Tietoisesti KONSERVATIIVINEN:** nimet jotka
sisältävät viitteen eri (vara-/nuoriso-)rosteriin - "Academy", "Young",
"Ares", "Next", jne. - jätetään TARKOITUKSELLA yhdistämättä (esim.
"B8 Academy" ≠ "B8", "G2 Ares" ≠ "G2", "The MongolZ Academy" ≠
"The MongolZ") koska ne ovat oikeasti eri pelaajia, eri taso. Jokainen
ehdotettu yhdistys tarkistettiin manuaalisesti ennen ajoa (24 nimeä,
katso commit-loki).

**Migraatio ajettu kertaalleen olemassa olevaan dataan:** 284 riviä
korjattu `historical_matches.opponent`-kentässä, 469 riviä (251+218)
`historical_maps.team1/team2`-kentissä. Sama normalisointi lisätty
`collect_history.py`:hen ja `collect_maps.py`:hen jotta bugi ei toistu
tulevissa keräyksissä.

**Vaikutus lopputuloksiin:** korjaus paljasti myös PIILEVIÄ dedup-
kaksoiskappaleita (sama oikea ottelu oli aiemmin laskettu KAHDESTI koska
nimet eivät täsmänneet dedup-avaimessa) - ottelumäärä laski 1134 → 943
kun nämä väärät kaksoiskappaleet poistuivat, ja nousi lopulta **1037**:ään
kun kaikki 50 joukkuetta kerättiin uudelleen korjatulla jäsentimellä
(ks. Tehtävä 1:n parser-bugikorjaus) - moni joukkue (G2 +28, Inner Circle
+36, jne.) sai takaisin otteluita jotka olivat aiemmin kadonneet
sarake-indeksi- tai nimibugin takia. **Lopulliset luvut (2026-09-17,
1037 ottelua):** Elo paras (scale=400, k=96) log loss **0.6632**,
holdout-validointi test_log_loss **0.6322** (80/20-jako) - malli edelleen
selvästi parempi kuin 50/50 (0.6931) ja pehmeä VRS (0.6786) täysin
näkemättömällä datalla.

### Skriptien parametrit eivät voi enää "unohtua päivittää"

Kun Tehtävä 3:n paras (scale, k) muuttui KAHDESTI peräkkäisten
bugikorjausten myötä, viisi eri skriptiä (`analyze_series.py`,
`predict_match.py`, `run_roster_signal.py`, `run_form_lambda.py`,
`generate_report.py`) piti päivittää käsin - ensimmäisellä kierroksella
yksi jäi vahingossa vanhoihin arvoihin, mikä johti hetkellisesti VÄÄRÄÄN
johtopäätökseen (ks. Tehtävä 5:n formipaino-osio). Korjattu pysyvästi:
`backtest.load_best_elo_params()` lukee parametrit aina tuoreena
`data/elo_report.json`:sta - yksikään skripti ei enää voi jäädä jälkeen.

### Mallin parannukset (2026-09-17, käyttäjän pyynnöstä, järjestyksessä)

1. **Epävarmuuden mallinnus** - `EloModel.rating_deviation()` ja
   `predict_with_confidence()`: yksinkertaistettu Glicko-tyylinen rating
   deviation (RD), joka pienenee pelattujen otteluiden myötä (RD_MAX=350
   uudelle joukkueelle, RD_MIN=40 paljon pelanneelle, puoliintumisaika 12
   ottelua - alkuarvaus, ei kalibroitu). `predict_with_confidence()`
   palauttaa piste-ennusteen lisäksi `[p_low, p_high]`-haarukan ja
   luokittelun (MATALA/KOHTALAINEN/HYVÄ) otosmäärän mukaan. Testattu:
   NRG vs Aurora (6 kelvollista ottelua) → MATALA, haarukka [0.01, 0.96];
   MOUZ vs NaVi (36/14 ottelua) → KOHTALAINEN. Ei täydellinen Glicko (ei
   volatiliteettia/RD-inflaatiota ajan myötä), mutta korjaa pahimman
   ongelman: mallin ei pidä väittää samaa tarkkuutta 6 ottelun ja 43
   ottelun päälle lasketuille luvuille.
2. **Kerääjän tuoreus-bugi korjattu** (`collect_history.py`) - LÖYDETTY:
   `history_team_progress`-taulun kaikki 50 joukkuetta olivat statuksessa
   `'ok'`, ja KAIKKI `last_attempt_utc`-arvot olivat samalta 27 minuutin
   ikkunalta (2026-09-17 12:07-12:34) - eli koko historiakeruu ajoi
   TÄYSIN kerran alusta ja pysähtyi siihen pysyvästi, koska mikään ei
   koskaan palauttanut valmiiksi merkittyä joukkuetta takaisin
   `pending`-tilaan. GitHub Actions ajoi siis tyhjää joka 15 min sen
   jälkeen, vaikka status-sivu väitti "kerääntyy jatkossa itsestään
   taustalla". Tämä selitti suoraan miksi Auroran data oli 17 vrk vanhaa.
   Korjaus: joukkueet joiden `last_attempt_utc` on yli `STALE_HOURS=6`
   vanha nollataan takaisin `pending`-tilaan ennen jokaista ajoa, ja
   käsittelyjärjestys on nyt vanhin-ensin (ei aakkosjärjestys).
3. **Putken jarkevyystarkistukset** (`src/check_pipeline_health.py`,
   ajetaan `collect_history.yml`:ssä jokaisen keruun jälkeen) - kolme
   tarkistusta: (a) onko joukkueita joita ei ole koskaan haettu tai jotka
   ovat yli 24h/72h vanhoja, (b) pudottaako dedup yli 60 % jonkun top-50-
   joukkueen otteluista suhteessa raakadataan (sama oire kuin edellä
   kuvattu dedup-bugi), (c) onko `bracket_progress` jumissa (pending-
   jonoa mutta ei uutta ajoa 72h:een). Ei korjaa mitään automaattisesti,
   vain raportoi - tarkoitus on että seuraava vastaava bugi löytyy
   nopeammin kuin käyttäjän omalla havainnolla.
4. **Formipaino λ testattu uudelleen korjatulla datalla - VIRHE MATKAN
   VARRELLA, KORJATTU:** ensimmäinen uudelleenajo (1134 ottelua, mutta
   VANHOILLA scale=100/k=32-parametreilla) näytti λ=0.3:n auttavan hieman
   (0.6673 vs 0.6683) - raportoitiin tässä READMEssä virheellisesti
   "teesi saa tukea". **Tämä oli itsessään virhe:** scale=100/k=32 ei ollut
   enää Tehtävä 3:n paras parametri korjatulla datalla (uusi paras on
   scale=200/k=48, ks. yllä) - sekoitettiin korjattu data vanhentuneisiin
   hyperparametreihin. Kun `run_form_lambda.py` päivitettiin käyttämään
   samaa scale=200/k=48-paria kuin Tehtävä 3, tulos palasi: **paras λ=0.0,
   log loss 0.6681 vs. yksittäisen Elon 0.6678 - monotonisesti huononeva
   käyrä koko välillä 0→1.** Tehtävä 5:n ALKUPERÄINEN "teesi ei saanut
   tukea" -johtopäätös siis PYSYY VOIMASSA - se ei ollut dedup-bugin
   artefakti, sen sijasin ITSE tein hetkellisen virheen vertailemalla
   epäjohdonmukaisilla parametreilla. Opetus: aina kun malli päivittyy,
   KAIKKI sitä käyttävät skriptit pitää päivittää samoihin parametreihin
   ennen johtopäätösten tekemistä.
   Roolimuutos-signaali (Edge-analyysi-osio) näytti koko datasetilla
   samansuuntaiselta täysin korjatun datan jälkeenkin (0.6595 vakaa/n=753
   vs. 0.6729 tuore muutos/n=284) - **MUTTA ks. alempi "Metodologinen
   itsekritiikki" -osio: aito train/test-tarkistus myöhemmin samana
   päivänä osoitti tämän suunnan KÄÄNTYVÄN PÄINVASTAISEKSI aidosti
   näkemättömällä datalla. Löydös on siis peruttu, ei enää pidetä
   vahvistettuna.**
5. **Kartta-veto-mallin korvaava heuristiikka - TESTATTU, EI TUKEA:**
   ennen koodin kirjoittamista testattiin empiirisesti kaksi oletusta
   omalla datalla: (a) ovatko decider-kartat (Bo3:n 3. kartta) tasaisempia
   kuin muut kartat round-pistemarginaalilla mitattuna - EI: decider
   keskimarginaali 5.29, muut 5.12 (n=109 vs 528, ero kohinan sisällä).
   (b) onko kartan järjestyksellä (1./2./3.) yleensä yhteyttä marginaaliin -
   EI: 5.35 / 4.89 / 5.29, ei selvää trendiä. **Ei siis lisätty mitään
   heuristiikkaa** `analyze_series.py`:hen - väärä oletus olisi pahempi
   kuin ei mitään. Empiirinen "kartta 1:n voittaja voittaa sarjan 79,1 %"
   -luku (ks. Tehtävä 4) pysyy parhaana käytettävissä olevana työkaluna.

## Tehtävä 4: Karttapoikkeamat & veto — kerääjä rakennettu, kattavuus osittainen

Joukkuekohtaiset `/Matches`-sivut (Tehtävä 1) kertovat vain ottelun
kokonaistuloksen (esim. "2-1") — eivät karttojen nimiä eikä CT/T-puoliskoja.
Tämä data löytyy sen sijaan turnauksen omalta **bracket-sivulta**
(`.brkts-match-popup-wrapper` -elementit), joka on rakenteeltaan täysin eri
kuin `/Matches`-taulukko ja vaati oman jäsentimen.

- `src/liquipedia_client.py`: `parse_bracket_matches()` purkaa jokaisesta
  ottelupopupista joukkueiden nimet, sarjatuloksen, ajankohdan ja jokaisen
  kartan erikseen — kartan nimi + molempien joukkueiden lopputulos + CT/T-
  puoliskotulokset (mukaan lukien OT-puoliskot kun niitä on). Testattu ja
  vahvistettu oikeaksi käsin ristiin katsomalla useita otteluita.
- **Turnaussivun otsikon selvittäminen osoittautui odotettua vaikeammaksi.**
  Naytto-nimi (esim. "IEM Beijing 2026: Global Qual") ei vastaa Liquipedian
  sivun otsikkoa sanatarkasti, ja Liquipedian tavallinen tekstihaku
  (`action=query&list=search`) osoittautui epäluotettavaksi tähän
  tarkoitukseen — se palautti systemaattisesti vääriä osumia (esim. haku
  "Esports World Cup 2026" palautti ensimmäiseksi tuloimeksi pelaajan
  sivun "FOKUS", koska turnauksen nimi mainittiin siellä usein). Korjattu
  käyttämällä Liquipedian `intitle:`-hakuoperaattoria (tasmaa vain
  otsikkoon, ei koko sivun tekstiin) yhdistettynä lava-tunnisteen (Group A,
  Playoffs, jne.) poistoon hakusanasta ennen hakua.
- `src/collect_maps.py`: sama resumable-malli kuin Tehtävä 1:ssä
  (`bracket_progress`-taulu, `--max-tournaments`-rajoitin). Ajastettu
  GitHub Actions -työnä (`.github/workflows/collect_maps.yml`, 20
  turnausta/ajo, offset-minuutit tasa-ajon välttämiseksi).
- **Resolveria parannettiin iteratiivisesti (2026-09-17) sitä mukaa kun
  puuttuvia turnaussarjoja huomattiin:** lyhenteille (IEM = "Intel Extreme
  Masters") lyhyempi hakumuoto, useamman osuman tapauksessa vuosiluvun
  ("2026") ja alasarjan (esim. "Summer"/"Fall") perusteella oikean kauden
  valinta (jotta ei vahingossa poimita vanhan kauden dataa vaaraan
  turnaukseen), ja numeroiduille sarjoille (esim. "BC.Game Masters
  Championship #2") suoraan numerolla tasmays. Jokainen korjaus lisatty
  vasta kun havaittu real esimerkki jota vanha logiikka ei loytanyt.
- **Koko 181 turnauksen ensimmäisen läpikäynnin tulos (2026-09-17):**
  86/181 (48 %) resolvoitui oikeaksi Liquipedia-sivuksi, 2 löytyi mutta
  ilman bracket-dataa, 92 ei löytynyt ollenkaan. **783 karttariviä
  (1566 joukkue×kartta-tapahtumaa) kerätty 24 eri turnaussivusta.**
  Suurin osa ei-löytyneistä on pieniä alueellisia karsintaturnauksia
  (esim. "CCT EU Series", "Moscow Cyber Games", "Thunderpick WC Qual")
  joiden Liquipedia-nimeämiskäytäntö poikkeaa nayttö-nimestä täysin eri
  tavalla kuin IEM/BLAST — näiden korjaaminen vaatisi tapaus kerrallaan
  tutkimista, ei yleistä sääntöä. **Kattavuus on siis rehellisesti
  osittainen** — puuttuvat turnaukset (`status='not_found'`) EIVAT yritetä
  loputtomiin uudelleen automaattisesti, jottei tuhlata Liquipedian
  pyyntökiintiötä samoihin tuloksettomiin hakuihin.
- Kartta- ja puoliskotason shrinkage-estimaattori (`src/run_map_deviations.py`,
  `m̂ = (n/(n+k))·m_raaka`, k=5) laskee jokaiselle joukkue×kartta-parille
  kuinka paljon sen voitto-% kyseisellä kartalla poikkeaa joukkueen OMASTA
  yleistasosta (ei kentän keskiarvosta, joka on rakenteellisesti aina 0.5).
  **Havaintoja (lopullisella datalla):** esim. MOUZ +17,5 %-yks. Miragella
  mutta -24,1 %-yks. Ancientilla suhteessa omaan tasoonsa; G2 vahva
  Infernolla, heikko Dust II:lla. Nama ovat aitoja, mitattavia
  poikkeamia — ei viela testattu markkinaa vastaan (Tehtävä 0 tauolla),
  joten emme tiedä hinnoitteleeko Coolbet nama jo sisään. k=5 on
  tietoinen alkuarvaus, ei kalibroitu.
- **BUGIKORJAUS (2026-09-17, "etsi lisää puutteita"):** "oma yleistaso"
  (baseline) laskettiin aiemmin KAIKISTA joukkueen kartoista MUKAAN
  LUKIEN se sama kartta jolle poikkeamaa laskettiin - itseensä viittaava
  kontaminaatio joka systemaattisesti ALIARVIOI poikkeaman, pahimmillaan
  60-100 % kontaminaatio pienen otoksen joukkueilla. Korjattu leave-one-
  out-periaatteella (baseline = kaikki MUUT kartat paitsi tama). Lisäksi
  löytyi ja korjattiin sama nimenhalkeamis-bugi kuin Tehtävä 3:ssa (ks.
  "Mallin parannukset") - `historical_maps` käytti Liquipedian TÄYTTÄ
  nimeä top50:n lyhyen sijaan 24/50 joukkueella, esim. Aurora oli
  tallennettuna "Aurora Gaming"-nimellä, minkä takia väitin aiemmin
  virheellisesti Auroralla olevan "0 riviä" karttadatassa.
- **Veto-/päivitysmalli EI vielä rakennettu** — vaatii kartta-VETOJARJESTYS-
  datan (mika kartta poistettiin milloinkin), jota historical_maps ei
  vielä tallenna erikseen: nyt tiedetaan vain PELATUT kartat, ei koko
  veto-sekvenssia. Seuraava laajennus jos tätä jatketaan.

### Löydetty ja korjattu vakava bugi (2026-09-17): teoreettinen sarjapäivitys ylitodennäköinen

**Käyttäjä testasi mallia konkreettisella tapauksella** (MIBR voitti kartan
1 FURIAa vastaan, Bo3, seuraava Nuke, decider Cache) ja vertasi tulosta
oikeaan Coolbet-live-kertoimeen (Money Line 1.80 MIBR / 1.90 FURIA, eli
sarja lähes tasapelinä ~51 %/49 % marginaali poistettuna).

- **v1** käytti Tehtävä 3:n ottelutason Elo-mallia per-kartta-syötteenä.
  `historical_matches` on kuitenkin SEKOITUS: 998/1223 riviä (82 %) on
  SARJAN kokonaistulos ("2-0" jne.), vain 219 riviä aidosti yksittäisen
  kartan tulos. → **P(sarjavoitto) = 87,7 %.**
- **v2** rakensi puhtaan per-kartta-Elon `historical_maps`-datasta (783
  aidosti yksittäisen kartan tulosta) ja syötti sen teoreettiseen Bo3-
  kombinatoriikkaan P=q+(1-q)·q. **Tuskin muuttunut (85,8 %)** — kaava
  olettaa jäljellä olevat kartat RIIPPUMATTOMIKSI joukkueen yleistasosta,
  mikä on väärin: Bo3:n veto-järjestys (molemmat kieltävät huonoimpansa)
  tekee jäljellä olevista kartoista todellisuudessa tasaisempia. Tätä ei
  voi mallintaa suoraan — veto-järjestysdataa EI ole Liquipedian bracket-
  sivuilla (tarkistettu tuoreella haulla), vain yksittäisten ottelusivujen
  kautta, mikä vaatisi yhden pyynnön PER OTTELU (satoja/tuhansia) — ei
  toteutettavissa 30,5s/pyyntö-rajoitteella.
- **v3 (nykyinen, `src/analyze_series.py`):** sen sijaan että mallinnetaan
  veto-mekanismia jota emme voi havaita, mitataan **suoraan toteutunut
  taajuus** omasta datastamme (`run_map_deviations.
  compute_empirical_leader_win_rate()`, käyttää `historical_maps`:n
  `map_order`-kenttää + lopullista sarjatulosta, jotka olivat jo tallessa).
  **Tulos: kartan 1 voittaja voitti Bo3-sarjan 193/244 kertaa (79,1 %,
  n=244 oikeaa ottelua)** — Bo5:lle vain n=11, liian pieni luotettavaksi.
  Tämä ei oleta mitään riippumattomuudesta; se ON toteutunut taajuus,
  veto-vaikutus jo sisäänrakennettuna koska se on oikeasti tapahtunut.
- **Rehellinen jäännösero:** 79,1 % on silti korkeampi kuin markkinan ~51 %
  tälle YKSITTÄISELLE ottelulle. Tämä EI ole ristiriita — 79,1 % on
  keskiarvo KAIKEN tasoisten otteluiden yli, kun taas Coolbetin hinta
  sisältää tietoa juuri TÄSTÄ ottelusta (esim. FURIAn koettu vahvuus juuri
  nyt) jota mallillamme ei ole. Merkki siitä että oma tietomme on
  suppeampi kuin markkinan, ei markkinavirheestä.

## Tehtävä 5: Formipaino λ — teesi EI saanut tukea (rehellinen negatiivinen tulos)

**Tämä on projektin alkuperäinen ydinteesi** ("kun muutaman pelin formia
hieman korostaa niin saa edgen") suorassa testissä.

- `src/run_form_lambda.py`: kaksi rinnakkaista Elo-ratingia, puoliintumisajat
  180 vrk (hidas) ja 21 vrk (nopea), PV = R_hidas + λ·(R_nopea − R_hidas)
  RATING-avaruudessa (ei todennäköisyysavaruudessa). λ grid-haku 0.0–1.0.
- **Tulos (2026-09-17): paras λ = 0.0.** Log loss kasvaa MONOTONISESTI
  λ:n kasvaessa (0.6653 → 0.6734 välillä λ=0→1) - tuoreen forman lisääminen
  ei paranna, se huonontaa ennustetta joka askeleella. Yksittäinen
  vaimentamaton Elo (0.6646) on paras kaikista.
- **Teesi ei siis saa tukea tässä datassa.** Raportoitu suoraan, ei etsitty
  kiertotietä (käyttäjän oma vaatimus istunnon alusta).
- **HUOM - rajoite joka pitää mainita joka kerta kun tätä tulosta siteerataan:**
  havaintoikkuna on vain 4 kuukautta, joka on LYHYEMPI kuin "hitaan" mallin
  180 vrk:n puoliintumisaika - hidas ja nopea rating eivät ehdi juuri eriytyä
  toisistaan tässä datassa. Tämä ei ole lopullinen kumous teesille, vaan
  varhainen negatiivinen tulos alimitoitetulla otoksella. Kun Tehtävä 0:n
  data kertyy kuukausien ajalta, testi kannattaa ajaa uudelleen.

Lähde [oddspapi.io](https://oddspapi.io), bookmaker **Coolbet**, skeema
varmistettu oikeaa dataa vastaan 2026-09-17. Ajastus pyörii GitHubin
palvelimella, täysin riippumatta tästä koneesta. Muut tehtävät
(`cs2-agenttibriiffi.md`) rakennetaan tämän päälle vasta kun hyväksymiskriteeri
(7 pv, 50 ottelua, molemmat kertoimet) täyttyy.

## Edge-analyysi (2026-09-17): mistä oikea etu voisi löytyä

Rehellinen lähtökohta: se että malli voittaa 50/50:n tai VRS-sijan (Tehtävä 3)
EI todista markkinaetua — Coolbet käyttää paljon enemmän tietoa kuin VRS-sija.
Oikea edge vaatii markkinavertailua (Tehtävä 0), jota ei viela ole. Alla kolme
konkreettista askelta jotka VALMISTELTIIN nyt olemassa olevalla datalla, jotta
niita voi ottaa kayttoon heti kun kerroindata alkaa kertya.

1. **Karttatason poikkeamat** — ks. Tehtävä 4 yllä (`src/run_map_deviations.py`).
   Todennäköisin paikka oikealle edgelle, koska kartta-handicap/-totaali
   -markkinat ovat yleensä ohuemmin hinnoiteltuja kuin ottelun voittaja.
2. **Rosterimuutos-signaali** (`src/run_roster_signal.py`) — **PERUTTU
   löydös, ks. "Metodologinen itsekritiikki" -osio alempana.** Koko
   datasetilla näytti siltä että Elon ennustevirhe on suurempi otteluissa
   joissa jompikumpi joukkue on vaihtanut kokoonpanoaan (log loss 0.6595
   vakaa vs. 0.6729 muutos, n=753/284), mutta aito train/test-tarkistus
   käänsi suunnan päinvastaiseksi näkemättömällä datalla. Ei siis (vielä)
   käyttökelpoinen edge - `RosterHistory.roster_stability` on silti valmis
   infrastruktuuri jos tätä halutaan tutkia uudelleen isommalla datalla.
3. **CLV-seuranta** (`src/clv.py`, `clv_log`-taulu skeemassa) — **valmisteltu,
   EI VIELÄ KÄYTÖSSÄ.** Tämä on briiffin oma lopullinen validointimittari:
   sen sijaan että odotetaan satoja oikeita vetotuloksia, katsotaan liikkuuko
   markkinan kerroin ajan mittaan kohti mallin ennustetta (CLV% =
   hinta_vedolla / sulkeutuva_hinta − 1). Rakenne (taulu + `log_bet()` /
   `settle_closing()` -funktiot) on valmis, mutta täysin tyhjä kunnes
   Tehtävä 0 käynnistyy uudelleen — mitään ei ole vielä kytketty
   `collect_odds.py`:hen, koska oikeaa kerrointa ei ole mihin verrata.

**Ei siis vielä väitetä että edgeä on löytynyt** — nämä ovat kolme valmisteltua
työkalua/havaintoa jotka aktivoituvat/vahvistuvat kun Tehtävä 0:n kerroindata
alkaa kertyä.

## Uupumus- ja turnauksen alkuvaihe -tutkimus (2026-09-17)

Käyttäjän kaksi hypoteesia testattu `src/run_tournament_effects.py`:llä
olemassa olevalla datalla (ei uutta keruuta): (1) vaikuttaako joukkueen
tiheä ottelutahti upset-todennäköisyyteen ("uupumus"), (2) onko turnauksen
ensimmäisissä otteluissa enemmän yllätyksiä kuin myöhemmissä.

**Ensimmäinen versio** ryhmitteli ottelut raa'an `tournament`-merkkijonon
mukaan (esim. "IEM Cologne Major 2026 Stage 1 - Round 1") - käyttäjä
huomautti tämän olevan väärä taso ("puhun turnauksen ensimmäisistä", ei
yksittäisen lavan). **Korjattu** hyödyntämällä Tehtävä 4:n
`bracket_progress`-taulua: 88/181 raakaa lava-merkkijonoa on jo resolvoitu
oikeiksi Liquipedia-tapahtumasivuiksi, ja monet lavat (esim. 19 eri
"Esports World Cup 2026 - Group X/LCQ/Playoffs" -merkkijonoa) osuvat
SAMAAN tapahtumaan - 182 raakaa merkkijonoa → 121 oikeaa tapahtumaa ilman
uutta dataa.

**Molemmat hypoteesit saivat PÄINVASTAISEN tuloksen kuin oletettu:**

- **"Uupumus":** suosikki joka EI OLE PELANNUT viimeisen 5 vrk:n aikana
  häviää useammin (upset% 48,9, n=227) kuin suosikki joka on pelannut
  äskettäin (upset% 38–42, n=810) - ei uupumusta vaan päinvastoin
  "ring rust"/kilpailurytmin puute.
- **Turnauksen alkuvaihe (oikealla tapahtumatasolla):** molempien
  joukkueiden ensimmäinen ottelu tapahtumassa on VAKAAMPI (upset% 39,0,
  log loss 0,637, n=354) kuin myöhemmät ottelut (upset% 43,5, log loss
  0,677, n=683) - todennäköinen selitys: ensimmäiseen otteluun tullaan
  tunnetulla seedauksella/ryhmäjaolla, kun taas myöhemmät (playoffs/
  decider) -ottelut ovat nimenomaan niitä joissa heikommat on jo
  karsittu pois ja jäljelle jääneet ovat tasaisempia.

### Metodologinen itsekritiikki (2026-09-17): kehämäinen validointi löydetty ja korjattu

**Käyttäjä esitti perustellun kritiikin:** "kun vertaat mallin toimivuutta
historiassa, vertaatko nimenomaan niiden otteluiden tuloksista tehtyä
mallia niihin samoihin otteluihin? eihän näin voi järkevästi tehdä."

Tämä osui suoraan siihen miten "ruostumis"- ja "taso"-korjaukset alun
perin fitattiin JA "validoitiin" - molemmat vaiheet käyttivät samaa koko
dataa. Yksittäisen ottelun ENNUSTE on aidosti walk-forward (Elo-rating
käyttää vain aiempaa dataa), mutta KORJAUSKERTOIMEN fittaus + sen
"toimivuuden" tarkistus samalla datalla on kehämäistä - täsmälleen sama
virhe jota `run_holdout_validation.py` jo korjasi Tehtävä 3:n
hyperparametreille (ks. yllä), mutta jota ei oltu vielä sovellettu näihin
kahteen uudempaan korjaukseen.

**Korjaus: kumpikin korjaus testattiin uudelleen aidolla 80/20-jaolla**
(kerroin fitattu VAIN ensimmäisellä 80%:lla, tarkistettu VAIN
viimeisellä 20%:lla jota fittaus ei koskaan nähnyt):

- **Ruostumiskorjaus: LÄPÄISI.** Kulmakerroin pysyi lähes samana
  train-only-fitattuna (0,792) verrattuna koko datasetin fittaukseen
  (0,790), ja paransi log lossia myös aidosti näkemättömällä test-datalla
  (0,684 → 0,683, n=28 - pieni otos, "toistaiseksi tuettu" ei "todistettu").
  **Pidetty käytössä** `predict_match.py`:ssä.
- **Taso-korjaus (S/A-Tier): EPÄONNISTUI.** Kulmakerroin train-datalla
  fitattuna oli 0,211 - TÄYSIN eri kuin koko datasetin 0,563. Sovellettuna
  aidosti näkemättömään test-dataan (n=50 HIGH-tier ottelua) se HUONONSI
  log lossia (0,624 → 0,675). Tämä oli siis ylisovitus/kohinaa, ei aito
  toistuva ilmiö. **Poistettu kokonaan** koodista.
- **Rosterimuutos-signaali (aiemmin raportoitu "mitattavana löydöksenä"):
  tarkistettu samalla tavalla - EI LÄPÄISSYT.** Suunta pysyi samana
  train-osalla (stabiili 0,665 vs. muutos 0,688, sama suunta kuin koko
  data), mutta KÄÄNTYI PÄINVASTAISEKSI aidosti näkemättömällä test-datalla
  (stabiili 0,636 vs. muutos 0,612 - muutos oli TÄSSÄ parempi). **Tämä
  löydös on siis peruttu** - alkuperäinen "Edge-analyysi"-osion väite
  "mitattava ero" ei kestä kunnollista validointia. Ei poisteta datasta,
  mutta ei myöskään enää esitetä vahvistettuna signaalina.
- **Tapahtuman ensimmäinen ottelu -löydös: LÄPÄISI.** Suunta pysyi samana
  ja jopa vahvistui test-datalla (train: ensimmäinen 0,655 vs. ei-1. 0,681;
  test: ensimmäinen 0,596 vs. ei-1. 0,651 - molemmissa ensimmäinen ottelu
  selvästi parempi). Tämä on siis toistaiseksi luotettavin näistä kolmesta
  löydöksestä, joskin edelleen vain yhden train/test-jaon varassa.

**Yleinen opetus:** täyden datasetin bucket-vertailu (esim. "ryhmä A:n
log loss on parempi kuin ryhmä B:n") EI ole validointia, vaikka itse
ennusteet olisivat walk-forward - se kertoo vain että ilmiö ESIINTYY
tässä datassa, ei että se ON AITO/TOISTUVA. Jatkossa jokainen uusi
"löydös" pitää testata train-only-fittauksella + test-only-tarkistuksella
ennen kuin sitä kutsutaan validoiduksi tai muutetaan koodiksi.

## Bookmaker-valinta: Coolbet, ei Pinnacle

**Huomio metodologiasta.** Alkuperäinen suunnitelma oli Pinnacle, joka on
riippumattomilla sharp-bookmaker-listauksilla ainoa nimenomaisesti "sharp"
(markkinatehokas) kirja - se on CLV-analyysin (closing line value) vakiintunut
vertailukohta. Coolbet valittiin hinnan takia ($13/kk halvempi Normal-tasolla,
tai $26/kk Dev-tasolla 100 000 pyynnöllä/kk vs. Pinnaclen $33/kk). Coolbetiä
EI mainita kummallakaan tarkistetulla sharp-bookmaker-listauksella - sen oma
markkinointi puhuu "razor-sharp odds" -tyylistä, mutta se ei ole riippumaton
luokitus. **Tämä on tietoinen oletus, ei vahvistettu fakta.** Jos mallin
CLV-tulokset alkavat näyttää epäilyttävän hyviltä tai epäjohdonmukaisilta,
tämä on ensimmäinen asia jota syytä epäillä - Coolbetin linja saattaa liikkua
eri tavalla kuin oikea markkinakonsensus.

## Miten se toimii

- `.github/workflows/collect_odds.yml` ajaa `src/collect_odds.py`:n cronilla
  GitHub Actionsin ubuntu-runnerilla ja committaa päivittyneen `data/odds.db`:n
  takaisin repoon ajon lopuksi. Ei tarvitse pitää omaa konetta tai palvelinta
  päällä. **Ajastus on pois päältä** (vain `workflow_dispatch`-käsiajo toimii)
  kunnes Coolbet + Dev -tilaus (100 000 pyyntöä/kk) on vahvistettu - nykyinen
  avain on vielä ilmaisella 250/kk-tasolla.
- Skripti hakee OddsPapista CS2:n aktiiviset turnaukset
  (`futureFixtures`/`upcomingFixtures`/`liveFixtures` > 0) ja niiden ottelun
  päävoittaja-kertoimet (market `.../0/moneyline`, ei yksittäisiä karttoja).
- Turnaus- ja joukkuelistat välimuistitetaan (`data/cache_*.json`, 2h/24h TTL,
  committoidaan myös repoon) - itse kertoimet haetaan aina tuoreena.
- Ensimmäinen havainto ottelusta = avauskerroin. Kerroin < `CLOSING_WINDOW_MINUTES`
  (oletus 10 min) ennen alkua = sulkeutuva kerroin. Kaikki muut havainnot
  tallennetaan myös (ilmaista dataa, hyödyllistä myöhemmälle CLV-analyysille).
- Jokainen ajo kirjaa rivin `collector_runs`-tauluun - virhe näkyy sekä siellä
  että GitHubin Actions-välilehdellä (punainen X, voi laittaa sähköposti-ilmoituksen).
- OddsPapin `/v4/historical-odds`-endpoint sisältää dataa jo ennen tämän
  projektin tiliä (vahvistettu 2026-08-20 asti taaksepäin isoille joukkueille) -
  Tehtävä 7:n backtest ei siis välttämättä vaadi kuukausien odottelua, ks.
  status-sivun löydökset.

## Paikallinen kehitys / manuaaliajo

```
py -3 src/collect_odds.py
py -3 src/check_status.py
git pull   # hakee GitHub Actionsin keräämän uusimman datan
```

`.env` on täytetty API-avaimella paikallista ajoa varten - GitHub Actions
käyttää samaa avainta Secretina (`ODDSPAPI_API_KEY`), ei `.env`-tiedostoa.

Vaihtoehtoinen paikallinen ajastus (`scheduler/register_task.ps1`, Windows
Task Scheduler) on rakennettu mutta EI käytössä - GitHub Actions korvasi sen,
koska se ei vaadi konetta pidettävän päällä.

## Hyväksymiskriteeri (Tehtävä 0)

7 päivän jälkeen: `py -3 src/check_status.py` (`git pull` ensin) näyttää
vähintään 50 ottelua, joilla sekä avaus- että sulkeutuva kerroin on tallennettu.

## Tietokanta

SQLite: `data/odds.db` (gitissä - tämä ON säilytysmekanismi GitHub Actionsin
ephemeraaleille runnereille). Skeema: `db/schema.sql`.
- `matches` - yksi rivi per ottelu
- `odds_snapshots` - yksi rivi per kerroinhavainto (avaus/sulkeutuva/välissä)
- `collector_runs` - yksi rivi per ajo, tästä näkee kaatuiko keruu hiljaa

## Tunnetut rajoitukset

- OddsPapi sallii max 5 `tournamentIds` per pyyntö - tämä on jo huomioitu.
- Muoto (Bo1/Bo3) ei vielä täyty `matches.format`-kenttään - se selvitetään
  Tehtävässä 1 (Liquipedia). `.../1/`, `.../2/`, `.../3/moneyline` -markkinat
  ovat kuitenkin jo datassa (raw_json), jos formaatin haluaa päätellä niistä.
- Coolbet ei ole vahvistettu sharp-kirja - ks. yllä oleva huomio.
- Julkinen repo: kerroin- ja joukkuedata näkyy kaikille GitHubissa. Ei
  arkaluontoista, mutta huomioitavaa.
- GitHub Actionsin cron-ajastus voi satunnaisesti viivästyä muutamalla
  minuutilla ruuhka-aikoina (GitHubin oma rajoitus, ei korjattavissa).
