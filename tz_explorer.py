"""
Twilight Zone Chain Explorer — Multi-process Randomized Restart Search
=======================================================================
A companion to tz_solver_pc6.py that finds long chains quickly through
randomized search rather than exhaustive DFS.

Each worker independently:
  - Picks random starting episodes (weighted toward well-connected ones)
  - Runs greedy randomized DFS — picks from top candidates, not all of them
  - Backtracks when stuck, restarts from a new episode periodically
  - Reports any chain longer than current best immediately

Cannot PROVE optimality — but can find 120, 121, 122+ quickly.
Any chain found is provably valid (same data and rules as solver).

When a new best is found:
  1. Saves to tz_best_chain_explorer.txt
  2. Updates tz_checkpoint_pc6.json so pc6 raises its pruning bar

Usage:
  pypy3 tz_explorer.py     (recommended)
  python3 tz_explorer.py   (also works)

Run in a separate terminal alongside tz_solver_pc6.py.

16-worker edition (2026-09-26):
  - Explicit strategies for all 16 workers (see WORKER_STRATEGIES)
  - Pinning: PINNED_WORKERS start from PINNED_EPISODE (2026-09-27: ALL 16 workers
    pinned to I Shot an Arrow into the Air, the start of every 124-episode chain)
  - Two-column worker display (8 rows per column); falls back to one
    column automatically if the terminal is narrower than
    TWO_COLUMN_MIN_WIDTH. Needs a terminal at least 50 lines tall to
    show all 16 workers plus the footer.
"""

import csv, time, os, sys, json, random
from collections import defaultdict
from multiprocessing import Process, Queue, Value
from queue import Empty
from blessed import Terminal

# ── SETTINGS ──────────────────────────────────────────────────────────────────
INPUT_FILE         = "tz_cast_normalized.csv"
OUTPUT_FILE        = "tz_best_chain_explorer.txt"
PC6_CHECKPOINT     = "tz_checkpoint_pc6.json"
NUM_WORKERS        = 16      # None = auto-detect CPU count (would be 32 on the 9950X)
PINNED_EPISODE     = "I Shot an Arrow into the Air"  # pinned workers always start here (None to disable)
PINNED_WORKERS     = set(range(16))  # worker IDs that start from PINNED_EPISODE
                                     # (was {0, 1} with "Queen of the Nile")
RUN_TIME_LIMIT     = 30      # run time for any worker not listed in WORKER_STRATEGIES
DEPTH_CHECK_TIME   = 10      # seconds before checking minimum depth
DEPTH_CHECK_MIN    = 100     # if below this depth after DEPTH_CHECK_TIME, abandon
SYNC_INTERVAL      = 300     # re-read checkpoint every N seconds
STATUS_INTERVAL    = 200     # DFS steps between status messages

# Per-worker strategy config: (num_candidates, backtrack_steps, run_time_limit)
# num_candidates  : top N options per step (None = all = exhaustive)
# backtrack_steps : steps to undo at dead end
# run_time_limit  : max seconds per attempt before restarting
# Tuning note (2026-09-26): with few candidates and short backtracks, a worker
# keeps walking back into the same dead end. With cands=1 the choice is fully
# deterministic, so it hits one dead end over and over. In a test, (1, 3) found
# 6 distinct dead ends out of 5,910 and (2, 5) and (3, 5) found only 4–6% new
# ones. Keep backtrack >= 8 unless cands is large.
WORKER_STRATEGIES = {
    #     cands  bt   time
    0:  (None, 15, 120),  # pinned: Queen of the Nile, all candidates, long runs
    1:  (None, 15, 120),  # pinned: Queen of the Nile, all candidates, long runs
    2:  (None,  8,  45),  # all candidates, moderate backtrack   (was (1, 3, 30): looped)
    3:  (6,     8,  30),  # moderate exploration                 (was (2, 5, 30): looped)
    4:  (4,    10,  30),  # narrow-moderate, deeper backtrack    (was (3, 5, 30): looped)
    5:  (3,    10,  30),  # narrow, deeper backtrack
    6:  (5,    10,  30),  # moderate exploration
    7:  (5,    15,  45),  # moderate, longer runs
    8:  (8,    15,  45),  # wider
    9:  (10,   20,  30),  # wide, deep backtracking
    10: (10,   20,  60),  # wide, deep backtracking, long runs
    11: (15,   25,  60),  # very wide
    12: (20,   30,  60),  # very wide, very deep backtracking
    13: (None, 10,  60),  # all candidates, random start
    14: (None, 20,  90),  # all candidates, random start, long runs
    15: (4,     8,  30),  # narrow-moderate
}
DEFAULT_STRATEGY = (3, 5, RUN_TIME_LIMIT)   # used for any worker ID not listed above
TWO_COLUMN_MIN_WIDTH = 110   # below this terminal width, workers display in one column
# ──────────────────────────────────────────────────────────────────────────────


