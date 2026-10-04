# CLAUDE.md – cs2voimat (CS2-voimaluku- ja vetomalli)

Tämä tiedosto on ohje jokaiselle Claude-sessiolle, joka työskentelee tässä
repossa: paikallisesti, pilvessä (claude.ai/code) tai millä tahansa koneella.
Lue se kokonaan ennen kuin teet mitään. Säännöt perustuvat oikeisiin
virheisiin, joista on seurannut haittaa. Älä ohita niitä, vaikka jokin
vaikuttaisi turhalta.

Viimeksi päivitetty 2026-09-30.

---

## 0. Käyttäjä ja viestintä

- **Kieli:** kirjoita suomeksi. Käyttäjä kirjoittaa lyhyesti ja odottaa, että
  hoidat asiat itse. Hän ei halua tehdä GitHub-toimia käsin ("sinä teet nyt asiat").
- **Tuuraajat:** oletuksena molemmat joukkueet pelaavat normaalilla
  kokoonpanolla. ÄLÄ kysy tuuraajista vetoarvioiden yhteydessä, sillä käyttäjä
  kertoo itse, jos tuuraajia on.
- **Taulukot:** yksi selkeä luku per sarake. Älä lisää sulkeisiin
  vaihtoehtoisia lukuja (esim. "ilman korjausta"), koska se sekoittaa.
- **Rehellisyys:** älä väitä mitään tarkistamatta. Esimerkiksi väite "mallia ei
  ole testattu kertoimia vastaan" oli väärä (ks. kohta 5). Jos et tiedä,
  tarkista koodista tai datasta ensin.
- **Johdonmukaisuus:** jos malli sanoo +EV, sano se suoraan. Erottele
  selvästi, mitä malli sanoo ja mitä itse arvioit. Älä jätä mallin +EV-vetoja
  mainitsematta.
- **Päätösvalta:** käyttäjä päättää, mitä otetaan tuotantoon. Anna suositus
  perusteluineen ja toteuta sitten käyttäjän valinta.

---

## 1. EHDOTON: Liquipediaan vain GitHub Actionsista

- Liquipediaan (liquipedia.net) EI SAA lähettää yhtään pyyntöä muualta kuin
  GitHub Actions -CI:stä. Kielletty on mikä tahansa kerääjäskriptin ajo,
  selaimen sivunlataus tai "onko esto poistunut" -tarkistus, millä tahansa
  koneella, myös pilvisessä Claude-sessiossa.
- **Syy:** käyttäjän kotiliittymän IP sai syyskuussa 2026 Cloudflare-eston,
  koska keräimiä ajettiin paikallisesti rinnakkain, uudelleenyrityksiä tuli
  liikaa ja HTML-sivuja ladattiin suoraan. Liquipedian ehdot kieltävät
  automaattisen pääsyn muihin kuin API-osoitteisiin, ja toistuvat estot voivat
  muuttua pysyviksi.
- `src/liquipedia_client.py` kieltäytyy toimimasta, ellei `GITHUB_ACTIONS=true`.
  Ohitusta `ALLOW_LOCAL_LIQUIPEDIA=1` ei saa käyttää ilman käyttäjän
  nimenomaista lupaa.
- Keräyksen tilan tarkistus tehdään AINA repon datasta (ks. kohta 3), ei
  koskaan Liquipediasta.
- Ei välityspalvelimia, IP-kiertoa tai muuta estojen kiertämistä.
- Kertoimia EI saa kaapia (OddsPortal ym. kieltävät sen ehdoissaan).
  `src/collect_market_snapshot.py` on poistettu käytöstä juuri tästä syystä.
- GitHubin tallennettuja tunnuksia (esim. Windows Credential Managerissa) ei
  kaiveta esiin muuhun käyttöön kuin siihen, mihin git niitä itse käyttää.

---

## 2. Arkkitehtuuri lyhyesti

- **Tietokanta:** `data/odds.db` (SQLite) on repossa. CI committaa sen jokaisen
  ajon jälkeen, joten se on AINA ajan tasalla vain `git pull`in jälkeen.
