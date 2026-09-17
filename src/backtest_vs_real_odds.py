"""
Vertaa mallin AIDOSTI WALK-FORWARD-ennustetta kayttajan omiin, oikeisiin
vetoihin (FISSURE Playground #3, syyskuu 2026). Tama on ainoa oikea
markkinavertailu jota tassa projektissa on saatu tehtya - Tehtava 0 on
tauolla eika laillista historia-arkistoa loytynyt (ks. README), mutta
kayttajan omat vedot OVAT oikeaa markkinadataa.

TARKEA METODOLOGINEN VAATIMUS (2026-09-17, kayttajan oman kritiikin
jalkeen tasta samasta sessiosta): jokainen ennuste kayttaa VAIN dataa
joka oli olemassa ENNEN kyseisen ottelun aikaleimaa - ei koko historiaa,
ei tulevia otteluita samasta turnauksesta. Tama on sama walk-forward-
periaate kuin run_elo_walkforward:ssa, mutta tassa ajettuna VALIKOITUJEN
otteluiden kohdalla eika koko datasetin lapi.

"Gamers2" kayttajan kuvakaappauksissa on sama kuin "G2" top50-datassa
(kayttajan vahvistus 2026-09-17, todennakoisesti bookmakerin oma
lyhenne-/kaannosvalinta joukkueen nimelle)."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from backtest import (  # noqa: E402
    EloModel,
    deduplicate_matches,
    load_clean_matches,
    load_best_elo_params,
    remove_margin,
)

# Oletettu kokonaismarginaali (molempien puolien implisiittiset tn:t
# yhteensa) VASTAPUOLEN kertoimen ARVIOINTIA varten kun oma puoli on
# EV<=0. Kayttajan maaritys 2026-09-18: 50/50-tilanteessa molemmat
# puolet hinnoitellaan 1.90 - eli margin = 2/1.90 = 1.0526 (~5.26%).
# Ristiin tarkistettu oikealla kahden puolen esimerkilla (Legacy vs G2,
# 13.9.2026: 2.020/1.806 -> implisiittinen marginaali 1.0487, hyvin
# lahella tata oletusta) - jatetaan silti nimenomaiseksi ARVIOKSI koska
# yksi havainto ei riita kalibroimaan margin tarkasti kaikille hinnoille.
ASSUMED_MARGIN = 2 / 1.90

# Kayttajan pyynnosta 2026-09-18: suodatetaan pois vedot joissa jommalla-
# kummalla joukkueella on liian vahan otteluhistoriaa taustalla ratingin
# tueksi (esim. 5star eSports = 1 ottelu, DENDELE = 0 ottelua). Raja on
# karkea nyrkkisaanto, ei tieteellisesti kalibroitu - tarkoitus on vain
# erottaa "mallilla on jotain jarkevaa nahtavana" -tapaukset "mallin
# arvio on kaytannossa arvaus" -tapauksista.
MIN_GAMES_FOR_RELIABLE = 10

# (pvm, team, opponent, kayttajan_kerroin_team:lle, veto_kohde, oikea_tulos)
# kerroin on aina TEAM-sarakkeen (ensimmainen nimetty joukkue) kertoimena
# jos veto oli suoraan tallä joukkueella; jos veto oli TOISELLA joukkueella,
# se on merkitty erikseen.
BETS = [
    {"date": "2026-09-09T09:00:00+00:00", "team": "Legacy", "opponent": "Alliance",
     "bet_on": "Legacy", "price": 1.90, "actual_winner": "Legacy", "bet_type": "2:0 (oikea tulos)"},
    {"date": "2026-09-10T03:15:00+00:00", "team": "Alliance", "opponent": "TYLOO",
     "bet_on": "TYLOO", "price": 1.62, "actual_winner": "Alliance", "bet_type": "voittaja"},
    {"date": "2026-09-10T09:10:00+00:00", "team": "Legacy", "opponent": "FURIA",
     "bet_on": "Legacy", "price": 2.44, "actual_winner": "Legacy", "bet_type": "voittaja"},
    {"date": "2026-09-10T10:40:00+00:00", "team": "MIBR", "opponent": "BETBOOM",
     "bet_on": "BETBOOM", "price": 2.75, "actual_winner": "BETBOOM", "bet_type": "0:2 (oikea tulos)"},
    {"date": "2026-09-11T06:00:00+00:00", "team": "MIBR", "opponent": "Alliance",
     "bet_on": "MIBR", "price": 1.80, "actual_winner": "MIBR", "bet_type": "voittaja"},
    {"date": "2026-09-12T06:15:00+00:00", "team": "Legacy", "opponent": "MIBR",
     "bet_on": "Legacy", "price": 2.15, "actual_winner": "Legacy", "bet_type": "2:0 (oikea tulos, toteutui 2:1)"},
    # "Gamers2" kayttajan kuvakaappauksissa = G2 Esports (kayttajan oma
    # vahvistus 2026-09-17) - naille loytyi data top50:sta "G2"-nimella.
    {"date": "2026-09-11T09:15:00+00:00", "team": "FURIA", "opponent": "G2",
     "bet_on": "FURIA", "price": 1.60, "actual_winner": "G2", "bet_type": "voittaja"},
    {"date": "2026-09-10T03:45:00+00:00", "team": "9z", "opponent": "G2",
     "bet_on": "G2", "price": 3.20, "actual_winner": "G2", "bet_type": "1:2 (oikea tulos)"},
    {"date": "2026-09-08T13:45:00+00:00", "team": "Astralis", "opponent": "G2",
     "bet_on": "G2", "price": 2.10, "actual_winner": "Astralis", "bet_type": "0:2 (oikea tulos, toteutui 2:1 Astraliksen voitolla)"},
    # Toinen kuvakaappaus-era (2026-09-17): kaksi puhdasta Money Line
    # -vetoa. Legacy-MIBR on SAMA ottelu kuin ylla, mutta eri veto
    # (Money Line 1.42, ei "2:0 oikea tulos") - lisatty erillisena rivina.
    {"date": "2026-09-12T06:15:00+00:00", "team": "Legacy", "opponent": "MIBR",
     "bet_on": "Legacy", "price": 1.42, "actual_winner": "Legacy", "bet_type": "voittaja (Money Line)"},
    {"date": "2026-09-12T11:05:00+00:00", "team": "BETBOOM", "opponent": "G2",
     "bet_on": "BETBOOM", "price": 2.40, "actual_winner": "G2", "bet_type": "voittaja (Money Line)"},
    # ENSIMMAINEN aidosti kaksipuolinen hinta (2026-09-18) - EI enaa
    # tarvitse ASSUMED_MARGIN-arviota, koska molemmat oikeat kertoimet
    # tunnetaan. "opponent_price" = team-sarakkeen VASTUSTAJAN kerroin.
    {"date": "2026-09-13T09:10:00+00:00", "team": "Legacy", "opponent": "G2",
     "price": 2.020, "opponent_price": 1.806,
     "bet_on": None, "actual_winner": "Legacy", "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu)"},
    # Twitter/X-postaus (Bets io Sports, "BLAST OPEN PORTO - PLAYOFFS")
    # 2026-09-18: postauksen oma paivamaara oli epaselva/ristiriitainen
    # ("Sep 4" vs grafiikan "17 TODAY"). SELVITETTY ITSE (kayttajan
    # ohje: ei kehapaatelmia, selvita ajankohta) hakemalla molemmat
    # ottelut omasta datastamme nimien perusteella - loytyivat
    # yksiselitteisesti 2026-09-04:lta ("BLAST Open Fall 2026 -
    # Playoffs" - "Porto" on vain isantakaupunki, sama tapahtuma).
    # Walk-forward kayttaa VAIN dataa ennen naita oikeita aikaleimoja.
    {"date": "2026-09-04T14:00:00+00:00", "team": "Falcons", "opponent": "G2",
     "price": 1.65, "opponent_price": 2.20,
     "bet_on": None, "actual_winner": "Falcons", "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu)"},
    {"date": "2026-09-04T16:50:00+00:00", "team": "FURIA", "opponent": "Vitality",
     "price": 2.50, "opponent_price": 1.52,
     "bet_on": None, "actual_winner": "Vitality", "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu)"},
    # Bettar "Expert predictions" (Day 1, FISSURE Playground #3 -ryhmavaihe)
    # ja Pinbetter (playoff-loppuottelut) -kuvakaappaukset (2026-09-18).
    # Kaikki ajankohdat VARMISTETTU itse omasta datastamme (ei kayttajan
    # kuvakaappauksen omasta ajasta) - kayttajan toistuva ohje: ei
    # kehapaatelmia, selvita ajankohdat itse ja kayta vain niita
    # edeltavaa dataa. Sivuston omat ennuste-artikkelitekstit jatetty
    # kokonaan huomiotta, kaytetty vain kerroin + lopputulos.
    {"date": "2026-09-08T03:00:00+00:00", "team": "9z", "opponent": "5star eSports",
     "price": 1.06, "opponent_price": 9.50,
     "bet_on": None, "actual_winner": "9z", "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu)"},
    {"date": "2026-09-08T03:00:00+00:00", "team": "Legacy", "opponent": "magic",
     "price": 1.32, "opponent_price": 3.36,
     "bet_on": None, "actual_winner": "Legacy", "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu)"},
    {"date": "2026-09-08T07:50:00+00:00", "team": "Alliance", "opponent": "FaZe",
     "price": 3.36, "opponent_price": 1.32,
     "bet_on": None, "actual_winner": "Alliance", "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu, ALIKERTOIMEN YLLATYS)"},
    {"date": "2026-09-08T05:55:00+00:00", "team": "MIBR", "opponent": "The MongolZ",
     "price": 2.15, "opponent_price": 1.70,
     "bet_on": None, "actual_winner": "MIBR", "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu, ALIKERTOIMEN YLLATYS)"},
    {"date": "2026-09-08T10:55:00+00:00", "team": "PARIVISION", "opponent": "TYLOO",
     "price": 1.60, "opponent_price": 2.33,
     "bet_on": None, "actual_winner": "PARIVISION", "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu)"},
    {"date": "2026-09-08T09:25:00+00:00", "team": "BIG", "opponent": "BETBOOM",
     "price": 2.02, "opponent_price": 1.78,
     "bet_on": None, "actual_winner": "BETBOOM", "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu)"},
    {"date": "2026-09-08T11:50:00+00:00", "team": "GamerLegion", "opponent": "FURIA",
     "price": 3.96, "opponent_price": 1.25,
     "bet_on": None, "actual_winner": "FURIA", "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu)"},
    # Seuraavat kolme ovat SAMOJA otteluita jotka ovat jo listassa ylla,
    # mutta ERI kirjanpitajan (Bettar/Pinbetter) kaksipuoliset kertoimet
    # - eri markkinahavainto samasta oikeasta ottelusta, ei sama rivi
    # kahdesti. Kayttajan huomio 2026-09-18: "voi olla duplikaatteja".
    {"date": "2026-09-08T13:45:00+00:00", "team": "G2", "opponent": "Astralis",
     "price": 1.36, "opponent_price": 3.14,
     "bet_on": None, "actual_winner": "Astralis",
     "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu) [sama ottelu kuin ylla, Bettar-kerroin, aiempi oli tarkka-tulos-kerroin]"},
    {"date": "2026-09-11T06:00:00+00:00", "team": "MIBR", "opponent": "Alliance",
     "price": 1.85, "opponent_price": 1.95,
     "bet_on": None, "actual_winner": "MIBR",
     "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu) [sama ottelu kuin ylla, Pinbetter-kerroin eri kirjanpitajalta]"},
    {"date": "2026-09-11T09:15:00+00:00", "team": "FURIA", "opponent": "G2",
     "price": 1.64, "opponent_price": 2.26,
     "bet_on": None, "actual_winner": "G2",
     "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu) [sama ottelu kuin ylla, Pinbetter-kerroin eri kirjanpitajalta]"},
    # Mega:Pari "Expert predictions" -kuvakaappaukset (2026-09-18):
    # BLAST Open Fall 2026 -ryhmavaihe, 26.-27.8.2026 (EDELTAVA turnaus
    # ennen FISSURE Playground #3:a). Ajankohdat VARMISTETTU itse omasta
    # datastamme kuten aina - sivuston omat ennustetekstit jatetty
    # huomiotta, kaytetty vain kerroin + lopputulos.
    {"date": "2026-08-26T09:00:00+00:00", "team": "Aurora", "opponent": "G2",
     "price": 2.03, "opponent_price": 1.78,
     "bet_on": None, "actual_winner": "G2", "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu)"},
    {"date": "2026-08-26T11:55:00+00:00", "team": "Spirit", "opponent": "DENDELE",
     "price": 1.12, "opponent_price": 5.20,
     "bet_on": None, "actual_winner": "Spirit", "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu)"},
    {"date": "2026-08-26T14:25:00+00:00", "team": "Natus Vincere", "opponent": "M80",
     "price": 1.22, "opponent_price": 4.30,
     "bet_on": None, "actual_winner": "M80", "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu, ALIKERTOIMEN YLLATYS)"},
    {"date": "2026-08-26T17:55:00+00:00", "team": "FURIA", "opponent": "paiN",
     "price": 1.26, "opponent_price": 3.88,
     "bet_on": None, "actual_winner": "FURIA", "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu)"},
    {"date": "2026-08-27T09:00:00+00:00", "team": "Vitality", "opponent": "Inner Circle",
     "price": 1.08, "opponent_price": 6.50,
     "bet_on": None, "actual_winner": "Inner Circle", "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu, ALIKERTOIMEN YLLATYS)"},
    {"date": "2026-08-27T12:00:00+00:00", "team": "MOUZ", "opponent": "9z",
     "price": 1.46, "opponent_price": 2.72,
     "bet_on": None, "actual_winner": "MOUZ", "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu)"},
    {"date": "2026-08-27T14:30:00+00:00", "team": "Legacy", "opponent": "FUT",
     "price": 2.00, "opponent_price": 1.73,
     "bet_on": None, "actual_winner": "Legacy", "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu, ALIKERTOIMEN YLLATYS)"},
    {"date": "2026-08-27T17:10:00+00:00", "team": "Falcons", "opponent": "Lynn Vision",
     "price": 1.05, "opponent_price": 10.00,
     "bet_on": None, "actual_winner": "Falcons", "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu)"},
    # Spinbetter-kuvakaappaukset (2026-09-18, saapuivat kesken taman
    # turnin): SAMAT playoff-neljannesfinaalit jotka ovat jo listassa
    # (Falcons vs G2, FURIA vs Vitality, 4.9.2026) - kolmas kirjanpitaja,
    # eri havaittu hinta samalle oikealle ottelulle.
    {"date": "2026-09-04T14:00:00+00:00", "team": "Falcons", "opponent": "G2",
     "price": 1.56, "opponent_price": 2.42,
     "bet_on": None, "actual_winner": "Falcons",
     "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu) [sama ottelu kuin ylla, Spinbetter-kerroin eri kirjanpitajalta]"},
    {"date": "2026-09-04T16:50:00+00:00", "team": "FURIA", "opponent": "Vitality",
     "price": 2.39, "opponent_price": 1.55,
     "bet_on": None, "actual_winner": "Vitality",
     "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu) [sama ottelu kuin ylla, Spinbetter-kerroin eri kirjanpitajalta]"},
    # tips.gg-artikkelit (2026-09-18, kayttajan antama linkki): Esports
    # World Cup 2026 -pudotuspelit, 20.-23.8.2026 - KOKONAAN UUTTA dataa,
    # ei paallekkaisyyksia aiempien kanssa. Ajankohdat VARMISTETTU itse
    # omasta datastamme kuten aina (ei sivun oman "Finished HH:MM"-ajan
    # perusteella, joka on paikallista aikaa eika UTC:ta).
    {"date": "2026-08-20T16:20:00+00:00", "team": "G2", "opponent": "FURIA",
     "price": 2.16, "opponent_price": 1.70,
     "bet_on": None, "actual_winner": "FURIA", "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu) [GGbet, EWC 2026 QF]"},
    {"date": "2026-08-21T11:00:00+00:00", "team": "FUT", "opponent": "MOUZ",
     "price": 2.39, "opponent_price": 1.57,
     "bet_on": None, "actual_winner": "FUT",
     "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu, ALIKERTOIMEN YLLATYS) [GGbet, EWC 2026 QF]"},
    {"date": "2026-08-21T13:50:00+00:00", "team": "Legacy", "opponent": "Falcons",
     "price": 3.68, "opponent_price": 1.28,
     "bet_on": None, "actual_winner": "Legacy",
     "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu, ALIKERTOIMEN YLLATYS) [GGbet, EWC 2026 QF]"},
    {"date": "2026-08-21T16:30:00+00:00", "team": "Vitality", "opponent": "Spirit",
     "price": 1.77, "opponent_price": 2.05,
     "bet_on": None, "actual_winner": "Spirit",
     "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu, ALIKERTOIMEN YLLATYS) [GGbet, EWC 2026 QF]"},
    {"date": "2026-08-22T14:00:00+00:00", "team": "FURIA", "opponent": "FUT",
     "price": 1.56, "opponent_price": 2.43,
     "bet_on": None, "actual_winner": "FUT",
     "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu, ALIKERTOIMEN YLLATYS) [Megapari, EWC 2026 SF]"},
    {"date": "2026-08-22T18:00:00+00:00", "team": "Legacy", "opponent": "Spirit",
     "price": 3.26, "opponent_price": 1.34,
     "bet_on": None, "actual_winner": "Spirit", "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu) [Megapari, EWC 2026 SF]"},
    {"date": "2026-08-23T08:00:00+00:00", "team": "FURIA", "opponent": "Legacy",
     "price": 1.56, "opponent_price": 2.43,
     "bet_on": None, "actual_winner": "Legacy",
     "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu, ALIKERTOIMEN YLLATYS) [Spinbetter, EWC 2026 3.sija]"},
    {"date": "2026-08-23T12:15:00+00:00", "team": "FUT", "opponent": "Spirit",
     "price": 3.42, "opponent_price": 1.31,
     "bet_on": None, "actual_winner": "Spirit", "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu) [Spinbetter, EWC 2026 finaali]"},
    # Kayttajan pyynnosta (2026-09-18) haettu VIELA yksi turnaus taaksepain
    # kronologisesti - ei enaa tips.gg:sta (sen analytiikka-arkisto loppuu
    # EWC 2026:een) vaan kahdesta muusta vinkkisivustosta: egamersworld.com
    # (neljannesfinaalit + 1. valierä) ja win.gg (2. valierä + finaali).
    # IEM Cologne Major 2026, Koln, 18.-21.6.2026. Ajankohdat VARMISTETTU
    # itse omasta datastamme kuten aina.
    {"date": "2026-06-18T13:45:00+00:00", "team": "Aurora", "opponent": "BETBOOM",
     "price": 1.68, "opponent_price": 2.05,
     "bet_on": None, "actual_winner": "Aurora",
     "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu) [egamersworld, IEM Cologne Major 2026 QF]"},
    {"date": "2026-06-18T17:00:00+00:00", "team": "FURIA", "opponent": "9z",
     "price": 1.26, "opponent_price": 3.40,
     "bet_on": None, "actual_winner": "FURIA",
     "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu) [egamersworld, IEM Cologne Major 2026 QF]"},
    {"date": "2026-06-19T13:45:00+00:00", "team": "Spirit", "opponent": "G2",
     "price": 1.28, "opponent_price": 3.30,
     "bet_on": None, "actual_winner": "Spirit",
     "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu) [egamersworld, IEM Cologne Major 2026 QF]"},
    {"date": "2026-06-19T19:25:00+00:00", "team": "Vitality", "opponent": "Falcons",
     "price": 1.43, "opponent_price": 2.80,
     "bet_on": None, "actual_winner": "Falcons",
     "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu, ALIKERTOIMEN YLLATYS) [egamersworld, IEM Cologne Major 2026 QF]"},
    {"date": "2026-06-20T13:45:00+00:00", "team": "FURIA", "opponent": "Aurora",
     "price": 1.61, "opponent_price": 2.16,
     "bet_on": None, "actual_winner": "FURIA",
     "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu) [egamersworld, IEM Cologne Major 2026 SF]"},
    {"date": "2026-06-20T17:00:00+00:00", "team": "Spirit", "opponent": "Falcons",
     "price": 1.75, "opponent_price": 2.04,
     "bet_on": None, "actual_winner": "Falcons",
     "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu, ALIKERTOIMEN YLLATYS) [win.gg, IEM Cologne Major 2026 SF]"},
    {"date": "2026-06-21T15:00:00+00:00", "team": "Falcons", "opponent": "FURIA",
     "price": 1.62, "opponent_price": 2.19,
     "bet_on": None, "actual_winner": "Falcons",
     "bet_type": "voittaja (Money Line, molemmat kertoimet tunnettu) [win.gg, IEM Cologne Major 2026 finaali]"},
]


def main() -> int:
    conn = get_connection()
    matches = deduplicate_matches(load_clean_matches(conn))
    conn.close()

    params = load_best_elo_params()
    elo = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])

    print(f"Elo-parametrit: scale={params['scale']} k={params['k_factor']} half_life={params['half_life_days']}\n")

    bankroll_log = []

    for bet in BETS:
        bet_date = datetime.fromisoformat(bet["date"])

        # Aja Elo VAIN otteluiden lapi jotka tapahtuivat ENNEN tata vetoa -
        # aidosti walk-forward, ei lookaheadia.
        prior = [m for m in matches if m.date < bet_date]
        elo_snapshot = EloModel(scale=params["scale"], k_factor=params["k_factor"],
                                 half_life_days=params["half_life_days"])
        for m in prior:
            elo_snapshot.update(m.team, m.opponent, m.team_won, m.date)

        p_team = elo_snapshot.predict(bet["team"], bet["opponent"], bet_date)
        n_team = elo_snapshot.games_played.get(bet["team"], 0)
        n_opp = elo_snapshot.games_played.get(bet["opponent"], 0)

        print(f"{bet['date'][:16].replace('T',' ')}  {bet['team']} vs {bet['opponent']}  [{bet['bet_type']}]")
        print(f"  Data ennen tata ottelua: {bet['team']}={n_team} ottelua, {bet['opponent']}={n_opp} ottelua")
        model_fav_early = bet["team"] if p_team >= 0.5 else bet["opponent"]
        print(f"  Mallin OMA suosikki: {model_fav_early} (P={max(p_team,1-p_team):.3f})")

        if bet.get("opponent_price") is not None:
            # MOLEMMAT oikeat kertoimet tunnetaan - EI mitaan margin-
            # oletusta tarvita, kaytetaan backtest.remove_margin():a
            # (sama funktio jota Tehtava 2 kayttaa) aidon marginaalin
            # poistoon. Tama on paras mahdollinen vertailu tassa
            # projektissa - ei mitaan arviointia, pelkkaa oikeaa dataa.
            p_fair_team, p_fair_opp = remove_margin(bet["price"], bet["opponent_price"])
            real_margin = 1 / bet["price"] + 1 / bet["opponent_price"]
            print(f"  OIKEAT kertoimet: {bet['team']}@{bet['price']}  {bet['opponent']}@{bet['opponent_price']}  "
                  f"(havaittu marginaali: {(real_margin-1)*100:.1f}%)")
            print(f"  Markkinan marginaaliton tn: {bet['team']}={p_fair_team:.3f}  {bet['opponent']}={p_fair_opp:.3f}")
            print(f"  Mallin tn:                  {bet['team']}={p_team:.3f}  {bet['opponent']}={1-p_team:.3f}")
            for side, price, p_model in ((bet["team"], bet["price"], p_team),
                                          (bet["opponent"], bet["opponent_price"], 1 - p_team)):
                ev_side = p_model * price - 1
                decision = "PANOSTAISIN (EV>0)" if ev_side > 0 else "EN PANOSTAISI (EV<=0)"
                print(f"    {side}@{price}: EV = {ev_side:+.1%}  ->  {decision}")
                if ev_side > 0:
                    won_side = bet["actual_winner"] == side
                    profit = (price - 1) if won_side else -1.0
                    bankroll_log.append((bet["date"], f"{side}@{price}", ev_side, profit, won_side, False,
                                          min(n_team, n_opp)))
                    print(f"      Jos panostettu 1 yksikko: {'+' if profit>0 else ''}{profit:.2f} "
                          f"({'voitti' if won_side else 'havisi'})")
            print()
            continue

        is_exact_score = "oikea tulos" in bet["bet_type"]

        # RIIPPUMATON arvio: hinnoiteltiin AINOASTAAN se puoli jolle
        # kayttajan slipissa oli kerroin - lasketaan talle puolelle EV
        # mallin mukaan RIIPPUMATTA siita mita kayttaja veikkasi. Tama
        # EI ole "osuiko kayttaja oikein" vaan "onko TAMA HINTA arvokas
        # mallin mukaan" - kayttajan pyynnosta 2026-09-17.
        priced_side = bet["bet_on"]  # ainoa puoli jolle meilla on oikea kerroin
        p_priced = p_team if priced_side == bet["team"] else (1 - p_team)
        fair_odds_priced = 1 / p_priced if p_priced > 0 else float("inf")
        ev = p_priced * bet["price"] - 1
        priced_side_won = bet["actual_winner"] == priced_side

        if is_exact_score:
            print(f"  Ainoa tunnettu kerroin ({bet['price']}) oli TARKALLE TULOKSELLE - ei vertailukelpoinen"
                  f" voitto-tn:n kanssa, EI kaytetty arvopaatoksessa.")
        else:
            print(f"  Tunnettu kerroin: {priced_side} @ {bet['price']}  "
                  f"(mallin reilu kerroin talle puolelle: {fair_odds_priced:.2f})  ->  EV = {ev:+.1%}")
            decision = "PANOSTAISIN (EV>0)" if ev > 0 else "EN PANOSTAISI (EV<=0)"
            print(f"  RIIPPUMATON PAATOS taman hinnan perusteella: {decision}")
            if ev <= 0:
                # Kayttajan huomio 2026-09-18: jos tama puoli on ALIKERROIN
                # (huono arvo), VASTAPUOLI on lahes aina YLIKERROIN (hyva
                # arvo), koska molemmat puolet ovat sidoksissa toisiinsa
                # saman markkinan marginaalin kautta. EI meilla OIKEAA
                # kerrointa vastapuolelle - ARVIOIDAAN se olettamalla
                # tyypillinen ~5% marginaali (ASSUMED_MARGIN). Kayttajan
                # ohje 2026-09-18: nama YHDISTETAAN samaan nettotulokseen
                # havaittuihin kertoimiin perustuvien vetojen kanssa (ei
                # pideta enaa erillaan) - jokainen rivi silti merkitaan
                # selvasti "(arvio)"-tunnisteella, jotta lahde nakyy.
                other_side = bet["opponent"] if priced_side == bet["team"] else bet["team"]
                implied_priced = 1 / bet["price"]
                implied_other_est = max(ASSUMED_MARGIN - implied_priced, 0.01)
                price_other_est = 1 / implied_other_est
                p_other = 1 - p_priced
                ev_other_est = p_other * price_other_est - 1
                other_won = bet["actual_winner"] == other_side
                print(f"  -> VASTAPUOLI {other_side}: ARVIOITU kerroin (~{int((ASSUMED_MARGIN-1)*100)}% marginaali-"
                      f"oletuksella) = {price_other_est:.2f}  ->  ARVIOITU EV = {ev_other_est:+.1%}"
                      f"  [EI OIKEA HAVAITTU KERROIN, vain arvio]")
                if ev_other_est > 0:
                    est_profit = (price_other_est - 1) if other_won else -1.0
                    bankroll_log.append((bet["date"], f"{other_side}@{price_other_est:.2f} (arvio)",
                                          ev_other_est, est_profit, other_won, True, min(n_team, n_opp)))
                    print(f"     Jos tama arvio pitaisi paikkansa ja panostettu 1 yksikko {other_side}:lle: "
                          f"{'VOITTI' if other_won else 'HAVISI'}")
            if ev > 0:
                profit = (bet["price"] - 1) if priced_side_won else -1.0
                bankroll_log.append((bet["date"], f"{priced_side}@{bet['price']}", ev, profit, priced_side_won, False,
                                      min(n_team, n_opp)))
                print(f"  Jos panostettu 1 yksikko: {'+' + format(profit, '.2f') if profit > 0 else format(profit, '.2f')} "
                      f"({'VOITTI' if priced_side_won else 'HAVISI'})")
        print()

    print("=== YHTEENVETO: 'panosta vain kun EV>0' -strategia (riippumaton kayttajan valinnoista) ===")
    print("(Kayttajan ohje 2026-09-18: havaitut ja ASSUMED_MARGIN-arvioidut vastapuolen kertoimet")
    print(" YHDISTETAAN samaan nettotulokseen - '(arvio)'-merkki rivilla kertoo lahteen.)")
    if bankroll_log:
        bankroll_log_sorted = sorted(bankroll_log, key=lambda row: row[0])
        total_profit = sum(p for _, _, _, p, _, _, _ in bankroll_log_sorted)
        total_ev = sum(ev for _, _, ev, _, _, _, _ in bankroll_log_sorted)  # odotusarvo (1 yksikko/veto)
        wins = sum(1 for *_, w, _, _ in bankroll_log_sorted if w)
        print(f"Panoksia tehty: {len(bankroll_log_sorted)}, joista voitti: {wins}")
        for date, desc, ev, profit, won, is_est, min_games in bankroll_log_sorted:
            tag = " (arvio)" if is_est and "(arvio)" not in desc else ""
            thin = "  [OHUT DATA]" if min_games < MIN_GAMES_FOR_RELIABLE else ""
            print(f"  {date[:10]}  {desc}{tag}  EV={ev:+.1%}  ->  "
                  f"{'+' if profit>0 else ''}{profit:.2f}  ({'voitti' if won else 'havisi'}, "
                  f"min_ottelut={min_games}){thin}")
        print(f"\nOdotusarvo (mallin mukainen, ennen tuloksia) {len(bankroll_log_sorted)} yksikon panoksella: "
              f"{'+' if total_ev>=0 else ''}{total_ev:.2f} yksikkoa "
              f"({total_ev/len(bankroll_log_sorted):+.1%} keskimaarin per panos)")
        print(f"Nettotulos (toteutunut) {len(bankroll_log_sorted)} yksikon panoksella (1 yksikko/veto): "
              f"{'+' if total_profit>=0 else ''}{total_profit:.2f} yksikkoa "
              f"({total_profit/len(bankroll_log_sorted):+.1%} keskimaarin per panos)")

        # Kayttajan pyynnosta 2026-09-18: sama laskelma, mutta poistettu
        # vedot joissa jommallakummalla joukkueella on alle
        # MIN_GAMES_FOR_RELIABLE ottelua taustalla (esim. 5star=1 ottelu).
        reliable = [row for row in bankroll_log_sorted if row[6] >= MIN_GAMES_FOR_RELIABLE]
        excluded = len(bankroll_log_sorted) - len(reliable)
        print(f"\n=== SAMA, MUTTA OHUTDATAISET VEDOT POISTETTU (alle {MIN_GAMES_FOR_RELIABLE} "
              f"ottelua jommallakummalla joukkueella) ===")
        print(f"Poistettu {excluded}/{len(bankroll_log_sorted)} vetoa.")
        if reliable:
            rel_profit = sum(p for _, _, _, p, _, _, _ in reliable)
            rel_ev = sum(ev for _, _, ev, _, _, _, _ in reliable)
            rel_wins = sum(1 for *_, w, _, _ in reliable if w)
            print(f"Panoksia jaljella: {len(reliable)}, joista voitti: {rel_wins}")
            print(f"Odotusarvo: {'+' if rel_ev>=0 else ''}{rel_ev:.2f} yksikkoa "
                  f"({rel_ev/len(reliable):+.1%} keskimaarin per panos)")
            print(f"Nettotulos: {'+' if rel_profit>=0 else ''}{rel_profit:.2f} yksikkoa "
                  f"({rel_profit/len(reliable):+.1%} keskimaarin per panos)")
        else:
            print("Ei yhtaan vetoa jaljella suodatuksen jalkeen.")
    else:
        print("Ei yhtaan EV>0-tilannetta loytynyt naista otteluista.")
    print("\n(Tarkka tulos -vedot eivat olleet mukana arvopaatoksessa - eri bet-tyyppi,")
    print(" ei suoraan vertailukelpoinen voitto-tn:n kanssa ilman erillista scoreline-mallia.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
