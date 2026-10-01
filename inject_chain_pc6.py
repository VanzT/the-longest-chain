"""
Inject a known chain into the pc6 solver checkpoint
===================================================
Gives tz_solver_pc6.py (and tz_explorer.py) a head start: the solver's pruning
bar is raised to the injected chain's length before it starts, so it only
spends time on branches that could beat it.

Usage (from the folder containing the CSV and the checkpoint):
  1. Stop the solver and the explorer (Ctrl+C). Both read and write
     tz_checkpoint_pc6.json, so don't leave either running.
  2. pypy3 inject_chain_pc6.py results/tz_chain_120.txt      (python3 works too)
  3. pypy3 tz_solver_pc6.py

Accepts any of the project's chain text files (numbered episode lines with
"└─[ Actor ]" lines between them).

What it does:
  1. Checks the chain against tz_cast_normalized.csv (every name exists, every
     link is valid, no repeated episodes or actors). If anything fails, it stops
     without touching any file.
  2. Backs up the existing checkpoint (tz_checkpoint_pc6.json.bak-YYYYMMDD-HHMMSS).
  3. Writes the chain into the checkpoint, keeping completed_branches,
     total_calls and every other field exactly as they were.
  4. Writes tz_best_chain_pc6.txt so the monitor and the solver display show it.

It will NOT replace a longer chain already in the checkpoint (--force overrides).

For fun: inject the 124 minus its last episode (a 123) and watch the solver try
to find the one ending that completes it. It can never do better than 124.
"""

import csv, json, os, re, sys, time
from collections import defaultdict

CHECKPOINT_FILE = "tz_checkpoint_pc6.json"
BEST_CHAIN_FILE = "tz_best_chain_pc6.txt"
INPUT_FILE      = "tz_cast_normalized.csv"
FORCE           = "--force" in sys.argv
ARGS            = [a for a in sys.argv[1:] if a != "--force"]


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


if len(ARGS) != 1:
    print("Usage: pypy3 inject_chain_pc6.py CHAIN_FILE [--force]")
    sys.exit(1)
BEST_CHAIN = read_chain(ARGS[0])
if len(BEST_CHAIN) < 3 or len(BEST_CHAIN) % 2 == 0:
    print(f"ERROR: could not read a chain from {ARGS[0]}.")
    sys.exit(1)


# ── 1. Verify the chain against the data ─────────────────────────────────────

def verify_chain(chain):
    if not os.path.exists(INPUT_FILE):
        print(f"ERROR: {INPUT_FILE} not found. Run this from the solver folder.")
        sys.exit(1)
    ep_actors = defaultdict(set)
    all_actors = set()
    with open(INPUT_FILE, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ep_actors[row["episode_title"].strip()].add(row["actor"].strip())
            all_actors.add(row["actor"].strip())

    episodes, actors = chain[0::2], chain[1::2]
    problems = []
    for e in episodes:
        if e not in ep_actors:
            problems.append(f"unknown episode: {e!r}")
    for a in actors:
        if a not in all_actors:
            problems.append(f"unknown actor: {a!r}")
    if len(set(episodes)) != len(episodes):
        problems.append("an episode repeats")
    if len(set(actors)) != len(actors):
        problems.append("a bridging actor repeats")
    if not problems:
        for i, a in enumerate(actors):
            if a not in ep_actors[episodes[i]] or a not in ep_actors[episodes[i + 1]]:
                problems.append(f"bad link {i + 1}: {episodes[i]!r} -[{a}]- {episodes[i + 1]!r}")
    if problems:
        print("ERROR: chain failed verification. Nothing was changed.")
        for p in problems:
            print("  -", p)
        sys.exit(1)
    print(f"Chain verified against {INPUT_FILE}: "
          f"{len(episodes)} episodes, {len(actors)} distinct bridging actors, all links valid.")


verify_chain(BEST_CHAIN)
new_len = (len(BEST_CHAIN) + 1) // 2


# ── 2. Load and back up the existing checkpoint ──────────────────────────────

data = {}
if os.path.exists(CHECKPOINT_FILE):
    try:
        with open(CHECKPOINT_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"ERROR: could not read {CHECKPOINT_FILE}: {e}")
        print("Nothing was changed. Fix or move the file, then run again.")
        sys.exit(1)

    old_chain = data.get("best_chain", [])
    old_len   = (len(old_chain) + 1) // 2 if old_chain else 0
    print(f"Existing checkpoint found:")
    print(f"  Best chain:         {old_len} episodes")
    print(f"  Completed branches: {len(data.get('completed_branches', []))}")
    print(f"  Prior calls:        {data.get('total_calls', 0):,}")

    if old_len > new_len and not FORCE:
        print(f"\nThe checkpoint already holds a LONGER chain ({old_len} episodes).")
        print("Nothing was changed. (Run with --force to overwrite anyway.)")
        sys.exit(0)

    backup = f"{CHECKPOINT_FILE}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    with open(backup, "w", encoding="utf-8") as f:
        json.dump(data, f)
    print(f"  Backup written:     {backup}")
else:
    print(f"No existing {CHECKPOINT_FILE} — creating a fresh one.")


# ── 3. Write the checkpoint (all other fields preserved) ────────────────────

data["best_chain"] = BEST_CHAIN
data.setdefault("completed_branches", [])
data.setdefault("total_calls", 0)
data["saved_at"] = time.time()

tmp = CHECKPOINT_FILE + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f)
os.replace(tmp, CHECKPOINT_FILE)


# ── 4. Best-chain text file (same format pc6 writes) ─────────────────────────

episodes, actors = BEST_CHAIN[0::2], BEST_CHAIN[1::2]
lines = [
    "TWILIGHT ZONE LONGEST EPISODE CHAIN  [PC Edition v6]",
    "==================================================",
    f"Episodes in chain : {len(episodes)}",
    "Time elapsed      : 0s",
    f"DFS calls         : {data.get('total_calls', 0):,}",
    "Workers used      : (injected by inject_chain_pc6.py)",
    "",
    "Each episode links to the next via the named actor.",
    "No episode or bridging actor repeats.",
    "",
]
for i, ep in enumerate(episodes):
    lines.append(f"{i + 1:>3}. {ep}")
    if i < len(actors):
        lines.append(f"       └─[ {actors[i]} ]")
tmp = BEST_CHAIN_FILE + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
os.replace(tmp, BEST_CHAIN_FILE)

print(f"\nCheckpoint updated!")
print(f"  Best chain injected:          {new_len} episodes")
print(f"  Completed branches preserved: {len(data['completed_branches'])}")
print(f"  Best chain file written:      {BEST_CHAIN_FILE}")
print(f"\nNow run: pypy3 tz_solver_pc6.py")
print(f"The solver will prune at {new_len} from the very first call "
      f"(it only explores branches that could reach {new_len + 1}+).")