- **Keruu, GitHub Actions** (`.github/workflows/`):
  - `collect_core.yml`: historia (`src/collect_history.py`), kartat
    (`src/collect_maps.py`), terveystarkistus ja commit. Käynnistyy cronilla
    (käytännössä vain muutaman tunnin välein), pushista keräinkoodiin ja
    `workflow_dispatch`illa (valinnainen `teams`-syöte). Jatkaa itseään, jos
    karttajonoa jää.
  - `collect_rosters.yml` ja `collect_roster_transitions.yml`: kokoonpanot.
  - Kaikilla on sama concurrency-ryhmä `cs2-core-collectors`, joten keräimet
    eivät koskaan aja rinnakkain.
- **Tärkeimmät taulut:**
  - `historical_matches`: sarjatulokset kummankin joukkueen sivulta, eli
    peiliriveinä.
  - `historical_maps`: karttatulokset puoliskoineen turnauskaavioista.
  - `history_team_progress`: joukkuekohtainen keruutila
    (pending/ok/error/not_found).
  - `bracket_progress`: turnauskohtainen karttakeruun tila.
  - `team_rosters`: liittymis- ja lähtöpäivät. Roolikenttää ei ole, joten
    mukana on myös valmentajia.
  - `roster_transitions`: kokoonpanosiirrot ilman päivämääriä.
- **Seuratut joukkueet:** `data/top50_teams.json` (nykyään 75 joukkuetta).
  Nimet normalisoidaan kanoniseen muotoon funktiolla
  `src/team_names.py::resolve_to_canonical`. Esimerkiksi "Magic" → `magic`
  (pienellä), "G2 Esports" → `G2`.

---

## 3. Datan tilan tarkistus ("onko kaikki pelit datassa?")

Tee aina tässä järjestyksessä:

1. `git pull`
2. Lue `data/pipeline_health.txt`. CI kirjoittaa sen jokaisella ajolla, ja
   kaikki `[VIRHE]`-rivit pitää selvittää.
3. Tarkista tietokannasta:
   - `SELECT status, COUNT(*) FROM history_team_progress GROUP BY status`.
     `not_found` ja `error` ovat hälytysmerkkejä.
   - `MAX(match_date_utc)` ja `MAX(collected_utc)` taulusta `historical_matches`.
   - `bracket_progress`-tilajakauma.
   - Tuplat: `from match_dedup import find_duplicates`. Tuloksen pitää olla 0.
4. Katso `git log --oneline -5`, josta näkyy CI:n viimeisimmät commitit.

Aiemmin löydetyt hiljaiset viat, joita kannattaa epäillä ensin:

- **'error'-tilaa ei yritetty uudelleen** (09-24–09-28): putki ajoi tyhjää
  neljä päivää. Korjattu: uusi yritys tunnin päästä.
- **429-virheen jälkeen 'not_found' ilman uusintaa** (09-18–09-30): seitsemän
  joukkuetta jäi jumiin. Korjattu: uusi yritys 24 tunnin välein, ja
  `TEAM_ALIASES` sisältää oikeat sivunimet.
- **Terveystarkistus oli vain CI-lokissa**, eikä kukaan huomannut sitä.
  Korjattu: tulos tallennetaan tiedostoon `data/pipeline_health.txt`.

Kun keräin "ajaa mutta ei kerää mitään", katso ensin progress-taulujen
tilajakauma.

---

## 4. EHDOTON: ei tuplaotteluita

- Sama ottelu ei saa esiintyä saman joukkueen näkökulmasta kahdesti.
  Syyskuussa tupla (Liquipedia siirsi GL–magic-ottelun aloitusajan 18:00 →
  18:10) nosti vetoarviota noin neljä prosenttiyksikköä väärään suuntaan.
- **Suojaukset:**
  - `src/match_dedup.py`: keräin poistaa siirretyn ajan, muuttuneen nimen ja
    käsin syötetyt vanhentuneet rivit sekä hakuikkunan ulkopuoliset
    nimivarianttituplat.
  - `backtest.deduplicate_matches`: latausvaiheen turvaverkko (sama pari, sama
    tulos, enintään 4 tunnin väli).
  - `check_pipeline_health.py`: merkitsee jäljelle jääneet tuplat virheiksi.
