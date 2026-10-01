"""
Clean up and merge cast lists
==============================
Merges one or more (actor, episode_title) CSV files, applies name and title
corrections, removes excluded people, and writes one de-duplicated CSV.

Why it matters: to a computer, "Ross Elliot" and "Ross Elliott" are two
different people, each in one episode, so neither can link anything. Every
missed match silently deletes a link from the puzzle.

Usage:
  python3 tools/normalize_cast.py imdb_cast.csv wiki_cast.csv --out cast_normalized.csv
  python3 tools/normalize_cast.py cast.csv --aliases tools/aliases_twilight_zone.csv

Aliases file: a CSV with columns  kind,variant,canonical  where kind is
"actor", "title" or "exclude" (for exclude, only the variant column is used).
tools/aliases_twilight_zone.csv holds the corrections used for this project.
"""

import argparse, csv
from collections import defaultdict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--aliases", default=None)
    ap.add_argument("--out", default="cast_normalized.csv")
    args = ap.parse_args()

    actor_map, title_map, exclude = {}, {}, set()
    if args.aliases:
        with open(args.aliases, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                k, v, c = r["kind"].strip(), r["variant"].strip(), (r.get("canonical") or "").strip()
                if k == "actor":
                    actor_map[v] = c
                elif k == "title":
                    title_map[v] = c
                elif k == "exclude":
                    exclude.add(v)

    rows = set()
    for path in args.inputs:
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                a = r["actor"].strip()
                e = r["episode_title"].strip()
                a = actor_map.get(a, a)
                e = title_map.get(e, e)
                if a and e and a not in exclude:
                    rows.add((a, e))

    rows = sorted(rows)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["actor", "episode_title"])
        w.writerows(rows)

    eps = defaultdict(set)
    for a, e in rows:
        eps[a].add(e)
    print(f"Wrote {args.out}: {len(rows)} rows, {len({e for _, e in rows})} episodes, "
          f"{len(eps)} actors, {sum(len(s) >= 2 for s in eps.values())} bridge actors")


if __name__ == "__main__":
    main()
