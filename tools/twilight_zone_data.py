"""
Rebuild the Twilight Zone cast list (tz_cast_normalized.csv)
=============================================================
IMDb licenses its datasets for personal and non-commercial use, so this
repository does not include the finished cast list. Instead you rebuild it on
your own computer from your own copy of IMDb's free files. It takes a minute.

  1. Download these four files from https://datasets.imdbws.com/ into one folder
     (about 1 GB in total; leave them compressed):
       title.basics.tsv.gz   title.episode.tsv.gz
       title.principals.tsv.gz   name.basics.tsv.gz

  2. From the top folder of this repository, run:
       python3 tools/twilight_zone_data.py build --imdb-dir <that folder>

  3. It writes tz_cast_normalized.csv and checks that it matches, row for row,
     the list used in the book (581 rows). Then:
       python3 tz_solve_optimal.py

How the rebuild works:
  - tools/extract_imdb_cast.py pulls the Twilight Zone cast out of IMDb's files.
  - tools/aliases_twilight_zone.csv fixes spelling variants and stage names and
    leaves out Rod Serling (narrator) and Douglas Heyes (director, voice only).
  - data/tz_cast_actors.txt is the list of actors kept in the project's list.
  - data/tz_cast_supplement.csv adds the appearances that IMDb's files don't
    have; these came from Wikipedia's guest-star lists and hand checking.
  - data/tz_cast_omit.csv drops the few IMDb appearances the project's list
    doesn't use.

Author only: `prepare` makes the three data/ files from the finished list
(python3 tools/twilight_zone_data.py prepare --imdb-dir <folder>
 --cast tz_cast_normalized.csv). Readers never need it.
"""

import argparse, csv, hashlib, json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
ALIASES = os.path.join(HERE, "aliases_twilight_zone.csv")
SERIES = "tt0052520"


def read_rows(path):
    with open(path, encoding="utf-8") as f:
        return {(r["actor"].strip(), r["episode_title"].strip()) for r in csv.DictReader(f)}


def write_rows(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["actor", "episode_title"])
        w.writerows(sorted(rows))


def fingerprint(rows):
    text = "\n".join(f"{a}\t{e}" for a, e in sorted(rows))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def imdb_rows(imdb_dir):
    """IMDb's Twilight Zone cast, with the project's name fixes and exclusions."""
    with tempfile.TemporaryDirectory() as tmp:
        raw = os.path.join(tmp, "imdb_raw.csv")
        norm = os.path.join(tmp, "imdb_norm.csv")
        if subprocess.run([sys.executable, os.path.join(HERE, "extract_imdb_cast.py"), SERIES,
                           "--dir", imdb_dir, "--out", raw]).returncode != 0:
            sys.exit("Stopped: couldn't read the IMDb files (see the message above).")
        subprocess.run([sys.executable, os.path.join(HERE, "normalize_cast.py"), raw,
                        "--aliases", ALIASES, "--out", norm], check=True)
        return read_rows(norm)


def prepare(args):
    if not os.path.exists(args.cast):
        sys.exit(f"Can't find {os.path.abspath(args.cast)}. Run this from the top folder "
                 f"of the repository, or give the full path to --cast.")
    final = read_rows(args.cast)
    imdb = imdb_rows(args.imdb_dir)
    actors = sorted({a for a, _ in final})
    keep = set(actors)
    supplement = final - imdb
    omit = {(a, e) for a, e in imdb if a in keep} - final
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "tz_cast_actors.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(actors) + "\n")
    write_rows(os.path.join(DATA, "tz_cast_supplement.csv"), supplement)
    write_rows(os.path.join(DATA, "tz_cast_omit.csv"), omit)
    with open(os.path.join(DATA, "tz_cast_expected.json"), "w", encoding="utf-8") as f:
        json.dump({"rows": len(final), "sha256": fingerprint(final)}, f, indent=2)
    print(f"actors kept: {len(actors)}   from IMDb: {len(final & imdb)}   "
          f"supplement: {len(supplement)}   omit: {len(omit)}")


def build(args):
    with open(os.path.join(DATA, "tz_cast_actors.txt"), encoding="utf-8") as f:
        keep = {line.strip() for line in f if line.strip()}
    supplement = read_rows(os.path.join(DATA, "tz_cast_supplement.csv"))
    omit = read_rows(os.path.join(DATA, "tz_cast_omit.csv"))
    with open(os.path.join(DATA, "tz_cast_expected.json"), encoding="utf-8") as f:
        expected = json.load(f)
    imdb = imdb_rows(args.imdb_dir)
    rows = ({(a, e) for a, e in imdb if a in keep} - omit) | supplement
    write_rows(args.out, rows)
    if len(rows) == expected["rows"] and fingerprint(rows) == expected["sha256"]:
        print(f"Wrote {args.out}: {len(rows)} rows, identical to the list used in the book.")
    else:
        print(f"Wrote {args.out}: {len(rows)} rows, but it does NOT match the book's list "
              f"({expected['rows']} rows). IMDb may have updated its data since 2026; the "
              f"solver will still run, but the answer could differ from 124.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="rebuild tz_cast_normalized.csv from your IMDb files")
    b.add_argument("--imdb-dir", required=True)
    b.add_argument("--out", default=os.path.join(ROOT, "tz_cast_normalized.csv"))
    p = sub.add_parser("prepare", help="(author only) make the data/ files")
    p.add_argument("--imdb-dir", required=True)
    p.add_argument("--cast", required=True)
    args = ap.parse_args()
    prepare(args) if args.cmd == "prepare" else build(args)


if __name__ == "__main__":
    main()
