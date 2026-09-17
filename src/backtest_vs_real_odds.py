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
# EV<=0 - tyypillinen esports-Money Line -marginaali. TAMA ON ARVAUS,
# ei mitattu tasta datasta - ks. kayton yhteydessa oleva HUOM-merkinta.
ASSUMED_MARGIN = 1.05

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

        print(f"{bet['date'][:16].replace('T',' ')}  {bet['team']} vs {bet['opponent']}  [{bet['bet_type']}]")
        print(f"  Data ennen tata ottelua: {bet['team']}={n_team} ottelua, {bet['opponent']}={n_opp} ottelua")
        model_fav = bet["team"] if p_team >= 0.5 else bet["opponent"]
        print(f"  Mallin OMA suosikki: {model_fav} (P={max(p_team,1-p_team):.3f}) - riippumatta kertoimesta")
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
                # tyypillinen ~5% marginaali (ASSUMED_MARGIN), JOTTA
                # NAHDAAN OLISIKO LOGIIKKA PITANYT PAIKKANSA. Selvasti
                # merkitty ARVIOKSI, ei havaituksi kertoimeksi.
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
                    print(f"     Jos tama arvio pitaisi paikkansa ja panostettu 1 yksikko {other_side}:lle: "
                          f"{'VOITTI' if other_won else 'HAVISI'}")
            if ev > 0:
                profit = (bet["price"] - 1) if priced_side_won else -1.0
                bankroll_log.append((bet["date"], f"{priced_side}@{bet['price']}", profit, priced_side_won))
                print(f"  Jos panostettu 1 yksikko: {'+' + format(profit, '.2f') if profit > 0 else format(profit, '.2f')} "
                      f"({'VOITTI' if priced_side_won else 'HAVISI'})")
        print()

    print("=== YHTEENVETO: 'panosta vain kun EV>0' -strategia (riippumaton kayttajan valinnoista) ===")
    if bankroll_log:
        total_profit = sum(p for _, _, p, _ in bankroll_log)
        wins = sum(1 for *_, w in bankroll_log if w)
        print(f"Panoksia tehty: {len(bankroll_log)}, joista voitti: {wins}")
        for date, desc, profit, won in bankroll_log:
            print(f"  {date[:10]}  {desc}  ->  {'+' if profit>0 else ''}{profit:.2f}  ({'voitti' if won else 'havisi'})")
        print(f"\nNettotulos {len(bankroll_log)} yksikon panoksella (1 yksikko/veto): "
              f"{'+' if total_profit>=0 else ''}{total_profit:.2f} yksikkoa "
              f"({total_profit/len(bankroll_log):+.1%} keskimaarin per panos)")
    else:
        print("Ei yhtaan EV>0-tilannetta loytynyt naista otteluista.")
    print("\n(Tarkka tulos -vedot eivat olleet mukana arvopaatoksessa - eri bet-tyyppi,")
    print(" ei suoraan vertailukelpoinen voitto-tn:n kanssa ilman erillista scoreline-mallia.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
