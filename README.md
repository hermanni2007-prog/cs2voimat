# CS2-pelivoimat

Tehtävä 0 (kertoimien keruu) on rakennettu ja testattu oikeaa dataa vastaan
2026-09-17: lähde on [oddspapi.io](https://oddspapi.io) (Pinnacle), skeema
varmistettu, 23 ottelua tallennettu, avaus- ja sulkeutuva kerroin toimivat.
Muut tehtävät (`cs2-agenttibriiffi.md`) rakennetaan tämän päälle vasta kun
tämän hyväksymiskriteeri (7 pv, 50 ottelua, molemmat kertoimet) täyttyy.

## Miten se toimii

- `src/collect_odds.py` hakee OddsPapista CS2:n aktiiviset turnaukset
  (`futureFixtures`/`upcomingFixtures`/`liveFixtures` > 0) ja niiden ottelun
  päävoittaja-kertoimet (market `.../0/moneyline`, ei yksittäisiä karttoja).
- Turnaus- ja joukkuelistat välimuistitetaan (`data/cache_*.json`, 2h/24h TTL)
  - itse kertoimet haetaan aina tuoreena.
- Ensimmäinen havainto ottelusta = avauskerroin. Kerroin < `CLOSING_WINDOW_MINUTES`
  (oletus 25 min) ennen alkua = sulkeutuva kerroin. Kaikki muut havainnot
  tallennetaan myös (ilmaista dataa, hyödyllistä myöhemmälle CLV-analyysille).
- Jokainen ajo kirjaa rivin `collector_runs`-tauluun ja `logs/collector.log`-
  tiedostoon - virhe EI jää huomaamatta hiljaisena.

## Käyttöönotto

1. `.env` on jo täytetty API-avaimella.
2. Aja käsin ja tarkista tila:
   ```
   py -3 src/collect_odds.py
   py -3 src/check_status.py
   ```
3. Ajastus 15 min välein (ei ole vielä rekisteröity - vaatii oman hyväksyntäsi,
   koska luo pysyvän Windows-ajastetun tehtävän):
   ```
   powershell -ExecutionPolicy Bypass -File .\scheduler\register_task.ps1
   ```
   Poisto: `.\scheduler\unregister_task.ps1`. Ajastus vaatii että kone on päällä
   (ei unessa) niinä hetkinä kun tehtävän pitäisi ajaa.

## Hyväksymiskriteeri (Tehtävä 0)

7 päivän jälkeen: `py -3 src/check_status.py` näyttää vähintään 50 ottelua,
joilla sekä avaus- että sulkeutuva kerroin on tallennettu.

## Tietokanta

SQLite: `data/odds.db` (ei gitissä). Skeema: `db/schema.sql`.
- `matches` - yksi rivi per ottelu
- `odds_snapshots` - yksi rivi per kerroinhavainto (avaus/sulkeutuva/välissä)
- `collector_runs` - yksi rivi per ajastettu ajo, tästä näkee kaatuiko keruu hiljaa

## Lokit

`logs/collector.log` - jokainen ajo, virheet mukaan lukien.

## Tunnetut rajoitukset

- OddsPapi sallii max 5 `tournamentIds` per pyyntö - tämä on jo huomioitu.
- Muoto (Bo1/Bo3) ei vielä täyty `matches.format`-kenttään - se selvitetään
  Tehtävässä 1 (Liquipedia). `.../1/`, `.../2/`, `.../3/moneyline` -markkinat
  ovat kuitenkin jo datassa (raw_json), jos formaatin haluaa päätellä niistä.
- Vain Pinnacle kerätään toistaiseksi (`ODDSPAPI_BOOKMAKERS` .env:ssä) -
  useamman kirjan lisääminen on yhden env-muutoksen päässä.
