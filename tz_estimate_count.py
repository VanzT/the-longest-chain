"""
Twilight Zone Chain — Estimate How Many Optimal Chains Exist
=============================================================
Listing every 124-episode chain may take forever. This estimates the total
instead, using a classic technique for counting things too numerous to list
(Knuth's random-path estimator):

  1. Start at the only possible first episode.
  2. At each step, ask the exact solver which next moves can still be
     completed into a full optimal chain. Pick one of them at random.
  3. Multiply together the number of good options seen at every step.
     That product is one unbiased estimate of the total number of chains.
  4. Repeat many times and average. The spread gives a margin of error.

Because the solver prunes every dead end, each walk ends in a real optimal
chain, so no walk is wasted.

Requirements: python3 + OR-Tools (pip install ortools). Not PyPy.

Usage:
  python3 tz_estimate_count.py                  # 40 walks, 4 parallel processes
  python3 tz_estimate_count.py --walks 200 --procs 14
Results are appended to tz_estimate_walks.txt so you can stop (Ctrl+C) and
resume later; the summary always uses every walk recorded so far.
"""
import argparse, csv, math, os, random, sys, time
from collections import defaultdict
from multiprocessing import Pool

INPUT_FILE = "tz_cast_normalized.csv"
WALKS_FILE = "tz_estimate_walks.txt"

def load():
    e2a, a2e = defaultdict(set), defaultdict(set)
    with open(INPUT_FILE, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            a, e = r["actor"].strip(), r["episode_title"].strip()
            e2a[e].add(a); a2e[a].add(e)
    bridge = sorted(a for a, eps in a2e.items() if len(eps) >= 2)
    bset = set(bridge)
    episodes = sorted(e for e in e2a if e2a[e] & bset)
    return e2a, a2e, episodes, bridge

_G = {}
def _init(length, start):
    from ortools.sat.python import cp_model
    e2a, a2e, episodes, bridge = load()
    m = cp_model.CpModel()
    eid = {e: i + 1 for i, e in enumerate(episodes)}
    aid = {a: len(episodes) + 1 + i for i, a in enumerate(bridge)}
    arcs, lit, skip = [], {}, {}
    def arc(u, v):
        l = m.NewBoolVar(""); arcs.append((u, v, l)); lit[(u, v)] = l; return l
    for e in episodes:
        u = eid[e]; arc(0, u); arc(u, 0); skip[u] = arc(u, u)
        for a in e2a[e]:
            if a in aid: arc(u, aid[a]); arc(aid[a], u)
    for a in bridge: arc(aid[a], aid[a])
    m.AddCircuit(arcs)
    m.Add(sum(l.Not() for l in skip.values()) == length)
    m.Add(lit[(0, eid[start])] == 1)             # fix the start (and so the direction)
    ep_act = {eid[e]: [aid[a] for a in e2a[e] if a in aid] for e in episodes}
    act_ep = {aid[a]: [eid[e] for e in a2e[a] if e in eid] for a in bridge}
    _G.update(m=m, lit=lit, eid=eid, ep_act=ep_act, act_ep=act_ep, cp=cp_model,
              end_ids=set())

def _feasible(prefix_lits):
    cp = _G["cp"]; m = _G["m"]
    m.ClearAssumptions(); m.AddAssumptions(prefix_lits)
    s = cp.CpSolver(); s.parameters.num_workers = 1; s.parameters.max_time_in_seconds = 120
    st = s.Solve(m)
    if st == cp.UNKNOWN: raise RuntimeError("feasibility check timed out")
    return st in (cp.OPTIMAL, cp.FEASIBLE)

def one_walk(seed):
    rnd = random.Random(seed)
    lit, ep_act, act_ep = _G["lit"], _G["ep_act"], _G["act_ep"]
    start = _G["start_id"]
    prefix = [lit[(0, start)]]; visited = {start}; used = set(); cur = start
    log_est = 0.0; steps = 0; checks = 0; t0 = time.time()
    while True:
        cands = [(a, e) for a in ep_act[cur] if a not in used
                 for e in act_ep[a] if e not in visited]
        good = []
        if len(cands) == 1:
            good = cands                 # the only continuation of a feasible prefix
        else:
            for (a, e) in cands:
                checks += 1
                if _feasible(prefix + [lit[(cur, a)], lit[(a, e)]]): good.append((a, e))
        # The model requires exactly LENGTH episodes, so a chain can only end
        # once every one is used; then there are no candidates left.
        if not good:
            break
        if len(good) > 1: log_est += math.log(len(good))
        a, e = good[rnd.randrange(len(good))]
        prefix += [lit[(cur, a)], lit[(a, e)]]; used.add(a); visited.add(e); cur = e; steps += 1
    return log_est, steps + 1, checks, time.time() - t0

def _init_worker(length, start):
    _init(length, start)
    _G["start_id"] = _G["eid"][start]

def summarize(logs):
    n = len(logs)
    mx = max(logs)
    mean = mx + math.log(sum(math.exp(x - mx) for x in logs) / n)     # log of the mean
    # standard error of the mean, in log space via relative error
    ws = [math.exp(x - mx) for x in logs]; mu = sum(ws) / n
    var = sum((w - mu) ** 2 for w in ws) / max(n - 1, 1)
    rel_se = math.sqrt(var / n) / mu if mu > 0 else float("inf")
    return mean, rel_se

def fmt(logv):
    v = math.exp(logv)
    if v < 1e6: return f"{v:,.0f}"
    e = int(math.floor(math.log10(v))); return f"{v / 10**e:.2f} x 10^{e}  ({v:,.0f})"

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--walks", type=int, default=40)
    ap.add_argument("--procs", type=int, default=4)
    ap.add_argument("--length", type=int, default=124)
    ap.add_argument("--start", default="I Shot an Arrow into the Air")
    args = ap.parse_args()
    done = []
    if os.path.exists(WALKS_FILE):
        done = [float(l.split()[0]) for l in open(WALKS_FILE) if l.strip()]
        print(f"Resuming: {len(done)} walks already recorded in {WALKS_FILE}")
    t0 = time.time()
    seeds = [random.randrange(1 << 30) for _ in range(args.walks)]
    with Pool(args.procs, initializer=_init_worker, initargs=(args.length, args.start)) as pool:
        try:
            for i, (lg, steps, checks, dt) in enumerate(pool.imap_unordered(one_walk, seeds), 1):
                done.append(lg)
                with open(WALKS_FILE, "a") as f: f.write(f"{lg}\t{steps}\t{checks}\t{dt:.1f}\n")
                mean, rse = summarize(done)
                print(f"walk {i}/{args.walks}: this walk ~{fmt(lg)} | running estimate {fmt(mean)} "
                      f"(+/- {100 * rse:.0f}%)  [{time.time() - t0:.0f}s]", flush=True)
        except KeyboardInterrupt:
            print("\nStopped; walks so far are saved.")
    if done:
        mean, rse = summarize(done)
        lo = math.log(max(math.exp(mean) * (1 - 2 * rse), 1)) if rse < 0.5 else None
        print("\n" + "=" * 64)
        print(f"Walks used: {len(done)}")
        print(f"Estimated number of optimal ({args.length}-episode) chains: {fmt(mean)}")
        print(f"Margin of error (1 standard error): +/- {100 * rse:.0f}%")
        print("Individual walks vary hugely (that is normal for this method); the")
        print("estimate tightens as more walks are added.")
        print("=" * 64)
