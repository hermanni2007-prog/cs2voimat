"""Online-kutistus (ONLINE_SHRINK) rolling originilla tuotantomallilla (karttatason Elo), 2026-10-01.
TULOS: 491/1415 top-vs-top-sarjaa online, testissa 268. Train-sovitettu s = 0.15-0.40 (ikkunat).
Test log loss: s_fit 0.6892, ei kutistusta 0.6911, 50/50 (nykyinen s=0) 0.6931.
Malli vs 50/50: -0.0020, 95 % [-0.026, +0.023], P(malli parempi) 0.57 -> heikko signaali, nykyinen s=0 liian jyrkka."""
import sys,math,random;sys.path.insert(0,'src')
from backtest import deduplicate_matches,load_clean_matches
from db import get_connection
from team_names import load_top50_names
from xr_model import load_map_games
from run_format_map_model import walk,attach_maps,series_shape,PROD_MAP_K,cal,fit_tau,SPLITS
conn=get_connection();top=load_top50_names()
ms=deduplicate_matches(load_clean_matches(conn));games=load_map_games(conn,top)
series,_=walk(ms,attach_maps(ms,games),top,PROD_MAP_K,0.0)
types=[m.match_type for m in ms if series_shape(m) and m.team in top and m.opponent in top]
assert len(types)==len(series)
rows=[(p,y,t) for (p,_,y,_),t in zip(series,types)]
n=len(rows);S=[i/20 for i in range(21)]
def ll(r): return sum(-math.log(min(max(p if y else 1-p,1e-9),1)) for p,y in r)/len(r)
shr=lambda p,s:0.5+(p-0.5)*s
cuts=[int(n*f) for f in SPLITS]+[n]
tot={'s_fit':[], 's=1':[], 's=0':[]};print("online yht",sum(t=='Online' for *_,t in rows),"/",n)
for i in range(5):
    tr=rows[:cuts[i]];te=rows[cuts[i]:cuts[i+1]]
    tau=fit_tau([(p,y) for p,y,_ in tr])
    tro=[(cal(p,tau),y) for p,y,t in tr if t=='Online'];teo=[(cal(p,tau),y) for p,y,t in te if t=='Online']
    s=min(S,key=lambda s:ll([(shr(p,s),y) for p,y in tro]))
    r={k:ll([(shr(p,v),y) for p,y in teo]) for k,v in (('s_fit',s),('s=1',1),('s=0',0))}
    for k in tot: tot[k]+= [(shr(p,{'s_fit':s,'s=1':1,'s=0':0}[k]),y) for p,y in teo]
    print(f"ikkuna {i+1}: train-online {len(tro)}, s={s:.2f} | test-online {len(teo)}: s_fit {r['s_fit']:.4f}  ei kutistusta {r['s=1']:.4f}  50/50 {r['s=0']:.4f}")
for k,v in tot.items(): print(k,f"{ll(v):.4f}",len(v))
# bootstrap: ei kutistusta vs 50/50
a=[(-math.log(p if y else 1-p))-(math.log(2)) for p,y in tot['s=1']]
random.seed(0);bs=sorted(sum(random.choice(a) for _ in a)/len(a) for _ in range(4000))
print("LL(malli)-LL(50/50):",f"{sum(a)/len(a):+.4f}","95%",f"[{bs[100]:+.4f},{bs[3900]:+.4f}]","P(malli parempi)",sum(x<0 for x in bs)/4000)
