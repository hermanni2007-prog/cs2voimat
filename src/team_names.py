"""
Joukkuenimien normalisointi top50_teams.json:n LYHYEEN kanoniseen nimeen.

LOYDETTY BUGI (2026-09-17, kayttajan pyytaessa lisaa puutteita mallista):
`historical_matches.opponent` ja `historical_maps.team1/team2` tallentavat
Liquipedian TAYDEN nayttonimen ("G2 Esports", "Team Spirit", "Aurora
Gaming"), kun taas `historical_matches.team` (oma joukkue, kun sen omaa
sivua kasitellaan) ja kaikki mallit (Elo, VrsRankings, deviaatiot) kayttavat
top50_teams.json:n LYHYTTA nimea ("G2", "Spirit", "Aurora"). Tama halkaisee
saman OIKEAN joukkueen KAHDEKSI ERI entiteetiksi mallissa - esim. G2:n
22 ottelua "G2 Esports" -nimella eivat koskaan paivita samaa Elo-ratingia
kuin G2:n omat ottelut "G2"-nimella. Vaikutti VAHINTAAN 24/50 top-joukkueesen
`historical_maps`:ssa (TARKISTETTU 2026-09-17: G2, FaZe, Spirit, Vitality,
Liquid, Falcons, Aurora, 9z, BC.Game, BetBoom, Betclic, DENDELE, FUT,
Luminosity, Lynn Vision, Nemesis, Nemiga, Sashi, fnatic, magic, paiN, jne).

Sama sanarajallinen fuzzy-resolute-logiikka kuin backtest.VrsRankings.
_fuzzy_resolve (todettu turvalliseksi jo kertaalleen - "am" ei enaa tasmaa
"team":n sisalla ilman sanarajaa) - jaettu talle uudelleenkaytettavaksi,
koska sama ongelma toistuu useassa eri paikassa (collect_history.py,
collect_maps.py, migraatioskripti)."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOP50_PATH = ROOT / "data" / "top50_teams.json"


def load_top50_names() -> set:
    data = json.loads(TOP50_PATH.read_text(encoding="utf-8"))
    return {t["name"] for t in data}


# Sanat jotka viittaavat ERI (vara-/nuoriso-)rosteriin saman organisaation
# alla - EI PIDA normalisoida paaroolituksen nimeen, koska pelaajat (ja
# siten oikea taso) ovat eri. Havaittu 2026-09-17: "B8 Academy" (!= B8),
# "G2 Ares" (!= G2), "FaZe Up Next" (!= FaZe), "Aurora Young Blud" (!=
# Aurora), "Team Spirit Academy" (!= Spirit), "The MongolZ Academy" (!=
# The MongolZ). Konservatiivinen: parempi jattaa yhdistamatta epavarma
# tapaus kuin yhdistaa vahingossa kaksi eri joukkuetta.
SUBTEAM_MARKERS = {"academy", "young", "blud", "reserve", "female", "next", "ares", "white", "black"}


def resolve_to_canonical(name: str, canonical_names: set) -> str:
    """Palauttaa canonical_names-joukosta parhaiten tasmaavan (lyhyimman)
    nimen, tai `name` sellaisenaan jos mikaan ei tasmaa (esim. joukkue joka
    ei ole top-50:ssa - ei muuteta, koska sille ei ole kanonista muotoa)."""
    if not name:
        return name
    if name in canonical_names:
        return name
    key = name.lower()
    words = set(re.findall(r"[a-z0-9]+", key))
    if words & SUBTEAM_MARKERS:
        return name  # todennakoisesti eri (vara-/nuoriso-)roosteri, ei yhdisteta
    candidates = []
    for full in canonical_names:
        full_l = full.lower()
        # min. 2 merkkia (ei 3): "G2", "B8", "9z" ovat lyhyita mutta
        # alfanumeerisina koodeina epatodennakoisia osumaan vahingossa
        # sanarajan sisalla - sanarajaehto (\b) on jo paasuoja vaaria
        # osumia vastaan (ks. VRS-bugikorjaus), ei pituus sinansa.
        if len(full_l) < 2 or len(key) < 2:
            continue
        if re.search(r"\b" + re.escape(full_l) + r"\b", key):
            candidates.append(full)
    if not candidates:
        return name
    candidates.sort(key=len)
    return candidates[0]
