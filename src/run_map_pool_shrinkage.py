"""
Karttakohtainen "partial pooling" (empiirinen Bayes -kutistus) -piirre
(sovittu jatkotyo 2026-09-20: kayttajan idea "get map-based elo systems
correct" - TAMA on se versio joka EI vaadi lisadataa, ks. muisti
project_model_improvement_roadmap.md kohta 2).

EROTUS aiemmin HYLATTYYN run_map_level_elo.py:hyn: tuo paivitti YHTA
kokonaisratingia jokaisesta yksittaisesta kartasta (rejected, karttatason
kohina huonompi kuin sarjatason signaali). TASSA sen sijaan pidetaan
tuotannon sarjatason Elo koskemattomana PERUSTANA ja lisataan PAALLE pieni,
otoskoolla painotettu poikkeama joka kertoo onko joukkue ERITYISEN
hyva/huono JUURI TALLA kartalla suhteessa omaan yleistasoonsa - klassinen
"jotkut ryhmat on dataa 60, jotkut 5" -ongelman ratkaisu (James-Stein/
empiirinen Bayes), ei kova n>=X-raja (kuten decider-kokeilussa) vaan
JATKUVA kutistus: pieni n -> lahes 0 poikkeama, iso n -> lahes raaka
karttakohtainen voitto-%.

METODOLOGINEN HUOMIO (rehellisyys): "mitka kartat pelattiin TASSA
sarjassa" TIEDETAAN Liquipedian veto-prosessissa jo ENNEN ottelua (ei
jalkikateen), joten taman kayttaminen SARJAN ennusteessa EI ole
kehapaatelma SAMALLA TAVALLA kuin "meniko deciserille" olisi (se selviaa
vasta ottelun aikana). MUTTA: emme viela KERAA reaaliaikaista veto-dataa
(ks. aiempi keskustelu) - tama testi kayttaa TODELLISUUDESSA PELATTUJA
karttoja korvikkeena "veto olisi paljastanut nama" -oletukselle. Jos tama
osoittautuu hyodylliseksi, tuotantoon vieminen vaatii VIELA erillisen
live-veto-datalahteen - tata EI ole viela rakennettu.

METODOLOGIA: sama kuin muut - kaikki vapaat parametrit (prior_strength,
gamma) grid-haetaan VAIN train-sarjoilla, validointi NAKEMATTOMALLA
test-sarjajoukolla, TASMALLEEN sama test-sarjajoukko kuin run_map_level_
elo.py:n (A)-mallissa jotta vertailu on reilu."""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime
from math import exp, log
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from backtest import (  # noqa: E402
    EloModel,
    deduplicate_matches,
    load_clean_matches,
    load_best_elo_params,
    log_loss,
    production_k_override,
)
from team_names import load_top50_names, resolve_to_canonical  # noqa: E402

PRIOR_STRENGTH_CANDIDATES = [1, 2, 3, 5, 8, 12, 20, 35, 60, 100, 100000]
GAMMA_CANDIDATES = [round(-3.0 + 0.25 * i, 2) for i in range(25)]  # -3.0 .. 3.0


def ll(pairs):
    if not pairs:
        return float("nan")
    return log_loss([y for _, y in pairs], [p for p, _ in pairs])


def logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return log(p / (1 - p))


def sigmoid(x):
    if x >= 0:
        return 1.0 / (1.0 + exp(-x))
    ez = exp(x)
    return ez / (1.0 + ez)


