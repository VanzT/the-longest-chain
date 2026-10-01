"""
Twilight Zone Chain — List Every Optimal (124-episode) Chain
=============================================================
Two steps:

STEP 1  (python3, needs OR-Tools: pip install ortools)
  Finds EVERY set of episodes that can form a longest chain, using the exact
  solver: find a solution, forbid that exact set of episodes, repeat until the
  solver proves nothing is left. Takes a few seconds. For the current data
  there are exactly 6 sets: 122 episodes are in every set, plus 2 of these 4:
  Time Enough at Last, A Game of Pool, Still Valley, The Incredible World of Horace Ford.
  Saves: tz_optimal_episode_sets.json

STEP 2  (python3, needs OR-Tools)
  For each episode set, the exact solver lists every chain that uses exactly
  those episodes, from the only possible start to the only possible end.
  Every chain is written as one line as it is found, so you can stop at any
  time (Ctrl+C) and keep what you have. (A plain walk, like pc6, gets lost
  here just as it did before; the solver's ceiling is what makes this work.)

Usage:
  python3 tz_enumerate_optimal.py --sets            # step 1 only (fast)
  python3 tz_enumerate_optimal.py --chains          # step 2, all 6 sets, one after another
  python3 tz_enumerate_optimal.py --chains --set 3  # only set 3 -> tz_all_optimal_chains_set3.txt
  python3 tz_enumerate_optimal.py --chains --max 100000 --time 3600
  python3 tz_enumerate_optimal.py --chains --count-only   # count, don't write

FASTEST ON THE RYZEN: listing uses one CPU core per run, so run the 6 sets at
once in 6 terminals (or in the background), each writing its own file:
  for k in 1 2 3 4 5 6; do python3 tz_enumerate_optimal.py --chains --set $k > set$k.log 2>&1 & done

Output line format (one chain per line):
  Episode | Actor | Episode | Actor | ... | Episode

IMPORTANT: the total may be very large (a 50-minute test found 15,235 chains
without finishing). Chains differ both in episode ORDER and in WHICH actor
bridges a pair of episodes that share more than one actor. Watch the file size;
use --count-only to just count.
"""

import argparse, csv, json, os, sys, time
from collections import defaultdict

INPUT_FILE  = "tz_cast_normalized.csv"
SETS_FILE   = "tz_optimal_episode_sets.json"
CHAINS_FILE = "tz_all_optimal_chains.txt"
sys.setrecursionlimit(20000)


def load(path):
    e2a, a2e = defaultdict(set), defaultdict(set)
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            a, e = row["actor"].strip(), row["episode_title"].strip()
            e2a[e].add(a); a2e[a].add(e)
    bridge = sorted(a for a, eps in a2e.items() if len(eps) >= 2)
    bset = set(bridge)
    episodes = sorted(e for e in e2a if e2a[e] & bset)
    return e2a, a2e, episodes, bridge


# ── STEP 1: every episode set that forms an optimal chain ───────────────────

def find_sets(workers):
    try:
        from ortools.sat.python import cp_model
    except ImportError:
        print("Step 1 needs OR-Tools under python3:  pip install ortools")
        sys.exit(1)
    e2a, a2e, episodes, bridge = load(INPUT_FILE)

    def build(min_len=None):
        m = cp_model.CpModel()
        eid = {e: i + 1 for i, e in enumerate(episodes)}
        aid = {a: len(episodes) + 1 + i for i, a in enumerate(bridge)}
        arcs, skip = [], {}
        def arc(u, v):
            l = m.NewBoolVar(""); arcs.append((u, v, l)); return l
        for e in episodes:
            u = eid[e]; arc(0, u); arc(u, 0); skip[e] = arc(u, u)
            for a in e2a[e]:
                if a in aid:
                    arc(u, aid[a]); arc(aid[a], u)
        for a in bridge:
            arc(aid[a], aid[a])
        m.AddCircuit(arcs)
        n = sum(skip[e].Not() for e in episodes)
        if min_len: m.Add(n == min_len)
        else: m.Maximize(n)
        return m, skip

    def solver():
        s = cp_model.CpSolver(); s.parameters.num_workers = workers
        s.parameters.max_time_in_seconds = 600
        return s

    # the optimum first
    m, skip = build()
    s = solver(); st = s.Solve(m)
    if s.StatusName(st) != "OPTIMAL":
        print("Could not prove the optimum:", s.StatusName(st)); sys.exit(1)
    best = int(s.ObjectiveValue())
    print(f"Longest possible chain: {best} episodes (proven)")

    m, skip = build(best)
    sets, t0 = [], time.time()
    while True:
        s = solver(); st = s.Solve(m)
        name = s.StatusName(st)
        if name == "INFEASIBLE":
            break
        if name not in ("OPTIMAL", "FEASIBLE"):
            print("Stopped without finishing:", name); break
        S = sorted(e for e in episodes if not s.Value(skip[e]))
        sets.append(S)
        m.Add(sum(skip[e].Not() for e in S) <= best - 1)   # forbid this exact set
        print(f"  episode set {len(sets)} found  ({time.time() - t0:.0f}s)")
    always = set.intersection(*map(set, sets)) if sets else set()
    print(f"\nComplete: {len(sets)} episode sets of {best} episodes.")
    print(f"  In every set: {len(always)} episodes")
    for i, S in enumerate(sets, 1):
        print(f"  Set {i}: also includes {sorted(set(S) - always)}")
    with open(SETS_FILE, "w", encoding="utf-8") as f:
        json.dump({"length": best, "sets": sets}, f, indent=1)
    print(f"Saved: {SETS_FILE}")


