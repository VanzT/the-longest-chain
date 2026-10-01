"""
Twilight Zone Longest Episode Chain Solver - PC EDITION v6 (exhaustive DFS)
===========================================================================
Searches for the longest chain of episodes linked by shared actors, with no
episode and no bridging actor used twice, and would PROVE the answer if it
ever finished. (It never finished: see the README. The one-second proof of the
optimum, 124, comes from tz_solve_optimal.py instead.)

How it works:
  - Depth-first search over episodes and bridge actors.
  - Pruning: before going deeper, count the unused episodes still reachable
    (bitmask breadth-first search). If the current length plus that count
    cannot beat the best chain, the branch is abandoned.
  - Early exit: the count stops as soon as it is big enough not to prune.
  - Pendant correction: episodes with only one usable bridge actor left can
    only be the LAST episode of a chain, so at most one of them counts.
  - Bitmasks: visited episodes/actors are Python integers (bit i = item i).
  - Parallelism: the search is split into 44,189 third-level branches that
    any worker process can pick up. Hourly checkpoints; Ctrl-C safe.
  - Green terminal display (needs: pip install blessed).

Run with PyPy for speed:  pypy3 tz_solver_pc6.py
Input:  tz_cast_normalized.csv
Output: tz_best_chain_pc6.txt, tz_checkpoint_pc6.json
"""

import csv, time, os, sys, json
from collections import defaultdict
from multiprocessing import Process, Queue, Value
from queue import Empty
from blessed import Terminal

# ── SETTINGS ──────────────────────────────────────────────────────────────────
INPUT_FILE          = "tz_cast_normalized.csv"
OUTPUT_FILE         = "tz_best_chain_pc6.txt"
CHECKPOINT_FILE     = "tz_checkpoint_pc6.json"
CHECKPOINT_INTERVAL = 3600
NUM_WORKERS         = None   # None = auto-detect
STATUS_INTERVAL     = 50
# ──────────────────────────────────────────────────────────────────────────────


# ── DATA LOADING AND ID ASSIGNMENT ────────────────────────────────────────────

def load_data(input_file):
    """
    Load CSV and return:
      ep_names   : list of episode name strings, index = episode ID
      actor_names: list of actor name strings,   index = actor ID
      ep_actors  : list of ints (bitmasks), ep_actors[ep_id] = actor bitmask
      actor_eps  : list of ints (bitmasks), actor_eps[actor_id] = ep bitmask
      ep_name_to_id  : dict str -> int
      actor_name_to_id: dict str -> int
    """
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

    # Keep only bridge actors (2+ episodes)
    bridge_actors = {a for a, eps in raw_actor_to_eps.items() if len(eps) >= 2}

    # Filter ep_to_actors to bridge actors only
    filtered_ep_to_actors = {
        ep: {a for a in actors if a in bridge_actors}
        for ep, actors in raw_ep_to_actors.items()
    }

    # Assign stable integer IDs
    # Sort for determinism across runs
    ep_names    = sorted(filtered_ep_to_actors.keys())
    actor_names = sorted(bridge_actors)

    ep_name_to_id    = {name: i for i, name in enumerate(ep_names)}
    actor_name_to_id = {name: i for i, name in enumerate(actor_names)}

    N_EPS    = len(ep_names)
    N_ACTORS = len(actor_names)

    # Build bitmask arrays
    ep_actors = [0] * N_EPS    # ep_actors[ep_id] = bitmask of bridge actors
    actor_eps = [0] * N_ACTORS # actor_eps[actor_id] = bitmask of episodes

    for ep_name, actors in filtered_ep_to_actors.items():
        ep_id = ep_name_to_id[ep_name]
        for actor_name in actors:
            actor_id = actor_name_to_id[actor_name]
            ep_actors[ep_id]    |= (1 << actor_id)
            actor_eps[actor_id] |= (1 << ep_id)

    return (ep_names, actor_names,
            ep_actors, actor_eps,
            ep_name_to_id, actor_name_to_id)


# ── BRANCH GENERATION ─────────────────────────────────────────────────────────

