"""EV-rajan tutkimus oikeilla kertoimilla (2026-10-02, kayttajan kysymys: 10-20 % vai yli 15 %?).
Vain havaitut kertoimet, molemmilla vahintaan 10 ottelua, walk-forward. Ajetaan uudelleen kun kerroinlokissa
on enemman tuloksia.
TULOS 2.10. (82 kartta / 78 vanha +EV-vetoa): ei selvaa rajaa, kaikki 95 % valit noin +-40 %.
Karttamallilla EV < 10 % -vedot (n=26) havisivat -41 %/veto; vanhalla ei samaa kuviota.
Tulos nojaa muutamaan yli 4.0 kertoimen voittoon."""
import sys,random;sys.path.insert(0,'src')
from datetime import datetime
import backtest_vs_real_odds_map as B
from backtest import deduplicate_matches,load_clean_matches,load_best_elo_params
from odds_log import load_logged_bets
conn=B.get_connection();top=B.load_top50_names()
ms=deduplicate_matches(load_clean_matches(conn));gs=B.load_map_games(conn,top);P=load_best_elo_params()
logged,_=load_logged_bets(ms)
R={'kartta':[],'vanha':[]}
for b in B.BETS+logged:
    d=datetime.fromisoformat(b["date"]);pr=[m for m in ms if m.date<d];pg=[g for g in gs if g.date<d]
    bo,_=B.best_of_for(b,ms);po,n=B.old_prob(b,pr,top,P);pm=B.map_prob(b,pr,pg,top,bo)
    for k,p in (('kartta',pm),('vanha',po)):
        for desc,ev,prof,won,est in B.value_bets(b,p):
            if not est and n>=10: R[k].append((ev,prof,float(desc.split('@')[1].split()[0])))
random.seed(1)
def boot(x):
    s=sorted(sum(random.choice(x) for _ in x)/len(x) for _ in range(3000));return s[75],s[2925]
for k,rows in R.items():
    print(f"\n== {k}: {len(rows)} +EV-vetoa (vain havaitut kertoimet, min 10 ottelua) ==")
    print("EV-vali       n   voitot  ROI/veto")
    for lo,hi in ((0,.05),(.05,.10),(.10,.15),(.15,.20),(.20,.30),(.30,.50),(.50,9)):
        x=[p for e,p,_ in rows if lo<e<=hi]
        if x: print(f"{lo:>4.0%}-{hi if hi<9 else 0:>4.0%}  {len(x):3}  {sum(p>0 for p in x):3}   {sum(x)/len(x):+.0%}")
    print("Raja (EV >)   n   ROI/veto  95% vali")
    for t in (0,.05,.10,.15,.20,.30):
        x=[p for e,p,_ in rows if e>t]
        if len(x)>3: lo,hi=boot(x);print(f"  >{t:>4.0%}     {len(x):3}  {sum(x)/len(x):+6.0%}   [{lo:+.0%}, {hi:+.0%}]")
    print("Kerroinluokka  n   ROI/veto")
    for lo,hi in ((1,1.8),(1.8,2.5),(2.5,4),(4,99)):
        x=[p for e,p,k_ in rows if lo<=k_<hi]
        if x: print(f"  {lo}-{hi if hi<99 else ''}  {len(x):3}  {sum(x)/len(x):+.0%}")
