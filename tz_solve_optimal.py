"""
Twilight Zone Chain — Exact Solver (constraint programming)
============================================================
Finds the longest possible chain AND proves no longer chain exists, using
Google OR-Tools CP-SAT. On 2026-09-27 this found a 124-episode chain and proved
124 optimal in under one second on 2 CPU cores.

How it works (plain version):
  Every episode and every bridge actor is a "stop". There is one extra stop,
  the DEPOT, which ties the end of the chain back to its start, turning the
  chain into a loop. The solver must build a single loop through the depot:
    depot -> episode -> actor -> episode -> actor -> ... -> episode -> depot
  Moves are only allowed between an episode and an actor who appears in it,
  so every step is a legal bridge. Every stop other than the depot may be
  skipped. Each stop is visited at most once, so no episode and no bridging
  actor can repeat. The solver maximizes the number of episodes visited.

  CP-SAT combines smart search with linear-programming bounds. The bounds are
  how it proves nothing longer exists without trying every chain.

Requirements:
  pip install ortools          (use python3; OR-Tools does not run under PyPy)

Usage:
  python3 tz_solve_optimal.py                 # solve, write tz_chain_optimal.txt
  python3 tz_solve_optimal.py --prove 125     # just ask: does a 125+ chain exist?
  python3 tz_solve_optimal.py --workers 16 --time 600

Output: tz_chain_optimal.txt (same format as the other chain files).
Every chain it writes is re-checked against the CSV independently.
"""

import argparse, csv, sys, time
from collections import defaultdict

try:
    from ortools.sat.python import cp_model
except ImportError:
    print("OR-Tools is not installed. Run:  pip install ortools   (then use python3)")
    sys.exit(1)

INPUT_FILE  = "tz_cast_normalized.csv"
OUTPUT_FILE = "tz_chain_optimal.txt"


def load(path):
    e2a, a2e = defaultdict(set), defaultdict(set)
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            a, e = row["actor"].strip(), row["episode_title"].strip()
            e2a[e].add(a); a2e[a].add(e)
    bridge = sorted(a for a, eps in a2e.items() if len(eps) >= 2)
    bset = set(bridge)
    episodes = sorted(e for e in e2a if e2a[e] & bset)   # drop episodes with no bridge actor
    return e2a, a2e, episodes, bridge


def build_model(e2a, episodes, bridge, min_len=None):
    m = cp_model.CpModel()
    eid = {e: i + 1 for i, e in enumerate(episodes)}                 # 0 = depot
    aid = {a: len(episodes) + 1 + i for i, a in enumerate(bridge)}
    arcs, skip = [], {}

    def arc(u, v):
        lit = m.NewBoolVar(f"a{u}_{v}")
        arcs.append((u, v, lit))
        return lit

    for e in episodes:
        u = eid[e]
        arc(0, u); arc(u, 0)                 # chain may start / end here
        skip[u] = arc(u, u)                  # self-loop = episode not used
        for a in e2a[e]:
            if a in aid:
                arc(u, aid[a]); arc(aid[a], u)
    for a in bridge:
        arc(aid[a], aid[a])                  # actor not used as a bridge
    m.AddCircuit(arcs)

    n_eps = sum(skip[eid[e]].Not() for e in episodes)
    if min_len:
        m.Add(n_eps >= min_len)
    else:
        m.Maximize(n_eps)
    return m, arcs, eid, aid


def extract_chain(solver, arcs, eid, aid):
    nxt = {u: v for (u, v, lit) in arcs if u != v and solver.Value(lit)}
    name = {v: k for k, v in eid.items()}
    name.update({v: k for k, v in aid.items()})
    chain, u = [], nxt[0]
    while u != 0:
        chain.append(name[u]); u = nxt[u]
    return chain


def verify(chain, e2a):
    eps, acts = chain[0::2], chain[1::2]
    return (len(set(eps)) == len(eps) and len(set(acts)) == len(acts) and
            all(acts[i] in e2a[eps[i]] and acts[i] in e2a[eps[i + 1]]
                for i in range(len(acts))))


def write_chain(chain, status, secs):
    eps, acts = chain[0::2], chain[1::2]
    lines = ["TWILIGHT ZONE LONGEST EPISODE CHAIN  [Exact solver, OR-Tools CP-SAT]",
             "=" * 68,
             f"Episodes in chain : {len(eps)}",
             f"Solver status     : {status}"
             + ("  (proven: no longer chain exists in this dataset)" if status == "OPTIMAL" else ""),
             f"Solve time        : {secs:.1f}s",
             f"Data              : {INPUT_FILE}", "",
             "Each episode links to the next via the named actor.",
             "No episode or bridging actor repeats.", ""]
    for i, e in enumerate(eps):
        lines.append(f"{i + 1:>3}. {e}")
        if i < len(acts):
            lines.append(f"       └─[ {acts[i]} ]")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--time", type=float, default=3600, help="time limit in seconds")
    ap.add_argument("--prove", type=int, default=None,
                    help="only test whether a chain of at least this many episodes exists")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    e2a, a2e, episodes, bridge = load(INPUT_FILE)
    print(f"Reachable episodes: {len(episodes)}   Bridge actors: {len(bridge)}")
    model, arcs, eid, aid = build_model(e2a, episodes, bridge, args.prove)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = args.time
    solver.parameters.num_workers = args.workers
    solver.parameters.log_search_progress = not args.quiet
    t0 = time.time()
    status = solver.StatusName(solver.Solve(model))
    secs = time.time() - t0

    print("\n" + "=" * 60)
    if args.prove:
        if status == "INFEASIBLE":
            print(f"PROVEN: no chain of {args.prove} or more episodes exists.  ({secs:.1f}s)")
        elif status in ("FEASIBLE", "OPTIMAL"):
            chain = extract_chain(solver, arcs, eid, aid)
            print(f"FOUND a chain of {len(chain)//2 + 1} episodes (>= {args.prove}).")
            if verify(chain, e2a):
                write_chain(chain, status, secs); print(f"Saved to {OUTPUT_FILE}")
        else:
            print(f"Undecided within the time limit (status {status}).")
    else:
        if status in ("OPTIMAL", "FEASIBLE"):
            chain = extract_chain(solver, arcs, eid, aid)
            n = len(chain) // 2 + 1
            ok = verify(chain, e2a)
            print(f"Status: {status}   Chain: {n} episodes   Upper bound: {solver.BestObjectiveBound():.0f}"
                  f"   Time: {secs:.1f}s")
            print(f"Independent check against the CSV: {'PASSED' if ok else 'FAILED'}")
            if status == "OPTIMAL":
                print(f"PROVEN OPTIMAL: no chain longer than {n} episodes exists in this dataset.")
            if ok:
                write_chain(chain, status, secs); print(f"Saved to {OUTPUT_FILE}")
        else:
            print(f"No solution within the time limit (status {status}).")
    print("=" * 60)
