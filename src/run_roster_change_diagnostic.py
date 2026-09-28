"""
Selittavatko kokoonpanomuutokset tuotantomallin syyskuun heikkenemisen?
(Rolling origin naytti tuotanto-Elon AUC:n pudonneen 0.64 -> 0.55.)

Tulos 2026-09-28: muutos 30 pv sisalla heikentaa ennusteita koko vuoden yli
(log loss 0.690 vs 0.675, AUC 0.573 vs 0.616), mutta syyskuu EI ole
poikkeuksellinen: muutososuus 53 % (tavallinen) ja syyskuun AUC 0.597 on
vuoden keskitasoa. Syyskuun "romahdus" oli pienen ikkunan kohinaa + poikkeuksellisen
hyvan elokuun (0.658) vertailua.

team_rosters sisaltaa tarkat liittymis-/lahtopaivat (roster_transitions ei
kelpaa: paivamaarat puuttuvat). HUOM: taulussa on myos valmentajia/staffia
(ei roolikenttaa), mika lisaa kohinaa - tulos on siksi varovainen alaraja.

1) Kokoonpanotapahtumat kuukausittain 2026 (top75-joukkueet).
2) Tuotantomallin walk-forward-ennusteet top75-vs-top75: log loss ja AUC
   otteluissa joissa jommallakummalla joukkueella oli muutos viim. WINDOW
   paivan aikana vs. ei muutosta, koko 2026 ja kuukausittain.
Ei sovitettavia parametreja -> ei train/test-jakoa tarvita (puhdas diagnoosi).
"""
from __future__ import annotations

import sys
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from backtest import EloModel, deduplicate_matches, load_best_elo_params, load_clean_matches, log_loss, production_k_override  # noqa: E402
from db import get_connection  # noqa: E402
from run_xr_diagnostics import auc  # noqa: E402
from team_names import load_top50_names  # noqa: E402

WINDOWS = [14, 30, 60]


def roster_events(conn, top):
    ev = defaultdict(list)
    for team, j, l in conn.execute("SELECT team, join_date, leave_date FROM team_rosters"):
        for d in (j, l):
            if d and d >= "2025-06-01":
                try:
                    ev[team].append(datetime.fromisoformat(d).replace(tzinfo=timezone.utc))
                except ValueError:
                    pass
    for t in ev:
        ev[t].sort()
    return ev


def had_change(ev, team, date, days):
    lst = ev.get(team, [])
    return bisect_right(lst, date) - bisect_left(lst, date - timedelta(days=days)) > 0


def main() -> int:
    conn = get_connection()
    top = load_top50_names()
    ev = roster_events(conn, top)
    matches = deduplicate_matches(load_clean_matches(conn))
    conn.close()

    print("=== 1) Kokoonpanotapahtumat kuukausittain (top75, liittymiset + lahdot) ===")
    by_month = defaultdict(int)
    for t, lst in ev.items():
        if t in top:
            for d in lst:
                if d.year == 2026:
                    by_month[d.strftime("%Y-%m")] += 1
    print("  " + "  ".join(f"{m}:{n}" for m, n in sorted(by_month.items())))

    params = load_best_elo_params()
    elo = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
    recs = []
    for m in matches:
        if m.team in top and m.opponent in top and m.date.year == 2026:
            p = elo.predict(m.team, m.opponent, m.date)
            flags = {w: had_change(ev, m.team, m.date, w) or had_change(ev, m.opponent, m.date, w) for w in WINDOWS}
            recs.append((p, m.team_won, m.date.strftime("%Y-%m"), flags))
        elo.update(m.team, m.opponent, m.team_won, m.date,
                   k_override=production_k_override(params["k_factor"], m.match_type, m.team, m.opponent, top))

    print("\n=== 2) Tuotantomalli: muutos vs ei muutosta (koko 2026) ===")
    for w in WINDOWS:
        for flag in (True, False):
            g = [r for r in recs if r[3][w] == flag]
            ps, ys = [r[0] for r in g], [r[1] for r in g]
            print(f"  ikkuna {w:>2} pv, {'MUUTOS   ' if flag else 'ei muutos'}: n={len(g):4d}  "
                  f"log loss {log_loss(ys, ps):.4f}  AUC {auc(ps, ys):.3f}")

    print("\n=== 3) Kuukausittain (ikkuna 30 pv): osuus otteluista joissa muutos + tarkkuus ===")
    months = sorted({r[2] for r in recs})
    for mo in months:
        g = [r for r in recs if r[2] == mo]
        ch = [r for r in g if r[3][30]]
        nc = [r for r in g if not r[3][30]]

        def fmt(x):
            return f"AUC {auc([r[0] for r in x], [r[1] for r in x]):.3f} (n={len(x)})" if len(x) >= 10 else f"(n={len(x)})"
        print(f"  {mo}: muutos-osuus {len(ch) / len(g):5.1%} | kaikki AUC {auc([r[0] for r in g], [r[1] for r in g]):.3f} "
              f"| muutos {fmt(ch)} | ei muutos {fmt(nc)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
