# About the data

Every program in this project reads one file, `tz_cast_normalized.csv`, with two
columns, `actor` and `episode_title`: one row for each appearance by an actor in the
project's list for the original *Twilight Zone* (1959–1964).

That file is **not** included here, because IMDb licenses its datasets for personal
and non-commercial use only. You build it yourself from your own copy of IMDb's free
files, with `tools/twilight_zone_data.py build --imdb-dir <folder>` (see the README).
The script checks the result against the book's list (581 rows, by SHA-256).

## Where it came from

1. **IMDb's downloadable datasets**, filtered to the original series
   (IMDb ID `tt0052520`) with `tools/extract_imdb_cast.py`.
   IMDb's file lists only the top-billed cast of each episode.
2. **Wikipedia guest-star lists** for each season, merged in to fill gaps.
   The appearances that don't come from IMDb are in `data/tz_cast_supplement.csv`.
3. **Hand corrections**, mainly spelling variants and stage names
   (see `tools/aliases_twilight_zone.csv`), for example
   Ross Elliot → Ross Elliott and Suzanne Cupito → Morgan Brittany.

Rod Serling (the host and narrator) and director Douglas Heyes (who is heard,
not seen, in one episode) are left out: the puzzle is about actors who played
parts in the stories.

## What's in it

- 156 original episodes; 153 appear in the file.
- 213 "bridge" actors appear in two or more episodes; only they can link episodes.
- 145 episodes can be reached by some chain. 11 never can: *An Occurrence at
  Owl Creek Bridge* (a French film), *The Last Night of a Jockey* (Mickey Rooney
  alone), *The Invaders*, *The Encounter*, *Two*, *Mirror Image*, *Four O'Clock*,
  *Stopover in a Quiet Town*, *Probe 7, Over and Out*, *King Nine Will Not
  Return* and *The Last Flight*.

## Caveats

- **The answer is only as true as the list.** The proof that 124 is the longest
  chain is exact *for this file*. An uncredited bit player who turns out to be in
  two episodes would add a link and could change the answer. If you find a
  correction, fix the CSV and rerun `tz_solve_optimal.py`; it takes about a second.
- **Licensing.** IMDb licenses its datasets for personal and non-commercial use
  (https://data.imdb.com/non-commercial-datasets). That's why this repository ships
  the tools and the non-IMDb additions in `data/`, not the finished list. If you
  share anything you build from IMDb's files, check that your use fits their terms.