- **ÄLÄ lisää ottelurivejä tietokantaan käsin.** Jos tulos puuttuu vielä
  (CI ei ole ehtinyt kerätä sitä), anna se ennusteelle muistissa:
  `--extra "Joukkue A;Joukkue B;2-0;2026-09-28T11:00"` (UTC). Sitä ei
  tallenneta, ja dedup pudottaa sen, jos sama ottelu on jo datassa.
- **Pysyvä vaihtoehto `--extra`-riveille (2.10. alkaen):** kirjaa käyttäjän
  antamat tuoreet tulokset (esim. HLTV-kuvat) tiedostoon
  `data/manual_results.json`. `predict_match` lukee ne automaattisesti
  (`src/manual_results.py`) vain muistiin ja ohittaa rivin vain, jos datassa
  on jo SAMA ottelu: sama pari, sama tulos ja enintään 24 tunnin ero. Saman
  parin uusintaottelu eri tuloksella (esim. Bo1-lohko 13–9 ja Bo3-pudotuspeli
  2–1) säilyy. Kun CI kerää ottelun, rivi putoaa pois itsestään. Ohita
  tiedosto valinnalla `--no-manual`.
- **ÄLÄ muokkaa `data/odds.db`:tä paikallisesti ja committaa sitä.** Se on
  binääri, jonka CI omistaa, joten konflikti heittää dataa pois. Testaa
  muutokset aina tietokannan kopiolla, esim. scratch-hakemistossa.
- Jos git-konflikti koskee `odds.db`:tä ja progress-JSONia, ota molemmat
  SAMASTA lähteestä. Älä koskaan yhdistele tiedostoja eri puolilta.
- Windows PowerShell: `git show ...:data/odds.db > tiedosto` korruptoi
  binäärin. Käytä `cmd /c "..."` tai Git Bashia.

---

## 5. Tuotantomalli ja ennusteet

Pääkomento:

```
python src/predict_match.py "Joukkue A" "Joukkue B" --bo 3 [--online] [--extra "..."]
```

(Windowsilla `py -3`. Pilvessä ja Macilla `python3`.)

- **`--bo 1/3/5`:** anna AINA oikea sarjan pituus. Oletus on 3.
- **`--online`:** online-ottelu. Ennuste kutistetaan kohti 0.5:tä
  (`ONLINE_SHRINK = 0.3`), ja arvovetoja annetaan vain tasaisiin otteluihin
  (ks. korjaukset alla).
- **Nimet:** kirjoitusasu normalisoidaan automaattisesti.

**Päämalli (tuotannossa 2026-09-28 alkaen, käyttäjän päätös):**
karttatason Elo (`src/run_format_map_model.py::map_elo_series_prob`,
`PROD_MAP_K=96`, scale 400). Rating päivittyy jokaisesta kartasta, ja
sarjaennuste lasketaan sarjan pituuden mukaan (`series_win_prob`) ja
kalibroidaan (tau). Haarukka lasketaan karkeasti RD-menetelmällä.
Vertailurivinä näytetään vanha sarjatason Elo.

- **Näyttö:** rolling origin, viisi ikkunaa. Log loss 0.6825 vs 0.6857
  (vanha), paras 4/5 ikkunassa, P(parannus > 0) = 0.85. Heikompi Bo1-otteluissa
  (n = 130), joten Bo1-varoitus näytetään.
- **Uudelleentesti noin 12.–19.10.2026:** aja `python src/run_format_map_model.py`
  ja ehdota paluuta vanhaan malliin, jos etu on kadonnut.

**Ennusteen korjaukset** (`backtest.apply_all_adjustments`), kaikki vetävät
ennustetta kohti 0.5:tä:

- **Online (muutettu 2.10., käyttäjän päätös):** `ONLINE_SHRINK = 0.3`, eli
  ennuste kutistetaan 70 % kohti 0.5:tä. Aiemmin kutistettiin kokonaan
  0.5:een. Painotus rating-päivityksessä on 0.2. Peruste: rolling origin
  1.10. (`src/run_online_rolling.py`, tuotantomalli, 268 testiottelua),
  log loss 0.6892 vs 50/50 0.6931; signaali on heikko. **Sääntö:**
  online-arvovetoja annetaan vain tasaisiin otteluihin. Jos raaka suosikki
  on yli 0.65 (`ONLINE_EVEN_MAX`), `predict_match` varoittaa eikä EV:tä
  uskota.