def generate_branches(ep_names, actor_names, ep_actors, actor_eps,
                      ep_name_to_id, actor_name_to_id):
    """
    Pre-compute third-level branches using bitmask operations.
    Falls back to second or first level when dead ends occur.
    Returns list of branch dicts with integer IDs.
    """
    N_EPS    = len(ep_names)
    N_ACTORS = len(actor_names)

    # Sort episodes by number of bridge actors (fewest first)
    sorted_ep_ids = sorted(range(N_EPS),
                           key=lambda e: bin(ep_actors[e]).count('1'))

    branches = []

    for start_id in sorted_ep_ids:
        # Iterate over bridge actors of this episode
        actor_mask = ep_actors[start_id]
        temp = actor_mask
        while temp:
            lsb    = temp & (-temp)
            temp  ^= lsb
            a1_id  = lsb.bit_length() - 1

            # Episodes reachable via actor a1, excluding start
            ep1_mask = actor_eps[a1_id] & ~(1 << start_id)
            temp2 = ep1_mask
            while temp2:
                lsb2   = temp2 & (-temp2)
                temp2 ^= lsb2
                ep1_id = lsb2.bit_length() - 1

                visited2_eps    = (1 << start_id) | (1 << ep1_id)
                visited2_actors = (1 << a1_id)

                # Second step actors from ep1 excluding a1
                a2_mask = ep_actors[ep1_id] & ~visited2_actors
                if a2_mask == 0:
                    # Dead end after first step
                    branches.append({
                        "init_chain":        [start_id, a1_id, ep1_id],
                        "init_visited_eps":  visited2_eps,
                        "init_visited_act":  visited2_actors,
                        "init_depth":        2,
                        "level":             1,
                        "branch_id":         (start_id, a1_id, ep1_id),
                        "label": (f"{ep_names[start_id]} → "
                                  f"{ep_names[ep1_id]}  [dead end]"),
                    })
                    continue

                temp3 = a2_mask
                while temp3:
                    lsb3   = temp3 & (-temp3)
                    temp3 ^= lsb3
                    a2_id  = lsb3.bit_length() - 1

                    ep2_mask = actor_eps[a2_id] & ~visited2_eps
                    if ep2_mask == 0:
                        continue  # this actor leads nowhere new

                    temp4 = ep2_mask
                    while temp4:
                        lsb4   = temp4 & (-temp4)
                        temp4 ^= lsb4
                        ep2_id = lsb4.bit_length() - 1

                        visited3_eps    = visited2_eps    | (1 << ep2_id)
                        visited3_actors = visited2_actors | (1 << a2_id)

                        # Third step actors from ep2
                        a3_mask = ep_actors[ep2_id] & ~visited3_actors
                        if a3_mask == 0:
                            # Dead end after second step
                            branches.append({
                                "init_chain":       [start_id, a1_id, ep1_id,
                                                     a2_id, ep2_id],
                                "init_visited_eps": visited3_eps,
                                "init_visited_act": visited3_actors,
                                "init_depth":       3,
                                "level":            2,
                                "branch_id":        (start_id, a1_id, ep1_id,
                                                     a2_id, ep2_id),
                                "label": (f"{ep_names[start_id]} → "
                                          f"{ep_names[ep1_id]} → "
                                          f"{ep_names[ep2_id]}  [dead end]"),
                            })
                            continue

                        temp5 = a3_mask
                        while temp5:
                            lsb5   = temp5 & (-temp5)
                            temp5 ^= lsb5
                            a3_id  = lsb5.bit_length() - 1

                            ep3_mask = actor_eps[a3_id] & ~visited3_eps
                            if ep3_mask == 0:
                                continue

                            temp6 = ep3_mask
                            while temp6:
                                lsb6   = temp6 & (-temp6)
                                temp6 ^= lsb6
                                ep3_id = lsb6.bit_length() - 1

                                visited4_eps    = visited3_eps    | (1 << ep3_id)
                                visited4_actors = visited3_actors | (1 << a3_id)

                                branches.append({
                                    "init_chain":       [start_id, a1_id, ep1_id,
                                                         a2_id, ep2_id,
                                                         a3_id, ep3_id],
                                    "init_visited_eps": visited4_eps,
                                    "init_visited_act": visited4_actors,
                                    "init_depth":       4,
                                    "level":            3,
                                    "branch_id":        (start_id, a1_id, ep1_id,
                                                         a2_id, ep2_id,
                                                         a3_id, ep3_id),
                                    "label": (f"{ep_names[start_id]} → "
                                              f"{ep_names[ep1_id]} → "
                                              f"{ep_names[ep2_id]} → "
                                              f"{ep_names[ep3_id]}"),
                                })

    return branches


# ── CHECKPOINT ────────────────────────────────────────────────────────────────