def load_data(input_file):
    raw_ep_to_actors = defaultdict(set)
    raw_actor_to_eps = defaultdict(set)
    try:
        with open(input_file, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                actor = row["actor"].strip()
                ep    = row["episode_title"].strip()
                raw_ep_to_actors[ep].add(actor)
                raw_actor_to_eps[actor].add(ep)
    except FileNotFoundError:
        print(f"ERROR: {input_file} not found.")
        sys.exit(1)

    bridge_actors = {a for a, eps in raw_actor_to_eps.items() if len(eps) >= 2}
    filtered = {ep: {a for a in actors if a in bridge_actors}
                for ep, actors in raw_ep_to_actors.items()}

    ep_names    = sorted(filtered.keys())
    actor_names = sorted(bridge_actors)
    ep_name_to_id    = {n: i for i, n in enumerate(ep_names)}
    actor_name_to_id = {n: i for i, n in enumerate(actor_names)}
    N_EPS    = len(ep_names)
    N_ACTORS = len(actor_names)

    ep_actors = [0] * N_EPS
    actor_eps = [0] * N_ACTORS
    for ep_name, actors in filtered.items():
        ep_id = ep_name_to_id[ep_name]
        for actor_name in actors:
            actor_id = actor_name_to_id[actor_name]
            ep_actors[ep_id]    |= (1 << actor_id)
            actor_eps[actor_id] |= (1 << ep_id)

    return (ep_names, actor_names, ep_actors, actor_eps,
            ep_name_to_id, actor_name_to_id)


def load_best_known():
    """Load best chain length from pc6 checkpoint or explorer output."""
    for path in [PC6_CHECKPOINT, OUTPUT_FILE]:
        if not os.path.exists(path):
            continue
        try:
            if path.endswith('.json'):
                with open(path) as f:
                    data = json.load(f)
                chain = data.get("best_chain", [])
                if chain:
                    return (len(chain) + 1) // 2
            else:
                with open(path) as f:
                    for line in f:
                        if line.startswith("Episodes in chain"):
                            return int(line.split(":")[1].strip())
        except Exception:
            pass
    return 0


def save_best(chain_ids, ep_names, actor_names, elapsed, total_attempts):
    """Save new best chain and update pc6 checkpoint atomically."""
    episodes = chain_ids[0::2]
    actors   = chain_ids[1::2]

    # Build alternating name list
    chain_names = []
    for i, ep_id in enumerate(episodes):
        chain_names.append(ep_names[ep_id])
        if i < len(actors):
            chain_names.append(actor_names[actors[i]])

    # Save explorer output file
    ep_name_list    = [ep_names[i] for i in episodes]
    actor_name_list = [actor_names[i] for i in actors]
    lines = [
        "TWILIGHT ZONE LONGEST EPISODE CHAIN  [Explorer Edition]",
        "========================================================",
        f"Episodes in chain : {len(episodes)}",
        f"Time elapsed      : {elapsed:.0f}s",
        f"Total attempts    : {total_attempts:,}",
        "",
        "Each episode links to the next via the named actor.",
        "No episode or bridging actor repeats.",
        "NOTE: Found by randomized search — not exhaustive proof.",
        "",
    ]
    for i, ep in enumerate(ep_name_list):
        lines.append(f"{i+1:>3}. {ep}")
        if i < len(actor_name_list):
            lines.append(f"       \u2514\u2500[ {actor_name_list[i]} ]")

    tmp = OUTPUT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.replace(tmp, OUTPUT_FILE)

    # Update pc6 checkpoint so exhaustive solver raises its pruning bar
    if os.path.exists(PC6_CHECKPOINT):
        try:
            with open(PC6_CHECKPOINT) as f:
                data = json.load(f)
            old_len = (len(data.get("best_chain", [])) + 1) // 2
            if len(episodes) > old_len:
                data["best_chain"] = chain_names
                tmp2 = PC6_CHECKPOINT + ".tmp"
                with open(tmp2, "w") as f:
                    json.dump(data, f)
                os.replace(tmp2, PC6_CHECKPOINT)
        except Exception:
            pass


def get_candidates(ep_id, visited_eps, visited_actors, ep_actors, actor_eps):
    """Get all valid next steps with onward-options score."""
    candidates = []
    avail_actors = ep_actors[ep_id] & ~visited_actors
    a_temp = avail_actors
    while a_temp:
        a_lsb    = a_temp & (-a_temp); a_temp ^= a_lsb
        actor_id = a_lsb.bit_length() - 1
        avail_eps = actor_eps[actor_id] & ~visited_eps
        e_temp = avail_eps
        while e_temp:
            e_lsb  = e_temp & (-e_temp); e_temp ^= e_lsb
            ep2_id = e_lsb.bit_length() - 1
            onward_actors = ep_actors[ep2_id] & ~(visited_actors | (1 << actor_id))
            onward = 0
            oa_temp = onward_actors
            while oa_temp:
                oa_lsb = oa_temp & (-oa_temp); oa_temp ^= oa_lsb
                oa_id  = oa_lsb.bit_length() - 1
                onward += bin(actor_eps[oa_id] & ~(visited_eps | (1 << ep2_id))).count('1')
            candidates.append((onward, actor_id, ep2_id))
    return candidates


def explorer_worker(worker_id, result_queue, best_len_val, stop_flag):
    """Single explorer worker — runs until stop_flag set."""
    (ep_names, actor_names, ep_actors, actor_eps,
     ep_name_to_id, actor_name_to_id) = load_data(INPUT_FILE)
    N_EPS = len(ep_names)

    # Episode weights for random start selection (prefer well-connected)
    ep_weights = [bin(ep_actors[i]).count('1') + 1 for i in range(N_EPS)]
    total_weight = sum(ep_weights)

    # Load this worker's strategy
    strategy = WORKER_STRATEGIES.get(worker_id, DEFAULT_STRATEGY)
    num_candidates, backtrack_steps, run_time = strategy

    attempts      = 0
    last_sync     = time.time()
    personal_best = 0          # tracks THIS worker's own finds, starts at 0
    personal_best_chain = []

    def weighted_random_ep():
        r = random.random() * total_weight
        cumulative = 0
        for i, w in enumerate(ep_weights):
            cumulative += w
            if r <= cumulative:
                return i
        return N_EPS - 1

    while not stop_flag.value:
        # Periodic sync with checkpoint
        now = time.time()
        if now - last_sync >= SYNC_INTERVAL:
            synced = load_best_known()
            if synced > best_len_val.value:
                with best_len_val.get_lock():
                    if synced > best_len_val.value:
                        best_len_val.value = synced
            last_sync = now

        # Pick starting episode — pinned workers always use the pinned episode
        if worker_id in PINNED_WORKERS and PINNED_EPISODE and PINNED_EPISODE in ep_name_to_id:
            start_ep_id = ep_name_to_id[PINNED_EPISODE]
        else:
            start_ep_id = weighted_random_ep()
        attempts += 1

        # Randomized greedy run
        chain        = [start_ep_id]
        visited_eps  = 1 << start_ep_id
        visited_acts = 0
        history      = []
        step         = 0
        deadline        = time.time() + run_time
        depth_check_at  = time.time() + DEPTH_CHECK_TIME
        depth_checked   = False

        while not stop_flag.value and time.time() < deadline:
            step += 1
            current_ep = chain[-1]

            # Depth checkpoint: if not deep enough early, abandon
            if not depth_checked and time.time() >= depth_check_at:
                depth_checked = True
                ep_count_now = (len(chain) + 1) // 2
                if ep_count_now < DEPTH_CHECK_MIN:
                    break  # abandon — starting episode not promising
            candidates = get_candidates(current_ep, visited_eps, visited_acts,
                                        ep_actors, actor_eps)

            if not candidates:
                # Dead end — backtrack
                if not history:
                    break
                steps = min(backtrack_steps, len(history))
                for _ in range(steps):
                    if not history:
                        break
                    actor_id, ep_id = history.pop()
                    chain.pop(); chain.pop()
                    visited_eps  ^= (1 << ep_id)
                    visited_acts ^= (1 << actor_id)
                continue

            # Pick from top candidates per this worker's strategy
            candidates.sort(reverse=True)
            top = candidates if num_candidates is None else candidates[:num_candidates]
            weights = [max(1, c[0] + 1) for c in top]
            total_w = sum(weights)
            r = random.random() * total_w
            cumulative = 0
            chosen = top[0]
            for i, w in enumerate(weights):
                cumulative += w
                if r <= cumulative:
                    chosen = top[i]
                    break

            _, actor_id, next_ep_id = chosen
            chain.append(actor_id)
            chain.append(next_ep_id)
            visited_eps  |= (1 << next_ep_id)
            visited_acts |= (1 << actor_id)
            history.append((actor_id, next_ep_id))

            ep_count = (len(chain) + 1) // 2

            # Send status periodically
            if step % STATUS_INTERVAL == 0:
                tail = [ep_names[i] for i in chain[0::2][-3:]]
                try:
                    result_queue.put_nowait((
                        "status", worker_id,
                        ep_names[start_ep_id],
                        ep_count,
                        personal_best,
                        attempts,
                        tail
                    ))
                except Exception:
                    pass

            # New personal best?
            if ep_count > personal_best:
                personal_best = ep_count
                personal_best_chain = list(chain)
                try:
                    result_queue.put_nowait((
                        "status", worker_id,
                        ep_names[start_ep_id],
                        ep_count,
                        personal_best,
                        attempts,
                        [ep_names[i] for i in chain[0::2][-3:]]
                    ))
                except Exception:
                    pass

                # New global best?
                if ep_count > best_len_val.value:
                    with best_len_val.get_lock():
                        if ep_count > best_len_val.value:
                            best_len_val.value = ep_count
                            try:
                                result_queue.put(("best", list(chain), time.time()))
                            except Exception:
                                pass

    result_queue.put(("done", worker_id))


def format_time(s):
    if s < 60:   return f"{s:.0f}s"
    if s < 3600: return f"{s/60:.1f}m"
    return f"{int(s//3600)}h {int((s%3600)//60)}m"

def truncate(s, n):
    return s if len(s) <= n else s[:n-3] + "..."


def render_segments(segments, width, styles):
    """Join (text, style) segments, cut/pad to exactly `width` visible
    characters, then colour. Measuring before colouring keeps columns aligned."""
    out, used = "", 0
    for text, style in segments:
        if used >= width:
            break
        room = width - used
        if len(text) > room:
            text = text[:max(room - 1, 0)] + "…" if room > 0 else ""
        out  += styles[style](text) if text else ""
        used += len(text)
    return out + " " * (width - used)


def worker_cell(wid, info, width, initial_best):
    """Return the 3 display lines (as segment lists) for one worker."""
    if info["done"]:
        return [[(f"  Explorer {wid+1:>2}: ", "bg"), ("[FINISHED]", "y")], [], []]
    strat    = WORKER_STRATEGIES.get(wid, DEFAULT_STRATEGY)
    cand_str = "all" if strat[0] is None else str(strat[0])
    pin      = " [pinned]" if wid in PINNED_WORKERS else ""
    pb       = info["personal_best"]
    is_pb    = info["depth"] == pb and info["depth"] > initial_best
    line1 = [(f"  Explorer {wid+1:>2}{pin}: ", "bg"),
             (info["start_ep"], "y")]
    if is_pb:
        line1.append(("  *** PERSONAL BEST ***", "y"))
    line2 = [(f"    depth {info['depth']:<4} best {pb if pb else '—':<4} "
              f"tries {info['attempts']:,}", "g"),
             (f"  [c:{cand_str} bt:{strat[1]} t:{strat[2]}s]", "g")]
    tail = info["tail"]
    if tail:
        per_ep   = max(12, (width - 12) // 3)   # share the cell width among 3 names
        tail_str = " → ".join(truncate(e, per_ep) for e in tail[-3:])
        line3 = [(f"    …{tail_str}", "g")]
    else:
        line3 = [("    starting…", "g")]
    return [line1, line2, line3]


def display_loop(term, result_queue, best_len_val, stop_flag,
                 start_time, num_workers, initial_best,
                 ep_names, actor_names):

    best_len   = initial_best
    best_chain = []
    improvement_history = []

    worker_info = {i: {
        "start_ep":      "starting...",
        "depth":         0,
        "personal_best": 0,    # starts at 0 — tracks actual finds only
        "attempts":      0,
        "tail":          [],
        "done":          False,
    } for i in range(num_workers)}

    total_attempts = 0
    frame          = 0
    spinner        = "|/-\\"
    W              = max(term.width or 80, 70)

    def g(t):
        try:    return term.green(t)
        except: return t
    def bg(t):
        try:    return term.bright_green(t)
        except: return t
    def y(t):
        try:    return term.yellow(t)
        except: return t

    with term.fullscreen(), term.hidden_cursor():
        while True:
            frame += 1
            now    = time.time()
            W      = max(term.width or 80, 70)   # re-read each frame so resizing works
            elapsed = now - start_time

            # Drain queue
            for _ in range(200):
                try:
                    msg  = result_queue.get_nowait()
                    kind = msg[0]

                    if kind == "best":
                        _, chain, ts = msg
                        ep_count = (len(chain) + 1) // 2
                        if ep_count > best_len:
                            best_len  = ep_count
                            best_chain = chain
                            improvement_history.append((ts, ep_count))
                            # Save to files
                            save_best(chain, ep_names, actor_names,
                                      ts - start_time, total_attempts)

                    elif kind == "status":
                        _, wid, sep, depth, pb, att, tail = msg
                        worker_info[wid]["start_ep"]      = sep
                        worker_info[wid]["depth"]          = depth
                        worker_info[wid]["personal_best"]  = pb
                        worker_info[wid]["attempts"]       = att
                        worker_info[wid]["tail"]           = tail
                        total_attempts = sum(
                            worker_info[i]["attempts"] for i in range(num_workers)
                        )

                    elif kind == "done":
                        worker_info[msg[1]]["done"] = True

                except Empty:
                    break
                except Exception:
                    break

            rows = []

            # Header
            rows.append(g((" >>> TWILIGHT ZONE CHAIN EXPLORER <<< ").center(W)))
            rows.append(g("-" * W))

            spin = spinner[frame % len(spinner)]
            since = ""
            if improvement_history:
                since = f"  ({format_time(now - improvement_history[-1][0])} ago)"
            rows.append(
                bg(f"  {spin} RUNNING  ") +
                g(f"| Uptime: ") + bg(f"{format_time(elapsed):<10}") +
                g(f"| Attempts: ") + bg(f"{total_attempts:>10,}") +
                g(f"| Workers: ") + bg(str(num_workers))
            )
            rows.append(g("-" * W))

            rows.append(
                y(f"  >> BEST KNOWN: ") +
                bg(str(best_len)) +
                g(f" episodes (from checkpoint)") + g(since)
            )

            if best_chain:
                best_eps  = best_chain[0::2]
                best_acts = best_chain[1::2]
                tail_start = max(0, len(best_eps) - 4)
                rows.append(g("  Best chain tail:"))
                for i in range(tail_start, len(best_eps)):
                    rows.append(bg(truncate(f"    {i+1:>3}. {ep_names[best_eps[i]]}", W-2)))
                    if i < len(best_acts):
                        rows.append(g(f"         \u2514\u2500[ {actor_names[best_acts[i]]} ]"))

            rows.append(g("-" * W))
            two_col = W >= TWO_COLUMN_MIN_WIDTH and num_workers > 1
            layout  = "2 columns" if two_col else "1 column"
            rows.append(g(f"  EXPLORERS — LIVE SEARCH ({num_workers} workers, {layout}):"))
            styles = {"g": g, "bg": bg, "y": y}
            if two_col:
                col_w  = (W - 3) // 2          # 3 = gap between columns
                n_left = (num_workers + 1) // 2
                for r in range(n_left):
                    rows.append("")
                    left  = worker_cell(r, worker_info[r], col_w, initial_best)
                    right_id = r + n_left
                    right = (worker_cell(right_id, worker_info[right_id], col_w, initial_best)
                             if right_id < num_workers else [[]] * len(left))
                    for lseg, rseg in zip(left, right):
                        rows.append(render_segments(lseg, col_w, styles) + "   " +
                                    render_segments(rseg, col_w, styles))
            else:
                for wid in range(num_workers):
                    rows.append("")
                    for seg in worker_cell(wid, worker_info[wid], W - 1, initial_best):
                        rows.append(render_segments(seg, W - 1, styles))

            rows.append(g("-" * W))
            rows.append(g(
                f"  Output: {OUTPUT_FILE}  |  "
                f"Updates: {PC6_CHECKPOINT}  |  Ctrl+C to stop"
            ))

            out = term.home
            for i, row in enumerate(rows):
                if i >= term.height - 1:
                    break
                out += term.move(i, 0) + term.clear_eol + row
            sys.stdout.write(out)
            sys.stdout.flush()

            time.sleep(0.5)

            if all(worker_info[i]["done"] for i in range(num_workers)):
                break

    return best_chain


if __name__ == "__main__":

    (ep_names, actor_names, ep_actors, actor_eps,
     ep_name_to_id, actor_name_to_id) = load_data(INPUT_FILE)

    if NUM_WORKERS is None:
        NUM_WORKERS = os.cpu_count() or 4

    initial_best = load_best_known()

    if PINNED_EPISODE and PINNED_EPISODE not in ep_name_to_id:
        print(f"ERROR: PINNED_EPISODE {PINNED_EPISODE!r} is not an episode in {INPUT_FILE}.")
        print("Check the spelling (it must match the CSV exactly), or set it to None.")
        sys.exit(1)

    print(f"Episodes      : {len(ep_names)}")
    print(f"Bridge actors : {len(actor_names)}")
    print(f"Workers       : {NUM_WORKERS}")
    print(f"Known best    : {initial_best} episodes")
    if PINNED_EPISODE:
        n_pinned = len([w for w in range(NUM_WORKERS) if w in PINNED_WORKERS])
        print(f"Pinned        : {n_pinned} of {NUM_WORKERS} workers start from {PINNED_EPISODE}")
    print(f"Output        : {OUTPUT_FILE}")
    print(f"Updates       : {PC6_CHECKPOINT}")
    print()
    print("Launching explorers...")

    result_queue = Queue(maxsize=5000)
    best_len_val = Value('i', initial_best)
    stop_flag    = Value('b', 0)
    start_time   = time.time()

    workers = []
    for i in range(NUM_WORKERS):
        p = Process(
            target=explorer_worker,
            args=(i, result_queue, best_len_val, stop_flag),
            daemon=True
        )
        p.start()
        workers.append(p)

    term       = Terminal()
    best_chain = []
    try:
        best_chain = display_loop(
            term, result_queue, best_len_val, stop_flag,
            start_time, NUM_WORKERS, initial_best,
            ep_names, actor_names
        )
    except KeyboardInterrupt:
        stop_flag.value = 1
        while True:
            try:
                msg = result_queue.get_nowait()
                if msg[0] == "best" and (len(msg[1])+1)//2 > (len(best_chain)+1)//2:
                    best_chain = msg[1]
            except Exception:
                break
        # Save whatever we found this session on exit
        if best_chain:
            elapsed = time.time() - start_time
            save_best(best_chain, ep_names, actor_names, elapsed, 0)

    for p in workers:
        p.terminate()

    elapsed = time.time() - start_time
    final_len = (len(best_chain) + 1) // 2 if best_chain else initial_best

    print(term.normal + term.clear)
    print(f"\n{'='*60}")
    print(f"Explorer finished after {format_time(elapsed)}")
    print(f"Best chain found: {final_len} episodes")
    if best_chain:
        print(f"Saved to: {OUTPUT_FILE}")
        print(f"Checkpoint updated: {PC6_CHECKPOINT}")
    print(f"{'='*60}\n")
