"""
Nopea ottelukohtainen ennustetyokalu, kayttoon ad-hoc kysymyksiin
("mitka kertoimet pitaisi olla X vs Y") ilman etukateen tallennettua
sarjatilannetta (ks. analyze_series.py Bo3-sarjatilanteille).

Kayttaa Tehtava 3:n ottelutason Elo-mallia (historical_matches, korjattu
dedup-bugi 2026-09-17) ja EloModel.predict_with_confidence() -metodia
(lisatty 2026-09-17) epavarmuushaarukan nayttamiseen - katso backtest.py:n
kommentti: karkea Glicko-tyylinen rating deviation, EI tilastollisesti
tasmallinen luottamusvali.

Soveltaa "ruostumis"-korjauksen (backtest.apply_rust_adjustment): jos
suosikki ei ole pelannut yhtaan ottelua viimeisen 5 vrk:n aikana,
ennustetta kutistetaan kohti 0.5:ta kertoimella 0.79.

Soveltaa myos online-korjauksen (backtest.apply_online_adjustment,
lisatty 2026-09-18, --online-lippu): jos ottelu pelataan onlinena (ei
LANilla), ennuste kutistetaan KOKONAAN kohti 0.5:ta (ONLINE_SHRINK=0.0) -
validoitu loytamalla etta mallilla ei ole online-otteluissa kaytannossa
minkaanlaista ennustearvoa markkinaa/arvausta parempaa (ks. backtest.py:n
kommentti ja run_online_adjustment.py). Todennakoinen syy: top-joukkueet
kayttavat online-karsinnoissa useammin stand-ineja / eivat panosta
taydella kokoonpanolla.

HUOM METODOLOGIASTA (2026-09-17, kayttajan perustellun huomion jalkeen):
tama kerroin on validoitu OIKEIN - fitattu VAIN kronologisen datan
ensimmaisella 80%:lla, sovellettu ja tarkistettu VASTA nakemattomalla
20%:lla (ks. README). Aiemmin talla samalla paikalla oli myos "taso"-
korjaus (S/A-Tier -otteluille) joka NAYTTI toimivan kun se validoitiin
virheellisesti (fitattu JA testattu samalla koko datasetilla) - oikealla
train/test-erottelulla se osoittautui ylisovitukseksi (huononsi test-
tulosta) ja poistettiin. Ruostumiskorjaus on ainoa jaljella oleva
korjaus koska se on ainoa joka selvisi oikeasta, ei-kehamaisesta
validoinnista - silti pienella otoksella (n=28 test-ottelua), joten
"toistaiseksi tuettu", ei "todistettu"."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from backtest import (  # noqa: E402
    EloModel,
    RUST_WINDOW_DAYS,
    apply_all_adjustments,
    count_recent_matches,
    current_elo_rank,
    deduplicate_matches,
    filter_top50_only,
    load_clean_matches,
    load_best_elo_params,
    online_k_override,
)
from run_tournament_effects import build_recent_match_index  # noqa: E402
from team_names import load_top50_names  # noqa: E402

_params = load_best_elo_params()
SCALE, K_FACTOR, HALF_LIFE = _params["scale"], _params["k_factor"], _params["half_life_days"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("team")
    parser.add_argument("opponent")
    parser.add_argument("--online", action="store_true",
                         help="Ottelu pelataan onlinena (ei LANilla) - soveltaa validoidun "
                              "online-korjauksen (ks. yla kommentti).")
    args = parser.parse_args()
    match_type = "Online" if args.online else None

    conn = get_connection()
    matches = deduplicate_matches(load_clean_matches(conn))
    conn.close()

    # SOS-suodatus (2026-09-18, validoitu ei-kehamaisesti run_sos_filter.py:ssa):
    # top50-ulkopuoliset vastustajat pois Elo-paivityksesta - katso backtest.py:n
    # filter_top50_only()-kommentti.
    top50_names = load_top50_names()
    matches = filter_top50_only(matches, top50_names)

    elo = EloModel(scale=SCALE, k_factor=K_FACTOR, half_life_days=HALF_LIFE)
    for m in matches:
        elo.update(m.team, m.opponent, m.team_won, m.date,
                    k_override=online_k_override(K_FACTOR, m.match_type))
    last_date = matches[-1].date if matches else None
    recent_idx = build_recent_match_index(matches)

    # Turvaverkko (lisatty 2026-09-17, "korjaa lisaa puutteita"): jos
    # nimi ei osu YHTAAN dataan (0 ottelua JA ei top50-listalla), tama on
    # todennakoisemmin KIRJOITUSVIRHE kuin oikeasti aloitteleva joukkue -
    # ilman tata varoitusta EloModel palauttaa hiljaa n=0/rating=1500,
    # mika nayttaa identtiselta oikealta "ei dataa" -tilanteelta.
    for name in (args.team, args.opponent):
        if name not in elo.ratings and name not in top50_names:
            print(f"  VAROITUS: '{name}' ei loydy top50-listalta eika sille ole yhtaan ottelua -"
                  f" tarkista kirjoitusasu (esim. isot/pienet kirjaimet, koko nimi vs. lyhenne).")

    r = elo.predict_with_confidence(args.team, args.opponent, last_date)

    n_recent_team = count_recent_matches(args.team, last_date, recent_idx, RUST_WINDOW_DAYS) if last_date else 0
    n_recent_opp = count_recent_matches(args.opponent, last_date, recent_idx, RUST_WINDOW_DAYS) if last_date else 0
    rank_team = current_elo_rank(elo, args.team, last_date) if last_date else None
    rank_opp = current_elo_rank(elo, args.opponent, last_date) if last_date else None
    p_adjusted = apply_all_adjustments(r["p_mid"], n_recent_team, n_recent_opp, match_type=match_type,
                                        rank_team=rank_team, rank_opponent=rank_opp)

    is_levea = rank_team is not None and rank_opp is not None and rank_team > 20 and rank_opp > 20

    print(f"{args.team} vs {args.opponent}" + ("  [ONLINE-ottelu]" if match_type == "Online" else ""))
    print(f"  n_ottelua: {args.team}={r['n_team']}  {args.opponent}={r['n_opp']}  -> luottamus: {r['confidence']}")
    if rank_team is not None:
        print(f"  Elo-sija: {args.team}=#{rank_team}  {args.opponent}=#{rank_opp}" +
              ("  [LEVEA-taso: molemmat top21-75]" if is_levea else ""))
    print(f"  P({args.team}) raaka = {r['p_mid']:.3f}  (haarukka [{r['p_low']:.3f}, {r['p_high']:.3f}])")
    if match_type == "Online":
        print(f"  P({args.team}) ONLINE-KORJATTU = {p_adjusted:.3f}  "
              f"(mallilla ei validoinnin mukaan ole online-otteluissa kaytannon ennustearvoa - "
              f"katso backtest.py:n ONLINE_SHRINK-kommentti)")
    elif is_levea:
        print(f"  P({args.team}) LEVEA-TASO-KORJATTU = {p_adjusted:.3f}  "
              f"(molemmat joukkueet top21-75: mallilla heikompi kalibrointi tassa poolissa - "
              f"katso backtest.py:n LEVEA_SHRINK-kommentti)")
    elif p_adjusted != r["p_mid"]:
        print(f"  P({args.team}) RUOSTUMISKORJATTU = {p_adjusted:.3f}  "
              f"(suosikilla 0 ottelua viimeisen {RUST_WINDOW_DAYS} vrk:n aikana - toistaiseksi tuettu, pieni otos)")
    p_final = p_adjusted
    print(f"  Reilu kerroin {args.team}: {1/p_final:.2f}")
    print(f"  Reilu kerroin {args.opponent}: {1/(1-p_final):.2f}")
    if r["confidence"] == "MATALA":
        print("\n  HUOM: MATALA luottamus - jommallakummalla joukkueella alle 10 kelvollista ottelua."
              " Piste-ennustetta ei pida kayttaa yhta luottavaisesti kuin HYVA-luokan ennusteita.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
