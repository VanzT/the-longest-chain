"""
Second, independent proof of the optimum (different model, different solver: SCIP).
Usage: python3 tz_verify_optimal_mip.py      (needs: pip install ortools)
Independent check: degree-constrained MIP + lazy loop-elimination cuts (SCIP).
x[e,a]=1 if the link episode e -- actor a is used. Each used actor has exactly 2 links,
each used episode has 2 links (or 1 if it is one of the 2 chain ends). Loops that are
disconnected from the chain are forbidden by adding cuts x(E(S)) <= |S|-1 and re-solving."""
import csv, sys, time
from collections import defaultdict
from ortools.linear_solver import pywraplp
e2a=defaultdict(set);a2e=defaultdict(set)
for r in csv.DictReader(open("tz_cast_normalized.csv")):
    e2a[r["episode_title"].strip()].add(r["actor"].strip()); a2e[r["actor"].strip()].add(r["episode_title"].strip())
B={a for a,e in a2e.items() if len(e)>=2}
E=sorted(e for e in e2a if e2a[e]&B); A=sorted(B)
s=pywraplp.Solver.CreateSolver("SCIP")
x={(e,a):s.BoolVar("") for e in E for a in e2a[e]&B}
y={e:s.BoolVar("") for e in E}; z={a:s.BoolVar("") for a in A}; t={e:s.BoolVar("") for e in E}
for a in A: s.Add(sum(x[e,a] for e in a2e[a]) == 2*z[a])
for e in E:
    s.Add(sum(x[e,a] for a in e2a[e]&B) == 2*y[e]-t[e]); s.Add(t[e]<=y[e])
s.Add(sum(t.values())==2)
s.Maximize(sum(y.values()))
it=0; t0=time.time()
while True:
    it+=1
    st=s.Solve()
    if st!=pywraplp.Solver.OPTIMAL: print("status",st); break
    used=[k for k,v in x.items() if v.solution_value()>0.5]
    adj=defaultdict(set)
    for e,a in used: adj[("E",e)].add(("A",a)); adj[("A",a)].add(("E",e))
    seen=set(); comps=[]
    for n in adj:
        if n in seen: continue
        st_=[n]; c={n}; seen.add(n)
        while st_:
            u=st_.pop()
            for w in adj[u]:
                if w not in seen: seen.add(w); c.add(w); st_.append(w)
        comps.append(c)
    loops=[c for c in comps if not any(n[0]=="E" and t[n[1]].solution_value()>0.5 for n in c)]
    obj=round(s.Objective().Value())
    print(f"iter {it}: objective {obj}, pieces {len(comps)}, disconnected loops {len(loops)}  ({time.time()-t0:.0f}s)",flush=True)
    if not loops:
        print(f"RESULT: single chain, {obj} episodes, proven optimal by this formulation"); break
    for c in loops:
        S=set(c)
        s.Add(sum(x[e,a] for (e,a) in x if ("E",e) in S and ("A",a) in S) <= len(S)-1)