# ── STEP 2: every chain within each set (exact solver, listing every solution) ──

def list_chains(which, limit_time, limit_max, count_only):
    try:
        from ortools.sat.python import cp_model
    except ImportError:
        print("Step 2 needs OR-Tools under python3:  pip install ortools"); sys.exit(1)
    if not os.path.exists(SETS_FILE):
        print(f"{SETS_FILE} not found. Run step 1 first:  python3 {sys.argv[0]} --sets")
        sys.exit(1)
    data = json.load(open(SETS_FILE, encoding="utf-8"))
    sets = data["sets"]; L = data["length"]
    e2a, a2e, episodes, bridge = load(INPUT_FILE)
    always = set.intersection(*map(set, sets))

    class Writer(cp_model.CpSolverSolutionCallback):
        def __init__(self, arcs, name, out, start_count):
            super().__init__()
            self.arcs, self.name, self.out = arcs, name, out
            self.n, self.start_count, self.t0 = 0, start_count, time.time()
        def on_solution_callback(self):
            self.n += 1
            if not count_only:
                nxt = {u: v for (u, v, l) in self.arcs if u != v and self.Value(l)}
                chain, u = [], nxt[0]
                while u != 0:
                    chain.append(self.name[u]); u = nxt[u]
                self.out.write(" | ".join(chain) + "\n")
            if self.n % 1000 == 0:
                self.out.flush()
                print(f"\r    chains so far in this set: {self.n:,}   ({time.time() - self.t0:.0f}s)",
                      end="", flush=True)
            if limit_max and self.start_count + self.n >= limit_max:
                self.StopSearch()

    total, complete = 0, True
    for i, S in enumerate(sets, 1):
        if which and i != which:
            continue
        fname = os.devnull if count_only else (
            CHAINS_FILE if not which else CHAINS_FILE.replace(".txt", f"_set{i}.txt"))
        print(f"Set {i} (plus {sorted(set(S) - always)}) -> {'(counting only)' if count_only else fname}")
        Sset = set(S)
        m = cp_model.CpModel()
        eid = {e: k + 1 for k, e in enumerate(episodes)}
        aid = {a: len(episodes) + 1 + k for k, a in enumerate(bridge)}
        name = {v: k for k, v in eid.items()}; name.update({v: k for k, v in aid.items()})
        arcs = []
        def arc(u, v, fixed=None):
            l = m.NewBoolVar(""); arcs.append((u, v, l))
            if fixed is not None: m.Add(l == fixed)
            return l
        # the two ends: the episodes in this set with only one usable actor
        deg = {e: len({a for a in e2a[e] if len(a2e[a] & Sset) >= 2}) for e in S}
        ends = sorted(e for e in S if deg[e] == 1)
        start = ends[0]   # fix one direction so each chain is listed once
        for e in episodes:
            u = eid[e]
            arc(0, u, 1 if e == start else 0)
            arc(u, 0, 1 if (e in ends and e != start) else 0)
            arc(u, u, 0 if e in Sset else 1)
            for a in e2a[e]:
                if a in aid:
                    arc(u, aid[a]); arc(aid[a], u)
        for a in bridge:
            arc(aid[a], aid[a])
        m.AddCircuit(arcs)
        s = cp_model.CpSolver()
        s.parameters.enumerate_all_solutions = True
        s.parameters.num_workers = 1          # listing every solution needs one worker
        if limit_time: s.parameters.max_time_in_seconds = limit_time
        with open(fname, "w", encoding="utf-8") as out:
            w = Writer(arcs, name, out, total)
            try:
                st = s.Solve(m, w)
            except KeyboardInterrupt:
                st = None
        finished = st is not None and s.StatusName(st) in ("OPTIMAL", "INFEASIBLE") and not (
            limit_max and total + w.n >= limit_max)
        print(f"\r    chains in this set: {w.n:,} {'(COMPLETE)' if finished else '(stopped early)'}"
              f"   ({time.time() - w.t0:.0f}s)          ")
        total += w.n
        complete = complete and finished
        if st is None or (limit_max and total >= limit_max):
            break
    print(f"\nTotal chains of {L} episodes listed: {total:,} "
          f"{'(COMPLETE: this is every one)' if complete and not which else '(partial)'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", action="store_true", help="step 1: find all optimal episode sets")
    ap.add_argument("--chains", action="store_true", help="step 2: list every chain in those sets")
    ap.add_argument("--set", type=int, default=0, help="step 2: only this set number (1-based)")
    ap.add_argument("--time", type=float, default=0, help="step 2: seconds per set before stopping (0 = no limit)")
    ap.add_argument("--max", type=int, default=0, help="step 2: stop after this many chains in total")
    ap.add_argument("--count-only", action="store_true", help="step 2: count chains without writing them")
    ap.add_argument("--workers", type=int, default=8, help="step 1: solver threads")
    args = ap.parse_args()
    if not args.sets and not args.chains:
        ap.print_help(); sys.exit(0)
    if args.sets:
        find_sets(args.workers)
    if args.chains:
        list_chains(args.set, args.time, args.max, args.count_only)