def load_map_series_detailed(conn, canonical_names: set) -> list:
    """Kuten run_map_level_elo.load_map_series, mutta sailyttaa map_name:n
    (tarpeen tassa, koska kartan IDENTITEETTI on koko pointti - alkuperainen
    versio pudotti sen koska sille riitti pelkka voitto/havio jarjestyksessa)."""
    rows = conn.execute(
        """SELECT match_date_utc, team1, team2, map_order, map_name, team1_score, team2_score
           FROM historical_maps
           WHERE team1_score IS NOT NULL AND team2_score IS NOT NULL
           ORDER BY match_date_utc, map_order"""
    ).fetchall()

    groups: dict = {}
    for date_s, t1, t2, map_order, map_name, s1, s2 in rows:
        t1c = resolve_to_canonical(t1, canonical_names)
        t2c = resolve_to_canonical(t2, canonical_names)
        key = (date_s, frozenset({t1c, t2c}))
        g = groups.setdefault(key, {"date_s": date_s, "team1": t1c, "team2": t2c, "maps": {}})
        map_key = (map_order, t1c, t2c)
        if map_key not in g["maps"] and s1 != s2:
            g["maps"][map_key] = (map_name, s1 > s2)  # (kartan nimi, voittiko team1)

    out = []
    for key, g in groups.items():
        entries = list(g["maps"].values())
        if not entries:
            continue
        team1_wins = sum(1 for _, w in entries if w)
        if team1_wins == len(entries) - team1_wins:
            continue  # tasapeli - ei kayteta
        winner = g["team1"] if team1_wins > len(entries) - team1_wins else g["team2"]
        out.append({
            "date": datetime.fromisoformat(g["date_s"]),
            "team1": g["team1"], "team2": g["team2"], "winner": winner,
            "maps_played": [m for m, _ in entries],  # kartat jotka TASSA sarjassa pelattiin
            "map_entries": entries,  # (map_name, team1_voitti) per kartta - kaytetaan tallennukseen
        })
    out.sort(key=lambda s: s["date"])
    return out