def save_checkpoint(best_chain_names, completed_branch_ids, calls, num_workers,
                    ep_names, actor_names):
    """Save checkpoint. best_chain_names is list of alternating ep/actor strings."""
    data = {
        "best_chain":         best_chain_names,
        "completed_branches": [list(b) for b in completed_branch_ids],
        "total_calls":        calls,
        "saved_at":           time.time(),
        "num_workers":        num_workers,
    }
    tmp = CHECKPOINT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, CHECKPOINT_FILE)


def load_checkpoint():
    if not os.path.exists(CHECKPOINT_FILE):
        return [], set(), 0
    try:
        with open(CHECKPOINT_FILE, encoding="utf-8") as f:
            data = json.load(f)
        chain     = data.get("best_chain", [])
        completed = {tuple(b) for b in data.get("completed_branches", [])}
        calls     = data.get("total_calls", 0)
        age       = time.time() - data.get("saved_at", 0)
        ep_count  = (len(chain) + 1) // 2 if chain else 0
        print(f"Checkpoint found:")
        print(f"  Best chain:          {ep_count} episodes")
        print(f"  Completed branches:  {len(completed)}")
        print(f"  Prior calls:         {calls:,}")
        print(f"  Age:                 {age/3600:.1f}h")
        return chain, completed, calls
    except Exception as e:
        print(f"Could not load checkpoint: {e}")
        return [], set(), 0


def chain_names_to_ids(chain_names, ep_name_to_id, actor_name_to_id):
    """Convert alternating [ep, actor, ep, actor...] name list to ID list."""
    ids = []
    for i, name in enumerate(chain_names):
        if i % 2 == 0:  # episode
            ids.append(ep_name_to_id.get(name, -1))
        else:           # actor
            ids.append(actor_name_to_id.get(name, -1))
    return ids


def chain_ids_to_names(chain_ids, ep_names, actor_names):
    """Convert alternating [ep_id, actor_id, ep_id...] list to names."""
    names = []
    for i, id_ in enumerate(chain_ids):
        if i % 2 == 0:
            names.append(ep_names[id_])
        else:
            names.append(actor_names[id_])
    return names


def save_best_file(chain_names, elapsed, calls, num_workers):
    episodes = chain_names[0::2]
    actors   = chain_names[1::2]
    lines = [
        "TWILIGHT ZONE LONGEST EPISODE CHAIN  [PC Edition v6]",
        "==================================================",
        f"Episodes in chain : {len(episodes)}",
        f"Time elapsed      : {elapsed:.0f}s",
        f"DFS calls         : {calls:,}",
        f"Workers used      : {num_workers}",
        "",
        "Each episode links to the next via the named actor.",
        "No episode or bridging actor repeats.",
        "",
    ]
    for i, ep in enumerate(episodes):
        lines.append(f"{i+1:>3}. {ep}")
        if i < len(actors):
            lines.append(f"       └─[ {actors[i]} ]")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ── REACHABILITY — INLINE PENDANT CORRECTION WITH CORRECTED EARLY-EXIT ──────
#
# v6 improvement over pc4: as each new episode is discovered during BFS,
# we immediately check if it is a "pendant" — an episode with exactly 1
# available bridge actor. Pendants can only be chain termini (last episode),
# not intermediate nodes. At most 1 pendant can be used; the rest are
# inaccessible as non-final episodes. So we subtract (pendants-1) from the
# raw BFS count.
#
# Key vs pc5: we count pendants INLINE during BFS (not in a separate pass),
# and the early-exit fires on the CORRECTED count. This preserves most of
# pc4's early-exit benefit while applying the tighter bound.
#
# Pendant check uses the O(1) bit trick: `x and not (x & (x-1))` is True
# iff x has exactly 1 bit set — no bin().count() needed.