- **Ruostuminen:** jos suosikilla ei ole otteluita viiteen vuorokauteen,
  kerroin 0.79. Pieni otos (n = 28), ei testattu uudelleen rolling originilla.
- **Väsymys:** jos suosikilla on vähintään kaksi ottelua 24 tunnin sisällä ja
  enemmän kuin altavastaajalla, kerroin 0.6. Rolling origin -uusintatestissä
  suunta oli oikea, mutta suuruus epävarma. **Sovittu sääntö:** jos etu syntyy
  vain väsymyskorjauksen ansiosta, sitä ei kutsuta arvovedoksi.
- **Viitehetki:** ruostuminen ja väsymys lasketaan datan viimeisimmän ottelun
  hetkestä, ei tulevan ottelun ajasta. Huomioi tämä, jos ottelu on päivien
  päästä.
- **Muut painotukset:** top75-listan ulkopuoliset vastustajat vaikuttavat
  päivityksessä puolella painolla.

**Testattu ja HYLÄTTY** (älä ehdota uudelleen ilman uutta dataa tai uutta ideaa):

- xR / odotetut kierrokset sarjaennusteeseen, myös puolikohtaiset ratingit,
  gamma ja sekoitus (`run_xr*.py`).
- Kokoonpanomuutoksia huomioiva Elo: kutistus, ratingin regressio ja K-lisä
  (`run_roster_aware.py`). Syyskuun "romahdus" oli pääosin kohinaa.
- Kierrosero karttapäivityksessä ja joukkuekohtaiset karttavahvuudet
  (`run_format_map_model.py`: MR ja D).
- Tasokorjaus (LEVEA), taso-/S-tier-korjaus, karttapoolin kutistus ja
  formipainotus.
- **Karttatasoitukset ±1.5:** malli on huonompi kuin pelkkä keskiarvo (sen
  selvimmät suosikit voittivat 2–0 vain 30.5 % kerroista, kun malli odotti
  43 %). ÄLÄ anna tasoitusmarkkinoista arvovetoja.
- **Totaalit ja jatkoaika:** ei informaatiota.

**Validointiperiaatteet** (käyttäjän vaatimus, ei poikkeuksia):

- Parametrit sovitetaan VAIN train-datalla, ja arvio tehdään näkemättömällä
  datalla. Ei kehäpäätelmiä.
- Käytä rolling originia (viisi ikkunaa, 50/60/70/80/90 %). Yksi jako on
  johtanut harhaan useasti.
- Vertailukohtana kalibroitu perusmalli, bootstrap-luottamusväli ja
  ikkunakohtaiset voitot.
- Kohinatason parannusta ei oteta käyttöön pelkästään siksi, että se "ei
  haittaa". Raportoi rehellisesti myös kielteiset tulokset.

---

## 6. Vetoarviot

**Työnkulku, kun käyttäjä lähettää kuvakaappauksen kertoimista:**

1. `git pull` ja tarkista, että joukkueiden tuoreet ottelut ovat datassa.
   Katso myös keskinäiset ottelut ja mahdolliset tuplat.
2. Selvitä sarjan pituus ja LAN vs online JOKAISELLE OTTELULLE ERIKSEEN.
   Käytä turnauksen saman vaiheen rivejä `historical_matches`-taulussa:
   `match_type` ja tuloksen suuruus (2 = Bo3, 3 = Bo5, 13+ = Bo1). **Lohkon
   formaatti ei kerro pudotuspelien formaattia.** 2.10. illan Journey-
   pudotuspelit ajettiin virheellisesti Bo1:nä, vaikka ne olivat Bo3:ia.
   Vihjeitä ovat otteluiden määrä ja porrastetut alkuajat. **Jos sarjan
   pituutta ei voi varmistaa, kysy käyttäjältä ennen analyysiä. Älä arvaa.**
