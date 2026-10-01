"""
Twilight Zone Chain Improver — local "detour" search
=====================================================
Takes a finished chain and tries to lengthen it. For every stretch of up to
MAX_WINDOW consecutive links, it searches for a LONGER route between the same
two endpoint episodes, using only:
  - episodes not used elsewhere in the chain, and
  - bridge actors not used elsewhere in the chain.
If it finds one, it splices it in, verifies the result, and starts again on the
longer chain. It repeats until no stretch can be improved.

This is how the 120-episode chain was found: in the spliced 119-chain, the link
  Mute --[Bill Erwin]--> Will the Real Martian Please Stand Up?
became
  Mute --[Frank Overton]--> Walking Distance --[Bill Erwin]--> Will the Real Martian...

It cannot prove optimality. It is a fast polish step (well under a second per
chain at MAX_WINDOW = 8) that exhaustive DFS and the explorer don't do.

Usage:
  pypy3 tz_improve_chain.py tz_chain_119_splice.txt
  pypy3 tz_improve_chain.py tz_best_chain_explorer.txt 12      (optional window size)

Accepts any of the project's chain text formats (numbered episode lines with
"└─[ Actor ]" or "-[ Actor ]" lines between them).
Output: <input name>_improved.txt, written only if the chain got longer.
"""

import csv, re, sys, os, time
from collections import defaultdict

INPUT_FILE = "tz_cast_normalized.csv"
MAX_WINDOW = 8     # longest stretch (in links) to try rerouting
EXTRA_LINKS = 3    # a detour may be up to this many links longer than the stretch


def load_data(input_file):
    raw_ep_to_actors = defaultdict(set)
    raw_actor_to_eps = defaultdict(set)
    with open(input_file, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            actor = row["actor"].strip()
            ep    = row["episode_title"].strip()
            raw_ep_to_actors[ep].add(actor)
            raw_actor_to_eps[actor].add(ep)
    bridge = {a for a, eps in raw_actor_to_eps.items() if len(eps) >= 2}
    ep_names    = sorted(raw_ep_to_actors)
    actor_names = sorted(bridge)
    e2i = {n: i for i, n in enumerate(ep_names)}
    a2i = {n: i for i, n in enumerate(actor_names)}
    ep_actors = [0] * len(ep_names)
    actor_eps = [0] * len(actor_names)
    for ep, actors in raw_ep_to_actors.items():
        for a in actors:
            if a in bridge:
                ep_actors[e2i[ep]]  |= 1 << a2i[a]
                actor_eps[a2i[a]]   |= 1 << e2i[ep]
    return ep_names, actor_names, ep_actors, actor_eps, e2i, a2i


def read_chain(path):
    names = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            m = re.match(r"^\d+\.\s+(.*)$", s)
            if m:
                names.append(m.group(1).strip())
                continue
            m = re.match(r"^(?:└─|-)\[\s*(.*?)\s*\]$", s)
            if m:
                names.append(m.group(1).strip())
    return names


def verify(ids, ep_actors):
    eps, acts = ids[0::2], ids[1::2]
    if len(set(eps)) != len(eps) or len(set(acts)) != len(acts):
        return False
    return all((ep_actors[eps[i]] >> acts[i]) & 1 and
               (ep_actors[eps[i + 1]] >> acts[i]) & 1 for i in range(len(acts)))


def find_detour(ids, i, j, ep_actors, actor_eps):
    """Longer route from eps[i] to eps[j] than the current j-i links, or None."""
    eps, acts = ids[0::2], ids[1::2]
    k = j - i
    used_e = 0
    for t, e in enumerate(eps):
        if t <= i or t >= j:
            used_e |= 1 << e
    used_a = 0
    for t, a in enumerate(acts):
        if t < i or t >= j:
            used_a |= 1 << a
    target = eps[j]
    limit = k + EXTRA_LINKS
    result = []

    def dfs(cur, ve, va, path, depth):
        av = ep_actors[cur] & ~va
        while av:
            lb = av & -av; av ^= lb
            a = lb.bit_length() - 1
            if depth + 1 > k and (actor_eps[a] >> target) & 1:
                result.append(path + [a, target])
                return True
            if depth + 1 >= limit:
                continue
            nx = actor_eps[a] & ~ve & ~(1 << target)
            while nx:
                lb2 = nx & -nx; nx ^= lb2
                e = lb2.bit_length() - 1
                if dfs(e, ve | lb2, va | lb, path + [a, e], depth + 1):
                    return True
        return False

    dfs(eps[i], used_e, used_a, [eps[i]], 0)
    return result[0] if result else None


def improve_once(ids, ep_actors, actor_eps, max_window):
    n_eps = len(ids[0::2])
    for k in range(1, max_window + 1):
        for i in range(0, n_eps - k):
            j = i + k
            path = find_detour(ids, i, j, ep_actors, actor_eps)
            if path:
                new = ids[:2 * i] + path + ids[2 * j + 1:]
                if verify(new, ep_actors):
                    return new, (i, j)
    return None, None


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    chain_file = sys.argv[1]
    max_window = int(sys.argv[2]) if len(sys.argv) > 2 else MAX_WINDOW

    ep_names, actor_names, ep_actors, actor_eps, e2i, a2i = load_data(INPUT_FILE)
    names = read_chain(chain_file)
    missing = [n for k, n in enumerate(names)
               if (e2i if k % 2 == 0 else a2i).get(n) is None]
    if missing:
        print("ERROR: names not found in dataset:", missing)
        sys.exit(1)
    ids = [(e2i if k % 2 == 0 else a2i)[n] for k, n in enumerate(names)]
    if not verify(ids, ep_actors):
        print("ERROR: input chain is not valid against", INPUT_FILE)
        sys.exit(1)

    start_len = len(ids[0::2])
    print(f"Loaded {chain_file}: {start_len} episodes (valid)")
    t0 = time.time()
    while True:
        new, where = improve_once(ids, ep_actors, actor_eps, max_window)
        if not new:
            break
        i, j = where
        before = ep_names[ids[2 * i]], ep_names[ids[2 * j]]
        gain = len(new[0::2]) - len(ids[0::2])
        ids = new
        print(f"  +{gain} -> {len(ids[0::2])} episodes "
              f"(rerouted between '{before[0]}' and '{before[1]}')")
    final_len = len(ids[0::2])
    print(f"Done in {time.time() - t0:.1f}s: {start_len} -> {final_len} episodes "
          f"(max window {max_window})")

    if final_len > start_len:
        out = os.path.splitext(chain_file)[0] + "_improved.txt"
        eps, acts = ids[0::2], ids[1::2]
        lines = ["TWILIGHT ZONE EPISODE CHAIN  [Improved by tz_improve_chain.py]",
                 "=" * 62,
                 f"Episodes in chain : {final_len}",
                 f"Source            : {chain_file} ({start_len} episodes)", "",
                 "Each episode links to the next via the named actor.",
                 "No episode or bridging actor repeats.", ""]
        for n, e in enumerate(eps):
            lines.append(f"{n + 1:>3}. {ep_names[e]}")
            if n < len(acts):
                lines.append(f"       └─[ {actor_names[acts[n]]} ]")
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"Saved: {out}")
    else:
        print("No improvement found; nothing written.")