def count_reachable(current_ep_id, visited_eps_mask, visited_actors_mask,
                    ep_actors, actor_eps, N_EPS, threshold=None):
    """
    Bitmask BFS with inline pendant correction and corrected early-exit.
    Returns corrected count = raw_reachable - max(0, pendants - 1).
    """
    reachable   = 0
    pendants    = 0
    frontier    = 1 << current_ep_id
    seen_eps    = visited_eps_mask | frontier
    seen_actors = visited_actors_mask

    while frontier:
        next_frontier = 0
        temp = frontier
        while temp:
            lsb   = temp & (-temp)
            temp ^= lsb
            ep_id = lsb.bit_length() - 1

            new_actors = ep_actors[ep_id] & ~seen_actors
            seen_actors |= new_actors

            a_temp = new_actors
            while a_temp:
                a_lsb    = a_temp & (-a_temp)
                a_temp  ^= a_lsb
                actor_id = a_lsb.bit_length() - 1

                new_eps = actor_eps[actor_id] & ~seen_eps
                if new_eps:
                    seen_eps      |= new_eps
                    next_frontier |= new_eps

                    # Process each newly found episode individually
                    e_temp = new_eps
                    while e_temp:
                        e_lsb     = e_temp & (-e_temp)
                        e_temp   ^= e_lsb
                        new_ep_id = e_lsb.bit_length() - 1
                        reachable += 1

                        # O(1) pendant check: exactly 1 available bridge actor
                        avail = ep_actors[new_ep_id] & ~visited_actors_mask
                        if avail and not (avail & (avail - 1)):
                            pendants += 1

                        # Corrected early-exit
                        if threshold is not None:
                            corrected = reachable - (pendants - 1 if pendants > 1 else 0)
                            if corrected > threshold:
                                return corrected

        frontier = next_frontier

    return reachable - (pendants - 1 if pendants > 1 else 0)


# ── DFS (bitmask) ─────────────────────────────────────────────────────────────

def dfs(chain, visited_eps, visited_actors,
        ep_actors, actor_eps, N_EPS,
        result_queue, calls_counter, best_len_val, stop_flag,
        worker_id, local_calls, local_best_depth, local_best_time,
        branch_calls, prune_count, prune_depth_sum,
        ep_names, actor_names):

    local_calls[0]  += 1
    branch_calls[0] += 1

    # Periodic flush and status message
    if local_calls[0] % STATUS_INTERVAL == 0:
        with calls_counter.get_lock():
            calls_counter.value += STATUS_INTERVAL
        ep_count = (len(chain) + 1) // 2
        # Build display tail from IDs
        tail_ids = chain[0::2][-4:]
        tail     = [ep_names[i] for i in tail_ids]
        try:
            result_queue.put_nowait((
                "status", worker_id,
                tail, ep_count,
                local_best_depth[0], local_best_time[0],
                branch_calls[0],
                prune_count[0], prune_depth_sum[0],
            ))
        except Exception:
            pass

    current_ep_id = chain[-1]
    ep_count      = (len(chain) + 1) // 2

    # Update personal best
    if ep_count > local_best_depth[0]:
        local_best_depth[0] = ep_count
        local_best_time[0]  = time.time()

    # Check global best
    if ep_count > best_len_val.value:
        with best_len_val.get_lock():
            if ep_count > best_len_val.value:
                best_len_val.value = ep_count
                chain_names = chain_ids_to_names(chain, ep_names, actor_names)
                try:
                    result_queue.put_nowait(("best", chain_names, time.time()))
                except Exception:
                    result_queue.put(("best", chain_names, time.time()))

    if stop_flag.value:
        return

    # Pruning with early exit
    threshold = best_len_val.value - ep_count
    reachable = count_reachable(current_ep_id, visited_eps, visited_actors,
                                ep_actors, actor_eps, N_EPS, threshold)
    if ep_count + reachable <= best_len_val.value:
        prune_count[0]     += 1
        prune_depth_sum[0] += ep_count
        return

    # Generate candidates using bitmask ops
    candidates = []
    avail_actors = ep_actors[current_ep_id] & ~visited_actors
    a_temp = avail_actors
    while a_temp:
        a_lsb    = a_temp & (-a_temp)
        a_temp  ^= a_lsb
        actor_id = a_lsb.bit_length() - 1

        avail_eps = actor_eps[actor_id] & ~visited_eps
        e_temp = avail_eps
        while e_temp:
            e_lsb  = e_temp & (-e_temp)
            e_temp ^= e_lsb
            ep_id  = e_lsb.bit_length() - 1

            # Onward heuristic: count further reachable (ep, actor) pairs
            onward_actors = ep_actors[ep_id] & ~(visited_actors | (1 << actor_id))
            onward = 0
            oa_temp = onward_actors
            while oa_temp:
                oa_lsb  = oa_temp & (-oa_temp)
                oa_temp ^= oa_lsb
                oa_id   = oa_lsb.bit_length() - 1
                new_eps  = actor_eps[oa_id] & ~(visited_eps | (1 << ep_id))
                onward  += bin(new_eps).count('1')

            candidates.append((onward, actor_id, ep_id))

    candidates.sort()

    for _, actor_id, ep_id in candidates:
        if stop_flag.value:
            return
        new_visited_eps    = visited_eps    | (1 << ep_id)
        new_visited_actors = visited_actors | (1 << actor_id)
        chain.append(actor_id)
        chain.append(ep_id)
        dfs(chain, new_visited_eps, new_visited_actors,
            ep_actors, actor_eps, N_EPS,
            result_queue, calls_counter, best_len_val, stop_flag,
            worker_id, local_calls, local_best_depth, local_best_time,
            branch_calls, prune_count, prune_depth_sum,
            ep_names, actor_names)
        chain.pop()
        chain.pop()


