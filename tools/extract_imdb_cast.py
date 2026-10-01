"""
Extract an episode/actor cast list for any TV series from IMDb's free datasets
===============================================================================
Produces a two-column CSV (actor, episode_title) in the format every program in
this project reads. Use it to try the longest-chain puzzle on another show.

1. Download these four files from https://datasets.imdbws.com/ into one folder
   (they are large, roughly 1 GB compressed in total; leave them compressed):
     title.basics.tsv.gz    title.episode.tsv.gz
     title.principals.tsv.gz    name.basics.tsv.gz
   IMDb licenses this data for personal and non-commercial use only. See
   https://data.imdb.com/non-commercial-datasets before sharing anything
   you make from it.

2. Find the series' IMDb ID: it's the "tt..." part of the show's IMDb web address.
     The Twilight Zone (1959)    tt0052520
   (Look up Night Gallery, The Outer Limits, etc. the same way.)

3. Run (python3 or pypy3; standard library only):
     python3 tools/extract_imdb_cast.py tt0052520 --out my_show_cast.csv
     python3 tools/extract_imdb_cast.py tt0052520 --exclude "Rod Serling"

Notes:
  - IMDb's "principals" file lists only the top-billed cast of each episode
    (usually about ten people). The Twilight Zone dataset in this project was
    also merged with guest-star lists from Wikipedia and hand-corrected, so this
    script alone will not reproduce tz_cast_normalized.csv exactly. To rebuild
    the Twilight Zone list, use tools/twilight_zone_data.py build.
  - Only actors/actresses (and "self" appearances) are kept, not crew.
  - Hosts and narrators usually should be excluded (--exclude), or they can link
    episodes that share no actual cast.
  - Run tools/normalize_cast.py afterwards to merge spelling variants and
    stage names.
"""

import argparse, csv, gzip, os, sys


def find(folder, name):
    """IMDb file in folder: name.tsv.gz, or the unzipped name.tsv."""
    for n in (name + ".tsv.gz", name + ".tsv"):
        if os.path.exists(os.path.join(folder, n)):
            return os.path.join(folder, n)
    return None


def rows(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", newline="") as f:
        header = f.readline().rstrip("\n").split("\t")
        for line in f:
            yield dict(zip(header, line.rstrip("\n").split("\t")))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("series", help="IMDb series ID, e.g. tt0052520")
    ap.add_argument("--dir", default=".", help="folder containing the four .tsv.gz files")
    ap.add_argument("--out", default="cast.csv", help="output CSV (default: cast.csv)")
    ap.add_argument("--exclude", action="append", default=[], help="person to leave out (repeatable)")
    args = ap.parse_args()

    need = ["title.episode", "title.basics", "title.principals", "name.basics"]
    files = {n: find(args.dir, n) for n in need}
    missing = [n + ".tsv.gz" for n, f in files.items() if not f]
    if missing:
        sys.exit(f"Missing {', '.join(missing)} in {os.path.abspath(args.dir)}\n"
                 f"Download them from https://datasets.imdbws.com/ into that folder "
                 f"(or point --dir / --imdb-dir at the folder that has them).")

    print("1/4 finding episodes...")
    episodes = {r["tconst"] for r in rows(files["title.episode"])
                if r.get("parentTconst") == args.series}
    if not episodes:
        sys.exit(f"No episodes found for {args.series}. Check the ID.")
    print(f"    {len(episodes)} episodes")

    print("2/4 reading episode titles...")
    title = {r["tconst"]: r["primaryTitle"] for r in rows(files["title.basics"])
             if r["tconst"] in episodes}

    print("3/4 reading cast...")
    keep = {"actor", "actress", "self"}
    links = set()
    for r in rows(files["title.principals"]):
        if r["tconst"] in episodes and r.get("category") in keep:
            links.add((r["nconst"], r["tconst"]))
    people = {n for n, _ in links}

    print("4/4 reading names...")
    name = {r["nconst"]: r["primaryName"] for r in rows(files["name.basics"])
            if r["nconst"] in people}

    excluded = set(args.exclude)
    out = sorted({(name.get(n, n), title.get(t, t)) for n, t in links})
    out = [(a, e) for a, e in out if a not in excluded]
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["actor", "episode_title"])
        w.writerows(out)

    per_actor = {}
    for a, e in out:
        per_actor.setdefault(a, set()).add(e)
    bridges = sum(1 for s in per_actor.values() if len(s) >= 2)
    print(f"Wrote {args.out}: {len(out)} rows, {len({e for _, e in out})} episodes, "
          f"{len(per_actor)} people, {bridges} who appear in 2+ episodes (possible bridges)")


if __name__ == "__main__":
    main()
