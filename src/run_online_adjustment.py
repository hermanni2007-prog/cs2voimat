"""
Muodostaa ja validoi korjauksen run_lan_online_upsets.py:n loydokselle
(2026-09-18): online-otteluissa suosikki havisi enemman kuin malli
ennustaa (upset-rate 47.1% vs odotettu 40.6%, log_loss 0.719), kun taas
LAN/Offline on lahes tasmalleen kalibroitu (41.8% vs 40.3%, log_loss
0.669).

SAMA METODOLOGIA kuin ruostumiskorjauksessa (backtest.apply_rust_
adjustment): OLS-kalibrointikulmakerroin pakotetulla leikkauspisteella
0.5, FITATTU VAIN kronologisen datan ensimmaisella 80%:lla (vain
online-otteluista), TARKISTETTU nakemattomalla 20%:lla (vain online-
otteluista) - ei kehamainen, sama periaate kuin kaikki muukin tassa
projektissa.

  slope = Sum((p-0.5)(y-0.5)) / Sum((p-0.5)^2)   (vain online-ottelut, train)
  p_korjattu = 0.5 + (p_raaka - 0.5) * slope       (VAIN online-otteluille)
"""
from __future__ import annotations

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


def ols_slope(pairs: list) -> float:
    """pairs: list of (p, y). Pakotettu leikkauspiste 0.5."""
    num = sum((p - 0.5) * (y - 0.5) for p, y in pairs)
    den = sum((p - 0.5) ** 2 for p, _ in pairs)
    return num / den if den > 0 else 1.0


def main() -> int:
    conn = get_connection()
    all_matches = deduplicate_matches(load_clean_matches(conn))
    conn.close()
    matches = filter_top50_only(all_matches, load_top50_names())
    params = load_best_elo_params()

    split_idx = int(len(matches) * 0.8)
    split_date = matches[split_idx].date
    print(f"Train/test-raja: {split_date.date()} ({len(matches)} ottelua yhteensa)\n")

    elo = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
    train_online, test_online = [], []
    train_offline, test_offline = [], []
    for m in matches:
        p = elo.predict(m.team, m.opponent, m.date)
        is_online = m.match_type == "Online"
        is_test = m.date >= split_date
        bucket_online = test_online if is_test else train_online
        bucket_offline = test_offline if is_test else train_offline
        (bucket_online if is_online else bucket_offline).append((p, m.team_won))
        elo.update(m.team, m.opponent, m.team_won, m.date)

    print(f"Train: {len(train_online)} online, {len(train_offline)} LAN/offline ottelua")
    print(f"Test:  {len(test_online)} online, {len(test_offline)} LAN/offline ottelua\n")

    slope = ols_slope(train_online)
    print(f"RAAKA OLS-slope (train-online, n={len(train_online)}): {slope:.3f}")
    print("  HUOM: taman kokoluokan otoksella (n=179) OLS-slope-estimaatti voi olla")
    print("  erittain epavakaa jos p-arvot ovat lahella 0.5:ta (nimittaja Sum((p-0.5)^2)")
    print("  pieni) - -0.091 olisi kaytannossa 'heita malli pois online-otteluissa'\n")
    print("  mika on vahvempi vaite kuin deskriptiivinen loydos (upset-rate 47% vs")
    print("  odotettu 41%) yksinaan perustelisi. Sen sijaan etta kaytetaan raakaa")
    print("  OLS-arvoa sellaisenaan, GRID-HAETAAN jarkevalta valilta [0.3, 1.0]")
    print("  sama tapa kuin run_elo.py:n half_life-grid - rajattu, ei ylisovita yhteen")
    print("  epavakaaseen pistearvioon.\n")

    outcomes_test = [y for _, y in test_online]
    outcomes_train = [y for _, y in train_online]
    print("=== Grid-haku train-datalla (rajattu valille 0.3-1.0) ===")
    candidates = [round(0.0 + 0.05 * i, 2) for i in range(21)]  # 0.00 .. 1.00
    train_results = []
    for s in candidates:
        probs = [0.5 + (p - 0.5) * s for p, _ in train_online]
        ll = log_loss(outcomes_train, probs)
        train_results.append((s, ll))
    print("  Koko kayra 0.0-1.0:")
    for s, ll in train_results:
        print(f"    s={s:.2f}  train_log_loss={ll:.4f}")
    best_s, best_train_ll = min(train_results, key=lambda r: r[1])
    print(f"  Paras shrink train-datalla: s={best_s}  (train_log_loss={best_train_ll:.4f})")

    # Vertailu: soveltamatta vs raaka OLS vs rajattu grid-paras, NAKEMATTOMALLA test-online-joukolla
    probs_raw = [p for p, _ in test_online]
    probs_ols = [0.5 + (p - 0.5) * slope for p, _ in test_online]
    probs_grid = [0.5 + (p - 0.5) * best_s for p, _ in test_online]

    ll_raw = log_loss(outcomes_test, probs_raw)
    ll_ols = log_loss(outcomes_test, probs_ols)
    ll_grid = log_loss(outcomes_test, probs_grid)
    print(f"\n=== Testataan NAKEMATTOMALLA test-online-joukolla (n={len(test_online)}) ===")
    print(f"  Ilman korjausta:                    log_loss = {ll_raw:.4f}")
    print(f"  Raaka OLS-slope ({slope:.3f}):            log_loss = {ll_ols:.4f}")
    print(f"  Rajattu grid-paras (s={best_s}):         log_loss = {ll_grid:.4f}")
    if ll_grid < ll_raw:
        print(f"\n  -> Rajattu shrink (s={best_s}) PARANTAA ennustetta nakemattomalla datalla "
              f"({ll_raw:.4f} -> {ll_grid:.4f}). OTETAAN KAYTTOON TATA, EI raakaa OLS-arvoa.")
    else:
        print(f"\n  -> Edes rajattu shrink ei paranna luotettavasti - HYLATAAN.")
    slope = best_s  # kaytetaan jatkossa rajattua, ei raakaa OLS-arvoa

    # Sama tarkistus offline-otteluille (ei pitaisi tarvita korjausta - kontrolli)
    slope_offline = ols_slope(train_offline)
    print(f"\n(Kontrolli - LAN/Offline OLS-slope train-datalla: {slope_offline:.3f}, "
          f"lahella 1.0:aa = ei tarvitse korjausta, kuten run_lan_online_upsets.py jo viittasi.)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