# ── WORKER PROCESS ────────────────────────────────────────────────────────────

def worker_process(worker_id, branch_queue, result_queue, calls_counter,
                   best_len_val, stop_flag, input_file):

    (ep_names, actor_names,
     ep_actors, actor_eps,
     ep_name_to_id, actor_name_to_id) = load_data(input_file)
    N_EPS    = len(ep_names)
    local_calls = [0]

    while not stop_flag.value:
        try:
            branch = branch_queue.get(timeout=2)
        except Empty:
            break

        label     = branch.get("label", "")
        branch_id = branch["branch_id"]

        try:
            result_queue.put_nowait(("branch_start", worker_id, label))
        except Exception:
            result_queue.put(("branch_start", worker_id, label))

        branch_calls     = [0]
        prune_count      = [0]
        prune_depth_sum  = [0]
        init_depth       = branch["init_depth"]
        local_best_depth = [init_depth]
        local_best_time  = [time.time()]

        init_chain        = list(branch["init_chain"])
        init_visited_eps  = branch["init_visited_eps"]
        init_visited_act  = branch["init_visited_act"]

        dfs(
            init_chain,
            init_visited_eps,
            init_visited_act,
            ep_actors, actor_eps, N_EPS,
            result_queue, calls_counter, best_len_val, stop_flag,
            worker_id, local_calls, local_best_depth, local_best_time,
            branch_calls, prune_count, prune_depth_sum,
            ep_names, actor_names
        )

        remainder = local_calls[0] % STATUS_INTERVAL
        if remainder:
            with calls_counter.get_lock():
                calls_counter.value += remainder

        try:
            result_queue.put_nowait(("branch_done", worker_id, branch_id))
        except Exception:
            result_queue.put(("branch_done", worker_id, branch_id))

    result_queue.put(("worker_done", worker_id))


# ── DISPLAY HELPERS ───────────────────────────────────────────────────────────

def format_time(s):
    if s < 0:    return "0s"
    if s < 60:   return f"{s:.0f}s"
    if s < 3600: return f"{s/60:.1f}m"
    return f"{int(s//3600)}h {int((s%3600)//60)}m"

def truncate(s, n):
    return s if len(s) <= n else s[:n-3] + "..."

def render_bar(filled, total, width, fill="\u2588", empty="\u2591"):
    if total == 0:
        return empty * width
    f = int(width * filled / total)
    return fill * f + empty * (width - f)

def render_graph(history, width, height):
    if len(history) < 2:
        return ["  (waiting for improvements...)"]
    times  = [h[0] for h in history]
    values = [h[1] for h in history]
    max_v, min_v = max(values), min(values)
    max_t, min_t = max(times),  min(times)
    if max_v == min_v:
        return [f"  {max_v}|" + "\u2593" * width]
    rows = []
    for row in range(height, 0, -1):
        threshold = min_v + (max_v - min_v) * (row - 1) / max(height - 1, 1)
        line = ""
        for col in range(width):
            t   = min_t + (max_t - min_t) * col / max(width - 1, 1)
            val = min_v
            for ht, hv in history:
                if ht <= t:
                    val = hv
            line += "\u2593" if val >= threshold else " "
        rows.append(f"  {int(threshold):>3}|{line}")
    rows.append(f"  {'':>3} " + "\u2500" * width)
    arrow_label = 'elapsed ->'
    rows.append("  {:>3} ".format('0') + "{:>{}}".format(arrow_label, width))
    return rows

def estimate_next(improvement_history, now):
    if len(improvement_history) < 3:
        return "insufficient data"
    gaps = [improvement_history[i][0] - improvement_history[i-1][0]
            for i in range(1, len(improvement_history))]
    if len(gaps) >= 2 and gaps[-2] > 0:
        ratio         = gaps[-1] / gaps[-2]
        predicted_gap = gaps[-1] * ratio
    else:
        predicted_gap = gaps[-1] * 2
    time_since = now - improvement_history[-1][0]
    remaining  = predicted_gap - time_since
    if remaining < 0:
        return "overdue \u2014 could be any moment"
    return f"~{format_time(remaining)} (rough estimate)"