3. Aja `predict_match.py` jokaiselle ottelulle.
4. Laske:
   - Odotusarvo = P(malli) × kerroin − 1.
   - Markkinan todennäköisyys marginaali poistettuna:
     (1/k1) / (1/k1 + 1/k2).
5. Esitä taulukko: veto, kerroin, malli, markkina ja mallin odotusarvo. Kerro
   selvästi kaikki mallin +EV-vedot ja erikseen omat varauksesi.
6. **Kirjaa AINA kaikki kuvakaappauksen Money Line -kertoimet molemmilta
   puolilta** tiedostoon `data/odds_log.json` (myös ne, joista ei tule vetoa),
   ja tee commit ja push. Kentät: logged_utc, match_date_utc (UTC; kuvien
   ajat ovat Suomen aikaa), tournament, team, opponent, price,
   opponent_price, source (`user_query` tai `user_bet`) ja note. Kerrointestit
   (`backtest_vs_real_odds.py` ja `_map.py`) lukevat lokin `src/odds_log.py`:n
   kautta ja ottavat ottelun mukaan, kun CI on kerännyt sen tuloksen.

**Mitä tiedetään mallista kertoimia vastaan:**

- **`src/backtest_vs_real_odds.py`:** 77 oikeaa kerrointa käyttäjän
  kuvakaappauksista (elokuu–13.9.), vanha sarjatason Elo. Mallin
  +EV-valinnat tuottivat +11.7 yksikköä, eli +15 % per veto.
  - Todennäköisyys näin hyvään tulokseen ilman etua on noin 10 %.
  - Myös suurten erojen ryhmä (mallin odotusarvo yli 30 %) oli plussalla,
    +18 %.
  - Isoja eroja markkinaan EI siis pidä automaattisesti leimata mallin
    virheeksi. Näyttö on kuitenkin vasta suuntaa antava, ei lopullinen.
- **Karttatason Elo samoilla kertoimilla** (`src/backtest_vs_real_odds_map.py`,
  30.9.): +EV-valinnat 83 vetoa, +1.0 yksikköä (+1 % per veto), kun vanhalla
  +11.7. Kun käyttäjän kuusi lyötyä vetoa (18.–28.9.) lisättiin lokista:
  vanha +8.1 (83 vetoa), kartta −2.6 (89 vetoa). Log loss 0.6888 vs vanha 0.6824 (n = 83, P(kartta parempi) = 0.31).
  Ero on kohinatasoa, eikä karttamalli näytä kertoimilla etua. Markkina
  (log loss 0.6594) on selvästi tarkempi kuin kumpikaan malli. Otetaan
  huomioon uudelleentestissä 12.–19.10.
- **EV-raja** (`src/run_ev_threshold.py`, 2.10.): datasta ei löydy selvää
  rajaa (noin 80 vetoa, 95 %:n välit noin ±40 %). Karttamallilla alle 10 %:n
  EV-vedot hävisivät (n = 26, −41 % per veto). Koska malli on markkinaa
  epätarkempi, alle 10 %:n etu on mallin virhemarginaalin sisällä. Ajetaan
  uudelleen, kun kerroinlokiin on kertynyt tuloksia.
- **Suositeltu panostus:** pienet ja tasaiset panokset kaikkiin mallin
  +EV-vetoihin. Älä anna henkilökohtaisia sijoitusneuvoja.

**Kirjaus:** käyttäjän lyömät vedot kirjataan tiedostoon `data/user_bets.json`
samaan muotoon kuin aiemmat: placed_utc, match_date_utc, tournament, team,
opponent, bet_on, price, stake_eur, potential_win_eur, model_p_bet_on,
model_ev, model_confidence, status, actual_winner, profit_eur ja note.
Tulokset päivitetään datasta tai käyttäjän kuvakaappauksesta, ja lopuksi
tehdään commit ja push.

Tilanne 2026-10-04 ilta: 22 ratkennutta vetoa, seitsemän voittoa ja 15
tappiota, yhteensä −109.00 €. Avoinna Sangal (vs Eternal Fire, Journey 3.10.):
tulos puuttuu.

---

## 7. Avoimet asiat (2026-09-30)

