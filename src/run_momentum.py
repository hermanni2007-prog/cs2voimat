"""
Kayttajan pyynto 2026-09-18: onko datassa "momentum-faktoria" - ennustaako
X perakkaista havioita todennakoisemmin havion, tai X perakkaista voittoa
todennakoisemmin voiton?

TARKEA EROTTELU: Elo-rating jo itsessaan nousee voittoputken aikana ja
laskee haviopütken aikana - jos vain katsoisi "voittaako voittoputkella
oleva joukkue useammin", loydettaisiin pelkastaan "hyvat joukkueet
voittavat useammin" uudelleen, ei mitaan UUTTA. Oikea kysymys: ennustaako
striikin PITUUS lopputuloksen viela senkin jalkeen kun Elo-mallin oma
ennuste (joka jo sisaltaa striikin nostaman/laskeneen ratingin) on
huomioitu? Eli: onko jaljelle jaavassa RESIDUAALISSA (toteutunut - Elon
ennustama P) systemaattista yhteytta striikin pituuteen?

METODOLOGIA: kaksivaiheinen, sama periaate kuin koko projektissa.
  1) DESKRIPTIIVINEN: kaikki data, ei fitata mitaan - vain katsotaan
     onko residuaalissa nakyvaa kuviota striikin mukaan. Puhdas havainto,
     ei viela paatosta.
  2) Jos vaihe 1 nayttaa jotain, muodostetaan konkreettinen korjaus ja
     validoidaan OIKEIN (fit train-osiolla, testi nakemattomalla
     osiolla) - EI ennen kuin tama on tehty, mitaan ei oteta kayttoon."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from backtest import (  # noqa: E402
    EloModel,
    deduplicate_matches,
    filter_top50_only,
    load_clean_matches,
    load_best_elo_params,
    log_loss,
)
from team_names import load_top50_names  # noqa: E402

MAX_STREAK_BUCKET = 5  # 5+ niputetaan yhteen (otoskoko)


def streak_bucket(streak: int) -> int:
    """streak: positiivinen = voittoputki, negatiivinen = haviosputki, 0 = ei putkea
    (edellinen tulos poikkeaa sita edellisesta, tai ensimmainen ottelu)."""
    return max(-MAX_STREAK_BUCKET, min(MAX_STREAK_BUCKET, streak))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-sos-filter", action="store_true",
                         help="Aja koko (suodattamattomalla) 2498 ottelun datalla SOS-suodatuksen "
                              "sijaan - vertailua varten, ei oletusarvoinen kayttotapa.")
    args = parser.parse_args()

    conn = get_connection()
    all_matches = deduplicate_matches(load_clean_matches(conn))
    conn.close()
    if args.no_sos_filter:
        matches = all_matches
        print("HUOM: --no-sos-filter -> kaytetaan KOKO deduplikoitua dataa (ei top50-vs-top50 -rajausta)\n")
    else:
        matches = filter_top50_only(all_matches, load_top50_names())
    params = load_best_elo_params()

    elo = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
    current_streak: dict = {}  # team -> int (positiivinen=voittoputki, negatiivinen=haviosputki)

    rows = []  # (streak_bucket_oma, streak_bucket_vastustaja, p_model, actual)
    for m in matches:
        p = elo.predict(m.team, m.opponent, m.date)
        s_team = current_streak.get(m.team, 0)
        s_opp = current_streak.get(m.opponent, 0)
        rows.append({
            "date": m.date, "team": m.team, "opponent": m.opponent,
            "streak_team": streak_bucket(s_team), "streak_opp": streak_bucket(s_opp),
            "p_model": p, "actual": m.team_won,
        })
        elo.update(m.team, m.opponent, m.team_won, m.date)

        # Paivita striikit VASTA nyt (tama ottelu ei viela ollut osa striikkia ennustushetkella)
        if m.team_won:
            current_streak[m.team] = max(1, current_streak.get(m.team, 0) + 1)
            current_streak[m.opponent] = min(-1, current_streak.get(m.opponent, 0) - 1)
        else:
            current_streak[m.opponent] = max(1, current_streak.get(m.opponent, 0) + 1)
            current_streak[m.team] = min(-1, current_streak.get(m.team, 0) - 1)

    print(f"Otteluita analysoitu: {len(rows)}\n")

    print("=== VAIHE 1: Residuaali (toteutunut - Elon ennustama P) OMAN striikin mukaan ===")
    print("(positiivinen residuaali = joukkue voitti Elon ennustettua useammin; "
          "negatiivinen = havisi ennustettua useammin)\n")
    buckets: dict = {}
    for r in rows:
        b = r["streak_team"]
        buckets.setdefault(b, []).append(r["actual"] - r["p_model"])

    print(f"{'Striikki':>10}  {'n':>5}  {'ka. residuaali':>15}")
    for b in sorted(buckets):
        vals = buckets[b]
        avg = sum(vals) / len(vals)
        label = (f"+{b} voitto" if b > 0 else (f"{b} havio" if b < 0 else "0 (ei putkea)"))
        print(f"{label:>10}  {len(vals):>5}  {avg:>+15.4f}")

    # Yksinkertainen korrelaatio: striikin (oma) suuruus vs. residuaali
    xs = [r["streak_team"] for r in rows]
    ys = [r["actual"] - r["p_model"] for r in rows]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / n
    var_x = sum((x - mean_x) ** 2 for x in xs) / n
    var_y = sum((y - mean_y) ** 2 for y in ys) / n
    corr = cov / ((var_x * var_y) ** 0.5) if var_x > 0 and var_y > 0 else 0.0
    print(f"\nKorrelaatio (striikin pituus vs. residuaali): r = {corr:+.4f}  (n={n})")
    print("(|r| < 0.05 luokkaa tarkoittaa kaytannossa ei havaittavaa yhteytta)")

    print("\n=== Sama, mutta VASTUSTAJAN striikin mukaan ===")
    buckets_opp: dict = {}
    for r in rows:
        b = r["streak_opp"]
        buckets_opp.setdefault(b, []).append(r["actual"] - r["p_model"])
    print(f"{'Vastustajan striikki':>22}  {'n':>5}  {'ka. residuaali (oma nakokulma)':>32}")
    for b in sorted(buckets_opp):
        vals = buckets_opp[b]
        avg = sum(vals) / len(vals)
        label = (f"+{b} voitto" if b > 0 else (f"{b} havio" if b < 0 else "0 (ei putkea)"))
        print(f"{label:>22}  {len(vals):>5}  {avg:>+32.4f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
