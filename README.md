# The Longest Chain in The Twilight Zone

**How many episodes of the original *Twilight Zone* can you string together, where each
episode shares an actor with the next, without using any episode or any linking actor twice?**

**The answer is 124, and it's proven: no longer chain exists** (for the cast data in this
repository).

This puzzle started during a Twilight Zone marathon in 2011. The first programs to attack it
reached 60 episodes. Fifteen years later, with an AI collaborator, months of searching on a
Raspberry Pi and a series of faster machines pushed the record to 120. Then a completely
different tool, an integer-programming solver, found a 124-episode chain and proved that
nothing longer exists, in about one second.

The whole story is told in the book *Finding the Longest Chain* (link to come).

## The rules

- Episodes are linked by an actor who appears in both.
- No episode may appear twice.
- No actor may be used as a link twice.
- Find the longest chain.

## Quick start: reproduce the answer

IMDb licenses its data for personal and non-commercial use, so this repository doesn't
include the finished cast list. You rebuild it from your own copy of IMDb's free files:

1. Download `title.basics.tsv.gz`, `title.episode.tsv.gz`, `title.principals.tsv.gz` and
   `name.basics.tsv.gz` from https://datasets.imdbws.com/ into one folder (about 1 GB;
   leave them compressed).
2. Build the cast list and solve:

```bash
pip install ortools
python3 tools/twilight_zone_data.py build --imdb-dir <folder with the IMDb files>
python3 tz_solve_optimal.py
```

The build step writes `tz_cast_normalized.csv` and checks that it matches the book's list
exactly (581 rows).

In a second or two it prints a 124-episode chain, checks every link against the data,
and reports `PROVEN OPTIMAL: no chain longer than 124 episodes exists in this dataset.`
The chain is saved to `tz_chain_optimal.txt`.

A second, independent proof (a different model, solved by SCIP):

```bash
python3 tz_verify_optimal_mip.py
```

## What we know about the answer

- The longest chain has **124 of the 145 reachable episodes**.
- **Every** 124-episode chain runs from *I Shot an Arrow into the Air* to
  *Number 12 Looks Just Like You* (or the reverse).
- **No** longest chain can include *Night Call* or *Queen of the Nile*, even though most of
  the long chains found by searching ended at *Night Call*.
- There are exactly **6 possible sets** of episodes a longest chain can use
  (`results/tz_optimal_episode_sets.json`).
- There are a **lot** of different longest chains: an estimated 6 quadrillion
  (6 × 10^15), and at the very least trillions.

## The files

**Proof and counting** (python3 + OR-Tools)

| File | What it does |
|---|---|
| `tz_solve_optimal.py` | Finds the longest chain and proves it optimal (CP-SAT). `--prove 125` just asks whether a 125+ chain exists. |
| `tz_verify_optimal_mip.py` | Independent second proof with a different model and solver (SCIP). |
| `tz_enumerate_optimal.py` | Step 1 `--sets`: every set of episodes a longest chain can use (seconds; writes `tz_optimal_episode_sets.json`, needed by step 2). Step 2 `--chains`: lists longest chains one per line. Warning: there are quadrillions; it will fill your disk long before it finishes. |
| `tz_estimate_count.py` | Estimates how many longest chains exist without listing them (Knuth's random-path estimator). |

**Search** (pypy3 recommended; `pip install blessed` for the display)

| File | What it does |
|---|---|
| `tz_solver_pc6.py` | The exhaustive depth-first search that ran for months: pruning, early exit, pendant correction, bitmasks, 44,189 parallel branches, hourly checkpoints. It would prove the answer if it ever finished. It never did. |
| `tz_explorer.py` | Randomized explorer: many fast, weighted-random dives looking for long chains. Can't prove anything, finds records quickly. Set `NUM_WORKERS` and pinning at the top. |
| `tz_improve_chain.py` | "Detour" polish: tries to lengthen a finished chain by rerouting short stretches through unused episodes. This is how 119 became 120. |
| `inject_chain_pc6.py` | Gives the solver and explorer a head start by loading a known chain into the checkpoint. |
| `tz_monitor.py` | Emails you when the solver finds a new record or finishes a branch. Fill in your Gmail address and an App Password first. (Ours never sent a single email.) |

**Data and tools**

| File | What it is |
|---|---|
| `tools/twilight_zone_data.py` | Rebuilds the Twilight Zone cast list (`tz_cast_normalized.csv`, `actor,episode_title`) from your IMDb files and checks it. See [DATA.md](DATA.md). |
| `data/` | What the rebuild adds to IMDb's files: the actors kept, appearances from Wikipedia and hand checking, and a few IMDb rows left out. |
| `tools/extract_imdb_cast.py` | Builds a cast list for **any** TV series from IMDb's free datasets. |
| `tools/normalize_cast.py` | Merges cast lists and fixes spelling variants and stage names. |
| `tools/aliases_twilight_zone.csv` | The corrections used for this project. |

**Results** (`results/`)

| File | Chain |
|---|---|
| `tz_chain_124.txt` | A proven-longest chain, 124 episodes |
| `tz_chain_120.txt` | Best found by searching: the spliced 119 plus one detour through *Walking Distance* |
| `tz_chain_119_splice.txt` | Found by accident: a copying mistake in the project's notes that turned out to be a valid 119 |
| `tz_chain_119_queen_of_the_nile.txt` | Found by James's Mac. The first chain without *Night Call* |
| `tz_chain_118_pi.txt` | The Raspberry Pi's 118, after about 7 days |
| `tz_chain_118_thinkcentre.txt` | The ThinkCentre's 118, in 12 minutes |
| `tz_optimal_episode_sets.json` | The 6 episode sets a longest chain can use |

## Why was one second enough, after months of searching?

"NP-hard" (which this problem is) describes the **worst case**: no known method is fast on
every possible puzzle of this kind. This particular puzzle is a friendly one. Of 213 linking
actors, 163 appear in exactly two episodes, and 21 episodes have only one usable actor, so
they can only be the first or last episode of a chain. That alone caps any chain at
145 − 19 = 126 episodes. The integer-programming solver does that kind of counting across
the whole graph at once, and for this graph its ceiling is already 124. The search programs
used a much looser ceiling ("how many episodes can I still reach?"), so they had to wander
through astronomically many near-misses before ruling anything out.

## Your turn

The solver doesn't know anything about the Twilight Zone. Give it any `actor,episode_title`
list and it will find, and prove, the longest chain for that show:

```bash
python3 tools/extract_imdb_cast.py <IMDb series ID> --exclude "<host name>" --out tz_cast_normalized.csv
python3 tz_solve_optimal.py
```

(This overwrites the Twilight Zone file; you can always rebuild it. Or edit `INPUT_FILE` at the top of the script.)
The hard part is the data, not the math: see [DATA.md](DATA.md). And a show that reuses the
same actors over and over may be a much tougher puzzle.

*Night Gallery*? *The Outer Limits*? *Alfred Hitchcock Presents*? *Black Mirror*?
The Twilight Zone's answer is 124. What's theirs?

## Credits

- The puzzle, the persistence, and the early programs (AutoIt, then Python on a Raspberry Pi):
  the author.
- James, sounding board since the beginning, whose Mac found the Queen of the Nile 119.
- Most of the code in this repository was written with an AI assistant (Anthropic's Claude),
  directed, tested and run by the author.
- Cast data from IMDb's non-commercial datasets (rebuilt by each user, not redistributed) and Wikipedia (see DATA.md).