- **Jumissa olleet joukkueet:** CI 30.9. haki Imperialin (87 ottelua), mutta
  kuudelle (BBL, Color, Butterfly, ASTRAL, ex-Zero Tenacity,
  THUNDERdOWNUNDER) ei löydy `/Matches`-sivua. Niiden ottelut top75-joukkueita
  vastaan ovat datassa. Keräin kirjaa nyt kokeillut sivunimet
  `history_team_progress.error`-kenttään (uusi yritys 24 h välein). Lue ne ja
  korjaa `TEAM_ALIASES`, jos oikea sivunimi selviää.
- **Nimivirhe korjattu 4.10.:** 1win esiintyy Liquipediassa nimellä
  "1w Team", joten mallilla oli 1winille 0 ottelua (46 korjauksen jälkeen).
  Korjauksena `team_names.EXPLICIT_ALIASES` ja `TEAM_ALIASES`. Tarkista
  samanlaiset tapaukset: joukkueet, joilla on 0 omaa riviä (WW, Fluxo).
- **Karttatason Elo kertoimia vastaan:** tehty 30.9. (ks. kohta 6), ei etua
  vanhaan. **Käyttäjän päätös 30.9.:** karttamalli pysyy tuotannossa, koska
  rolling origin perustuu isompaan dataan (702 sarjaa vs 83 ottelua).
  Arvioidaan uudelleen 12.–19.10.
- **Mallin uudelleentesti noin 12.–19.10.:** aja `run_format_map_model.py` (ks.
  kohta 5).
- **Käyttäjän harkitsemat vedot (eivät vielä lyötyjä):** ESL Pro League S24 3.10. (8 ottelua). Kaikki kertoimet
  molemmilta puolilta ovat tiedostossa `data/odds_log.json`. Aiemmin mainitut
  +EV:t (LAN): TYLOO @ 5.30, M80 @ 4.80, Legacy, BetBoom, G2 @ 1.72 ja ShindeN.
  Stake Ranked 1.10.: Nemiga, Alliance ja fnatic lyötiin (ks. `user_bets.json`). Legacyn, BetBoomin, Nemigan ja fnaticin
  kertoimet olivat 30.9. illan kuvassa korkeammat kuin aiemmin kirjatut.
- **Tier-2/3-otteluiden (esim. CS 2. Journey 2.10.) kertoimet ovat lokissa.**
  Mallilla ei ole näistä käytännössä tietoa: top75-listan ulkopuolisten
  joukkueiden rating perustuu vain otteluihin top75-joukkueita vastaan, ja
  ennuste jää lähelle 50/50:tä. Silloin kaikki altavastaajat näyttävät
  +EV:ltä. Lokin tulokset kertovat myöhemmin, pitääkö tämä paikkansa.
- **Paras seuraava datalähde karttojen ja karttavalintojen tutkimiseen:**
  yksittäisten ottelusivujen veto-järjestys ("1. X removed Map ..."). Keräintä
  ei ole rakennettu. Se tehdään vain CI:n kautta ja API-ehtoja noudattaen.
- **Kertoimet:** kertoimia ei kerätä aktiivisesti. Laillinen, maksullinen
  API olisi ainoa hyväksyttävä tapa kerätä niitä.

---

## 8. Git ja CI

- Tee aina `git pull --rebase` ennen työtä ja ennen pushia, koska CI
  committaa tietokantaa jatkuvasti.
- **Commit-viestit:** suomeksi. Tiedostoja ei saa pakottaa päälle
  (force push) ilman käyttäjän lupaa.
- **Keräinkoodin push käynnistää CI-ajon heti** (`paths`-suodatin
  `collect_core.yml`:ssä). Bottien omat data-commitit eivät käynnistä sitä,
  joten silmukkaa ei synny.
- **GitHubin cron on epäluotettava:** ajot käynnistyvät käytännössä muutaman
  tunnin välein. Tuoreet tulokset voivat siksi puuttua tunteja, ja silloin
  käytetään `--extra`-valintaa.
- **Repo on julkinen:** älä committaa salaisuuksia, tunnuksia tai
  henkilötietoja.