# ── DISPLAY LOOP ──────────────────────────────────────────────────────────────

def display_loop(term, result_queue, calls_counter, best_len_val,
                 stop_flag, start_time, total_eps, total_branches,
                 num_workers, initial_chain_names=None,
                 initial_completed=None):

    best_chain_names    = initial_chain_names or []
    completed_branches  = set(initial_completed) if initial_completed else set()
    worker_info         = {i: {
        "label":           "starting...",
        "branch_start_t":  None,
        "depth":           0,
        "personal_best":   0,
        "personal_best_t": None,
        "branch_calls":    0,
        "prune_count":     0,
        "prune_depth_sum": 0,
        "chain_tail":      [],
        "done":            False,
    } for i in range(num_workers)}

    call_history        = []
    last_calls          = 0
    improvement_history = []
    last_checkpoint_t   = time.time()
    frame               = 0
    spinner             = "|/-\\"
    W                   = max(term.width or 80, 70)
    GRAPH_W             = W - 8
    GRAPH_H             = 4

    def g(text):
        try:    return term.green(text)
        except: return text
    def bg(text):
        try:    return term.bright_green(text)
        except: return text
    def y(text):
        try:    return term.yellow(text)
        except: return text

    with term.fullscreen(), term.hidden_cursor():
        while True:
            frame  += 1
            now     = time.time()
            elapsed = now - start_time

            # Drain queue
            for _ in range(300):
                try:
                    msg  = result_queue.get_nowait()
                    kind = msg[0]

                    if kind == "best":
                        _, chain_names, ts = msg
                        if len(chain_names) > len(best_chain_names):
                            best_chain_names = chain_names
                            improvement_history.append(
                                (ts, (len(chain_names) + 1) // 2)
                            )
                            save_best_file(
                                best_chain_names, ts - start_time,
                                calls_counter.value, num_workers
                            )

                    elif kind == "status":
                        _, wid, tail, depth, pb, pb_t, bc, pc, pds = msg
                        worker_info[wid]["chain_tail"]      = tail
                        worker_info[wid]["depth"]           = depth
                        worker_info[wid]["branch_calls"]    = bc
                        worker_info[wid]["prune_count"]     = pc
                        worker_info[wid]["prune_depth_sum"] = pds
                        if pb > worker_info[wid]["personal_best"]:
                            worker_info[wid]["personal_best"]   = pb
                            worker_info[wid]["personal_best_t"] = pb_t

                    elif kind == "branch_start":
                        _, wid, label = msg
                        worker_info[wid].update({
                            "label":           label,
                            "branch_start_t":  now,
                            "branch_calls":    0,
                            "prune_count":     0,
                            "prune_depth_sum": 0,
                            "personal_best":   4,
                            "personal_best_t": now,
                        })

                    elif kind == "branch_done":
                        _, wid, bid = msg
                        completed_branches.add(bid)

                    elif kind == "worker_done":
                        worker_info[msg[1]]["done"] = True

                except Empty:
                    break
                except Exception:
                    break

            # Hourly checkpoint
            if now - last_checkpoint_t >= CHECKPOINT_INTERVAL:
                save_checkpoint(best_chain_names, completed_branches,
                                calls_counter.value, num_workers,
                                [], [])
                last_checkpoint_t = now

            # Stats
            calls      = calls_counter.value
            delta      = calls - last_calls
            last_calls = calls
            call_history.append(delta)
            if len(call_history) > 10:
                call_history.pop(0)
            cps = sum(call_history) / max(len(call_history), 1) / 0.5

            best_len      = (len(best_chain_names) + 1) // 2
            best_eps      = best_chain_names[0::2]
            best_acts     = best_chain_names[1::2]
            pct_chain     = best_len / total_eps * 100 if total_eps else 0
            branches_done = len(completed_branches)
            pct_branches  = branches_done / total_branches * 100 if total_branches else 0

            rows = []

            # Header
            rows.append(g((" >>> TWILIGHT ZONE CHAIN SOLVER — PC EDITION v6 <<< ").center(W)))
            rows.append(g("-" * W))

            spin = spinner[frame % len(spinner)]
            rows.append(
                bg(f"  {spin} RUNNING  ") +
                g(f"| Uptime: ") + bg(f"{format_time(elapsed):<10}") +
                g(f"| Calls: ") + bg(f"{calls:>14,}") +
                g(f"| CPS: ") + bg(f"{cps:>8.0f}") +
                g(f"| Workers: ") + bg(str(num_workers))
            )
            rows.append(g("-" * W))

            since = ""
            if improvement_history:
                secs  = now - improvement_history[-1][0]
                since = f"  ({format_time(secs)} ago)"
            rows.append(
                y(f"  >> BEST CHAIN: ") +
                bg(str(best_len)) +
                g(f" / {total_eps} episodes") +
                g(since)
            )
            bar_w   = W - 18
            filled  = int(bar_w * pct_chain / 100)
            bar     = "\u2588" * filled + "\u2591" * (bar_w - filled)
            rows.append(g(f"  Coverage  [") + bg(bar[:filled]) +
                        g(bar[filled:]) + g(f"]  {pct_chain:.1f}%"))

            rows.append(g("-" * W))
            rows.append(g("  BEST CHAIN TAIL (last 5):"))
            tail_start = max(0, len(best_eps) - 5)
            for i in range(tail_start, len(best_eps)):
                rows.append(bg(truncate(f"    {i+1:>3}. {best_eps[i]}", W - 2)))
                if i < len(best_acts):
                    rows.append(g(f"         \u2514\u2500[ {best_acts[i]} ]"))

            rows.append(g("-" * W))
            rows.append(g("  CHAIN LENGTH OVER TIME:"))
            for gr in render_graph(improvement_history, GRAPH_W, GRAPH_H):
                rows.append(g(gr))

            rows.append(g("-" * W))
            est = estimate_next(improvement_history, now)
            rows.append(g(f"  Est. time to next improvement: ") + y(est))

            rows.append(g("-" * W))
            rows.append(
                g(f"  BRANCH QUEUE: ") +
                bg(f"{branches_done}") +
                g(f" / {total_branches} branches completed  ") +
                y(f"({pct_branches:.1f}% of total work)")
            )
            bq_bar_w = W - 18
            bq_filled = int(bq_bar_w * pct_branches / 100)
            bq_bar = "\u2588" * bq_filled + "\u2591" * (bq_bar_w - bq_filled)
            rows.append(g("  Progress  [") + bg(bq_bar[:bq_filled]) +
                        g(bq_bar[bq_filled:]) + g("]"))

            rows.append(g("-" * W))
            rows.append(g("  WORKERS — LIVE EXPLORATION:"))

            for wid in range(num_workers):
                info = worker_info[wid]
                rows.append("")
                if info["done"]:
                    rows.append(g(f"  Worker {wid+1}: ") + y("[FINISHED]"))
                    rows.append(""); rows.append(""); rows.append("")
                    continue

                label      = info["label"]
                bt         = info["branch_start_t"]
                elapsed_br = format_time(now - bt) if bt else "\u2014"
                depth      = info["depth"]
                pb         = info["personal_best"]
                pb_t       = info["personal_best_t"]
                pb_ago     = format_time(now - pb_t) if pb_t else "\u2014"
                pc         = info["prune_count"]
                pds        = info["prune_depth_sum"]
                bc         = info["branch_calls"]
                pr_rate    = f"{pc/bc*100:.1f}%" if bc > 0 else "\u2014"
                avg_dep    = f"{pds/pc:.1f}" if pc > 0 else "\u2014"

                rows.append(truncate(
                    bg(f"  Worker {wid+1}: ") +
                    g(f"[") + y(label) + g(f"]") +
                    g(f"  \u2014  exploring for ") + bg(elapsed_br),
                    W
                ))
                rows.append(g(
                    f"    depth: {depth:<4}  "
                    f"personal best: {pb}  (found {pb_ago} ago)  "
                    f"calls: {bc:,}"
                ))
                rows.append(g(
                    f"    prune rate: {pr_rate:<8}  "
                    f"avg depth at prune: {avg_dep}"
                ))
                tail = info["chain_tail"]
                if tail:
                    tail_str = " \u2192 ".join(truncate(e, 20) for e in tail[-3:])
                    rows.append(g(truncate(f"    ...{tail_str}", W - 2)))
                else:
                    rows.append(g("    starting..."))

            rows.append(g("-" * W))
            cp_ago = format_time(now - last_checkpoint_t)
            rows.append(g(
                f"  Output: {OUTPUT_FILE}  |  "
                f"Checkpoint: {cp_ago} ago  |  Ctrl+C to stop"
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

    return best_chain_names, completed_branches


# ── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    (ep_names, actor_names,
     ep_actors, actor_eps,
     ep_name_to_id, actor_name_to_id) = load_data(INPUT_FILE)

    TOTAL_EPISODES = len(ep_names)
    N_EPS          = TOTAL_EPISODES

    print("Generating branch list...")
    all_branches   = generate_branches(ep_names, actor_names,
                                       ep_actors, actor_eps,
                                       ep_name_to_id, actor_name_to_id)
    TOTAL_BRANCHES = len(all_branches)
    print(f"Episodes      : {TOTAL_EPISODES}")
    print(f"Bridge actors : {len(actor_names)}")
    print(f"Total branches: {TOTAL_BRANCHES}")

    if NUM_WORKERS is None:
        NUM_WORKERS = os.cpu_count() or 4
    print(f"Workers       : {NUM_WORKERS}")

    # Load checkpoint (names-based, compatible with inject scripts)
    init_chain_names, completed_branches, prior_calls = load_checkpoint()

    # Filter remaining branches
    completed_ids      = {tuple(b) for b in completed_branches}
    remaining_branches = [b for b in all_branches
                          if b["branch_id"] not in completed_ids]
    skipped = TOTAL_BRANCHES - len(remaining_branches)
    if skipped:
        print(f"Resuming: skipping {skipped} completed branches")
    print(f"Branches to explore: {len(remaining_branches)}")
    print()

    # Best chain: convert name list to ID list for best_len_val
    init_best_len = (len(init_chain_names) + 1) // 2 if init_chain_names else 0

    branch_queue  = Queue()
    for branch in remaining_branches:
        branch_queue.put(branch)

    result_queue  = Queue(maxsize=10000)
    calls_counter = Value('l', prior_calls)
    best_len_val  = Value('i', init_best_len)
    stop_flag     = Value('b', 0)
    GLOBAL_START  = time.time()

    workers = []
    for i in range(NUM_WORKERS):
        p = Process(
            target=worker_process,
            args=(i, branch_queue, result_queue, calls_counter,
                  best_len_val, stop_flag, INPUT_FILE),
            daemon=True
        )
        p.start()
        workers.append(p)

    term             = Terminal()
    best_chain_names = init_chain_names or []
    try:
        result = display_loop(
            term, result_queue, calls_counter, best_len_val,
            stop_flag, GLOBAL_START, TOTAL_EPISODES, TOTAL_BRANCHES,
            NUM_WORKERS,
            initial_chain_names=init_chain_names,
            initial_completed=completed_ids
        )
        if result:
            best_chain_names, completed_branches = result
    except KeyboardInterrupt:
        stop_flag.value = 1
        while True:
            try:
                msg = result_queue.get_nowait()
                if msg[0] == "best" and len(msg[1]) > len(best_chain_names):
                    best_chain_names = msg[1]
                elif msg[0] == "branch_done":
                    completed_branches.add(msg[2])
            except Exception:
                break

    for p in workers:
        p.terminate()

    elapsed = time.time() - GLOBAL_START

    if best_chain_names:
        save_best_file(best_chain_names, elapsed,
                       calls_counter.value, NUM_WORKERS)
    else:
        print("Warning: no best chain found — best chain file not overwritten.")

    save_checkpoint(best_chain_names, completed_branches,
                    calls_counter.value, NUM_WORKERS,
                    ep_names, actor_names)

    episodes = best_chain_names[0::2]
    actors_  = best_chain_names[1::2]
    assert len(set(episodes)) == len(episodes), "ERROR: duplicate episode!"
    assert len(set(actors_))  == len(actors_),  "ERROR: duplicate actor!"

    print(term.normal + term.clear)
    print(f"\n{'='*60}")
    print(f"FINAL RESULT: {len(episodes)} episodes")
    print(f"Time: {format_time(elapsed)}  |  Calls: {calls_counter.value:,}")
    print(f"Branches completed: {len(completed_branches)} / {TOTAL_BRANCHES}")
    print(f"Saved to: {OUTPUT_FILE}")
    print(f"{'='*60}\n")
    for i, ep in enumerate(episodes):
        print(f"{i+1:>3}. {ep}")
        if i < len(actors_):
            print(f"       \u2514\u2500[ {actors_[i]} ]")
