# CS2-pelivoimat

Tehtävä 0 (kertoimien keruu) on **käynnissä tuotannossa** GitHub Actionsissa:
https://github.com/hermanni2007-prog/cs2voimat

Lähde [oddspapi.io](https://oddspapi.io) (Pinnacle), skeema varmistettu oikeaa
dataa vastaan 2026-09-17. Ajastus pyörii GitHubin palvelimella 20 min välein,
täysin riippumatta tästä koneesta. Muut tehtävät (`cs2-agenttibriiffi.md`)
rakennetaan tämän päälle vasta kun hyväksymiskriteeri (7 pv, 50 ottelua,
molemmat kertoimet) täyttyy.

## Miten se toimii

- `.github/workflows/collect_odds.yml` ajaa `src/collect_odds.py`:n cronilla
  (`*/20 * * * *`) GitHub Actionsin ubuntu-runnerilla ja committaa päivittyneen
  `data/odds.db`:n takaisin repoon ajon lopuksi. Ei tarvitse pitää omaa konetta
  tai palvelinta päällä.
- Skripti hakee OddsPapista CS2:n aktiiviset turnaukset
  (`futureFixtures`/`upcomingFixtures`/`liveFixtures` > 0) ja niiden ottelun
  päävoittaja-kertoimet (market `.../0/moneyline`, ei yksittäisiä karttoja).
- Turnaus- ja joukkuelistat välimuistitetaan (`data/cache_*.json`, 2h/24h TTL,
  committoidaan myös repoon) - itse kertoimet haetaan aina tuoreena.
- Ensimmäinen havainto ottelusta = avauskerroin. Kerroin < `CLOSING_WINDOW_MINUTES`
  (oletus 25 min, `.github/workflows/collect_odds.yml`:ssä) ennen alkua =
  sulkeutuva kerroin. Kaikki muut havainnot tallennetaan myös (ilmaista dataa,
  hyödyllistä myöhemmälle CLV-analyysille).
- Jokainen ajo kirjaa rivin `collector_runs`-tauluun - virhe näkyy sekä siellä
  että GitHubin Actions-välilehdellä (punainen X, voi laittaa sähköposti-ilmoituksen).

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
- Vain Pinnacle kerätään toistaiseksi (`ODDSPAPI_BOOKMAKERS` workflow-tiedostossa) -
  useamman kirjan lisääminen on yhden rivin päässä.
- Julkinen repo: kerroin- ja joukkuedata näkyy kaikille GitHubissa. Ei
  arkaluontoista, mutta huomioitavaa.
- GitHub Actionsin cron-ajastus voi satunnaisesti viivästyä muutamalla
  minuutilla ruuhka-aikoina (GitHubin oma rajoitus, ei korjattavissa).