def main() -> int:
    conn = get_connection()
    canonical_names = load_top50_names()
    series_list = load_map_series_detailed(conn, canonical_names)
    series_list = [s for s in series_list if s["team1"] in canonical_names and s["team2"] in canonical_names]

    match_list = deduplicate_matches(load_clean_matches(conn))
    conn.close()
    top50 = canonical_names

    print(f"Karttadatasta johdettuja sarjoja (top75-vs-top75): {len(series_list)}")

    split_idx = int(len(series_list) * 0.8)
    split_date = series_list[split_idx]["date"]
    print(f"Train/test-raja (sarjadatan oma): {split_date.date()}  "
          f"(train={split_idx}, test={len(series_list)-split_idx})\n")

    # --- (A) Perusta: tuotannon sarjatason Elo, KOSKEMATON ---
    params = load_best_elo_params()
    eval_keys = {(s["date"], s["team1"], s["team2"]) for s in series_list}
    eval_keys |= {(s["date"], s["team2"], s["team1"]) for s in series_list}

    elo = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
    base_p_lookup = {}
    for m in match_list:
        p = elo.predict(m.team, m.opponent, m.date)
        if (m.date, m.team, m.opponent) in eval_keys:
            base_p_lookup[(m.date, frozenset({m.team, m.opponent}))] = (p, m.team)
        k_override = production_k_override(params["k_factor"], m.match_type, m.team, m.opponent, top50)
        elo.update(m.team, m.opponent, m.team_won, m.date, k_override=k_override)

    # --- Kronologinen per-joukkue-per-kartta ja yleis-voitto-% -kirjanpito ---
    map_record = defaultdict(lambda: [0, 0])     # (team, map_name) -> [voitot, haviot]
    overall_record = defaultdict(lambda: [0, 0])  # team -> [voitot, haviot] (kaikki kartat)

    def overall_rate(team):
        w, l = overall_record[team]
        n = w + l
        return w / n if n else 0.5

    def shrunk_map_rate(team, map_name, prior_strength):
        w, l = map_record[(team, map_name)]
        n = w + l
        prior_mean = overall_rate(team)
        return (w + prior_strength * prior_mean) / (n + prior_strength)

    records = []  # (base_p_team1, maps_played, team1, team2, y, is_test)
    for s in series_list:
        key = (s["date"], frozenset({s["team1"], s["team2"]}))
        if key not in base_p_lookup:
            continue
        p_row, row_team = base_p_lookup[key]
        base_p_team1 = p_row if row_team == s["team1"] else (1 - p_row)
        y = 1 if s["winner"] == s["team1"] else 0
        is_test = s["date"] >= split_date
        # HUOM: map_diff lasketaan MYOHEMMIN (grid-tasolla), tassa vain
        # tallennetaan raaka-ainekset (kartat + niiden hetkiset tilastot)
        # NIMENOMAAN ENNEN taman sarjan paivitysta (walk-forward).
        maps_data = []
        for map_name in set(s["maps_played"]):
            w1, l1 = map_record[(s["team1"], map_name)]
            w2, l2 = map_record[(s["team2"], map_name)]
            maps_data.append((map_name, w1, l1, overall_rate(s["team1"]), w2, l2, overall_rate(s["team2"])))
        records.append((base_p_team1, maps_data, y, is_test))

        # Nyt paivitetaan kirjanpito TAMAN sarjan karttatuloksilla (walk-forward: seuraava
        # sarja nakee taman, tama sarja EI nahnyt itseaan).
        for map_name, team1_won in s["map_entries"]:
            if team1_won:
                map_record[(s["team1"], map_name)][0] += 1
                map_record[(s["team2"], map_name)][1] += 1
                overall_record[s["team1"]][0] += 1
                overall_record[s["team2"]][1] += 1
            else:
                map_record[(s["team1"], map_name)][1] += 1
                map_record[(s["team2"], map_name)][0] += 1
                overall_record[s["team1"]][1] += 1
                overall_record[s["team2"]][0] += 1

    n_test = sum(1 for r in records if r[3])
    print(f"Ennuste loytyi {len(records)}/{len(series_list)} sarjalle (test-osuus n={n_test})\n")

    def map_diff_for(maps_data, prior_strength):
        devs1, devs2 = [], []
        for map_name, w1, l1, rate1, w2, l2, rate2 in maps_data:
            n1, n2 = w1 + l1, w2 + l2
            shrunk1 = (w1 + prior_strength * rate1) / (n1 + prior_strength)
            shrunk2 = (w2 + prior_strength * rate2) / (n2 + prior_strength)
            devs1.append(shrunk1 - rate1)
            devs2.append(shrunk2 - rate2)
        d1 = sum(devs1) / len(devs1) if devs1 else 0.0
        d2 = sum(devs2) / len(devs2) if devs2 else 0.0
        return d1 - d2

    def adjusted_p(base_p, maps_data, prior_strength, gamma):
        diff = map_diff_for(maps_data, prior_strength)
        return sigmoid(logit(base_p) + gamma * diff)

    print("=== Grid-haku train-datalla (prior_strength x gamma) ===")
    best = None
    for ps in PRIOR_STRENGTH_CANDIDATES:
        for gamma in GAMMA_CANDIDATES:
            train_pairs = [(adjusted_p(bp, md, ps, gamma), y) for bp, md, y, is_test in records if not is_test]
            tll = ll(train_pairs)
            if best is None or tll < best[2]:
                best = (ps, gamma, tll)
    best_ps, best_gamma, best_tll = best
    print(f"Paras (train): prior_strength={best_ps}  gamma={best_gamma}  train_log_loss={best_tll:.4f}")

    base_train_pairs = [(bp, y) for bp, md, y, is_test in records if not is_test]
    print(f"(vertailu) perusta ilman karttatietoa (train):  log_loss={ll(base_train_pairs):.4f}\n")

    print("=== Testataan NAKEMATTOMALLA test-sarjajoukolla ===")
    base_test = [(bp, y) for bp, md, y, is_test in records if is_test]
    adj_test = [(adjusted_p(bp, md, best_ps, best_gamma), y) for bp, md, y, is_test in records if is_test]
    print(f"(A) Perusta (ei karttatietoa):        log_loss={ll(base_test):.4f}  (n={len(base_test)})")
    print(f"(C) Kartta-kutistus (ps={best_ps}, gamma={best_gamma}):  log_loss={ll(adj_test):.4f}  (n={len(adj_test)})")

    if ll(adj_test) < ll(base_test):
        print(f"\n-> Kartta-kutistus PARANTAA nakemattomalla datalla ({ll(base_test):.4f} -> {ll(adj_test):.4f}).")
        print("   HUOM: tama kayttaa TODELLISUUDESSA PELATTUJA karttoja korvikkeena veto-tiedolle -")
        print("   tuotantoon vienti vaatii viela oikean live-veto-datalahteen (ei viela olemassa).")
    else:
        print(f"\n-> EI paranna nakemattomalla datalla ({ll(base_test):.4f} -> {ll(adj_test):.4f}) - EI oteta kayttoon.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
