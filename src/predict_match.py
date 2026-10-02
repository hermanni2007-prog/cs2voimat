"""
Nopea ottelukohtainen ennustetyokalu, kayttoon ad-hoc kysymyksiin
("mitka kertoimet pitaisi olla X vs Y") ilman etukateen tallennettua
sarjatilannetta (ks. analyze_series.py Bo3-sarjatilanteille).

2026-09-28 ALKAEN PAAMALLI ON KARTTATASON ELO (run_format_map_model.py,
map_elo_series_prob): paivittyy jokaisesta kartasta ja laskee sarjaennusteen
sarjan pituuden (--bo) mukaan. Alla kuvattu sarjatason Elo naytetaan vertailuna.

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
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from backtest import (  # noqa: E402
    EloModel,
    MatchRow,
    FATIGUE_THRESHOLD,
    FATIGUE_WINDOW_DAYS,
    RUST_WINDOW_DAYS,
    ONLINE_EVEN_MAX,
    ONLINE_SHRINK,
    apply_all_adjustments,
    count_recent_matches,
    current_elo_rank,
    deduplicate_matches,
    load_clean_matches,
    load_best_elo_params,
    production_k_override,
)
from run_format_map_model import map_elo_series_prob  # noqa: E402
from manual_results import load_manual_results  # noqa: E402
from run_tournament_effects import build_recent_match_index  # noqa: E402
from xr_model import load_map_games  # noqa: E402
from team_names import load_top50_names, resolve_to_canonical  # noqa: E402

_params = load_best_elo_params()
SCALE, K_FACTOR, HALF_LIFE = _params["scale"], _params["k_factor"], _params["half_life_days"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("team")
    parser.add_argument("opponent")
    parser.add_argument("--online", action="store_true",
                         help="Ottelu pelataan onlinena (ei LANilla) - soveltaa validoidun "
                              "online-korjauksen (ks. yla kommentti).")
    parser.add_argument("--bo", type=int, choices=(1, 3, 5), default=3,
                         help="Sarjan pituus (oletus 3) - karttatason Elo laskee sarjan "
                              "voittotodennakoisyyden taman mukaan. ANNA AINA OIKEA ARVO.")
    parser.add_argument("--extra", action="append", default=[],
                         help='Tuore tulos jota CI ei ole viela kerannyt, muodossa '
                              '"Joukkue A;Joukkue B;2-0;2026-09-28T11:00". Kaytetaan VAIN taman '
                              'ennusteen ajan, EI tallenneta tietokantaan (kasin syotetyt rivit '
                              'aiheuttivat tuplia). Jos sama ottelu on jo datassa, dedup poistaa sen.')
    parser.add_argument("--no-manual", action="store_true",
                         help="Ala lue data/manual_results.json-tiedostoa.")
    args = parser.parse_args()
    match_type = "Online" if args.online else None

    conn = get_connection()
    matches = load_clean_matches(conn)
    games = load_map_games(conn, load_top50_names())
    conn.close()
    for spec in args.extra:
        a, b, score, when = [x.strip() for x in spec.split(";")]
        sa, sb = (int(x) for x in score.split("-"))
        when_dt = datetime.fromisoformat(when)
        if when_dt.tzinfo is None:
            when_dt = when_dt.replace(tzinfo=timezone.utc)
        ca, cb = resolve_to_canonical(a, load_top50_names()), resolve_to_canonical(b, load_top50_names())
        matches.append(MatchRow(date=when_dt, team=ca, opponent=cb, tier=None, match_type="Offline",
                                tournament="--extra", team_won=int(sa > sb), score_team=sa, score_opponent=sb))
        print(f"  (lisatty vain tahan ennusteeseen: {ca} {sa}-{sb} {cb}, {when_dt.isoformat()[:16]} UTC)")
    # 2026-10-02: kayttajan antamat tuoreet tulokset (data/manual_results.json),
    # vain muistissa; jo datassa olevat ohitetaan (ks. manual_results.py).
    if not args.no_manual:
        manual, skipped = load_manual_results(matches, load_top50_names())
        matches.extend(manual)
        if manual or skipped:
            print(f"  (manual_results.json: {len(manual)} tulosta lisatty muistiin, {skipped} jo datassa -> ohitettu)")
    matches.sort(key=lambda m: m.date)
    matches = deduplicate_matches(matches)

    # SOS-PEHMENNYS (2026-09-18, korvasi taman paivan aiemman hard filter_
    # top50_only:n - ks. backtest.py:n sos_k_override()-kommentti): KAIKKI
    # ottelut mukana Elo-paivityksessa, top75-ulkopuoliset vastustajat vain
    # pienemmalla K:lla (SOS_NON_TOP50_K_WEIGHT) taysin poissulkemisen sijaan.
    top50_names = load_top50_names()
    # 2026-09-28: "Magic" -> "magic" (kanoninen nimi on pienella) - ilman tata
    # kirjoitusasu erosi hiljaa ja ennuste laskettiin 1500-oletusratingilla.
    args.team = resolve_to_canonical(args.team, top50_names)
    args.opponent = resolve_to_canonical(args.opponent, top50_names)

    elo = EloModel(scale=SCALE, k_factor=K_FACTOR, half_life_days=HALF_LIFE)
    for m in matches:
        elo.update(m.team, m.opponent, m.team_won, m.date,
                    k_override=production_k_override(K_FACTOR, m.match_type, m.team, m.opponent, top50_names))
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
    n_fatigue_team = count_recent_matches(args.team, last_date, recent_idx, FATIGUE_WINDOW_DAYS) if last_date else 0
    n_fatigue_opp = count_recent_matches(args.opponent, last_date, recent_idx, FATIGUE_WINDOW_DAYS) if last_date else 0
    rank_team = current_elo_rank(elo, args.team, last_date, candidate_names=top50_names) if last_date else None
    rank_opp = current_elo_rank(elo, args.opponent, last_date, candidate_names=top50_names) if last_date else None
    # TUOTANTOMALLI 2026-09-28: karttatason Elo (huomioi sarjan pituuden --bo).
    # Rolling origin (run_format_map_model.py): parempi kuin vanha sarjatason Elo
    # 4/5 ikkunassa, P(parannus>0)=0.85, mutta heikompi Bo1:ssa (n=130). Vanha
    # malli naytetaan vertailuna alla. Korjaukset (online/ruostuminen/vasymys)
    # validoitiin vanhalla mallilla - sovelletaan samoina.
    p_mid, p_low, p_high = map_elo_series_prob(matches, games, top50_names, args.team, args.opponent, args.bo,
                                               r["rd_team"], r["rd_opp"])

    def adjust(p):
        return apply_all_adjustments(p, n_recent_team, n_recent_opp, match_type=match_type,
                                     rank_team=rank_team, rank_opponent=rank_opp,
                                     n_fatigue_team=n_fatigue_team, n_fatigue_opponent=n_fatigue_opp)
    p_adjusted = adjust(p_mid)
    p_old = adjust(r["p_mid"])

    is_levea = rank_team is not None and rank_opp is not None and rank_team > 20 and rank_opp > 20
    is_team_favorite = p_mid >= 0.5
    favorite_n_fatigue = n_fatigue_team if is_team_favorite else n_fatigue_opp
    underdog_n_fatigue = n_fatigue_opp if is_team_favorite else n_fatigue_team
    # HUOM 2026-09-20: taytyy olla AIDOSTI vasyneempi kuin altavastaaja, ei
    # vain omilla ansioillaan yli kynnyksen - ks. backtest.py:n
    # apply_fatigue_adjustment()-kommentti (Vitaly-FURIA-bugikorjaus).
    is_fatigued = favorite_n_fatigue >= FATIGUE_THRESHOLD and favorite_n_fatigue > underdog_n_fatigue

    print(f"{args.team} vs {args.opponent}  [Bo{args.bo}]" + ("  [ONLINE-ottelu]" if match_type == "Online" else ""))
    print(f"  n_ottelua: {args.team}={r['n_team']}  {args.opponent}={r['n_opp']}  -> luottamus: {r['confidence']}")
    if rank_team is not None:
        print(f"  Elo-sija: {args.team}=#{rank_team}  {args.opponent}=#{rank_opp}" +
              ("  [LEVEA-taso: molemmat top21-75]" if is_levea else ""))
    print(f"  P({args.team}) raaka = {p_mid:.3f}  (haarukka [{p_low:.3f}, {p_high:.3f}])  - karttatason Elo")
    if match_type == "Online":
        print(f"  P({args.team}) ONLINE-KORJATTU = {p_adjusted:.3f}  "
              f"(kutistus {ONLINE_SHRINK} kohti 0.5:ta - heikko signaali, ks. backtest.py:n ONLINE_SHRINK)")
        if max(p_mid, 1 - p_mid) > ONLINE_EVEN_MAX:
            print(f"  HUOM ONLINE + iso ero (raaka suosikki > {ONLINE_EVEN_MAX}): EV ei luotettava,"
                  " EI arvovetoa (sovittu saanto 2.10.)")
    # HUOM: LEVEA-tier-shrink on nykyaan no-op (LEVEA_SHRINK=1.0, ks.
    # backtest.py:n kommentti) - SOS-pehmennys korvasi sen tarpeen, joten
    # taalla ei enaa nayteta erillista "LEVEA-TASO-KORJATTU" -viestia.
    elif is_fatigued:
        print(f"  P({args.team}) VASYMYSKORJATTU = {p_adjusted:.3f}  "
              f"(suosikilla >={FATIGUE_THRESHOLD} ottelua viimeisen {FATIGUE_WINDOW_DAYS*24:.0f}h aikana - "
              f"toistaiseksi tuettu, pieni otos, ks. backtest.py:n FATIGUE_SHRINK-kommentti)")
    elif p_adjusted != p_mid:
        print(f"  P({args.team}) RUOSTUMISKORJATTU = {p_adjusted:.3f}  "
              f"(suosikilla 0 ottelua viimeisen {RUST_WINDOW_DAYS} vrk:n aikana - toistaiseksi tuettu, pieni otos)")
    p_final = p_adjusted
    print(f"  Reilu kerroin {args.team}: {1/p_final:.2f}")
    print(f"  Reilu kerroin {args.opponent}: {1/(1-p_final):.2f}")

    print(f"\n  [vertailu] vanha sarjatason Elo (ei huomioi formaattia): P({args.team}) = {p_old:.3f}  "
          f"-> reilut kertoimet {1/p_old:.2f} / {1/(1-p_old):.2f}")
    if args.bo == 1:
        print("  HUOM Bo1: karttatason Elo oli testissa Bo1-otteluissa HEIKOMPI kuin vanha malli (n=130) -"
              " jos mallit eroavat, ala luota kumpaankaan vahvasti")
    if abs(p_old - p_final) >= 0.05:
        print(f"  HUOM: mallit eroavat {abs(p_old - p_final) * 100:.0f} %-yksikkoa - ennuste epavarmempi kuin haarukka antaa ymmartaa")
    if r["confidence"] == "MATALA":
        print("\n  HUOM: MATALA luottamus - jommallakummalla joukkueella alle 10 kelvollista ottelua."
              " Piste-ennustetta ei pida kayttaa yhta luottavaisesti kuin HYVA-luokan ennusteita.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
