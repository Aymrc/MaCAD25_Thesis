# enriched.py
# Soft-weighted, randomized program allocation without changing topology.
# - No modes. Each run varies automatically unless --seed is provided.
# - Programs split and scatter across levels.
# - Prints structured lines to STDOUT for easy capture by a CLI/daemon/FastAPI.

import json
from copy import deepcopy
from collections import defaultdict
import math
import random
import time
from datetime import datetime
from typing import Union, Dict, Any, List, Optional
import argparse
import os
import sys
import csv
import os


VERSION = "1.1.0"


# ---------------------------- Core ----------------------------------

def enrich_without_changing_topology(
    massing_graph_or_path: Union[str, Dict[str, Any]],
    brief_graph_or_path: Union[str, Dict[str, Any]],
    *,
    rng_seed: Optional[int] = None,            # None => auto from current time
    noise_strength: float = 1.0,               # random jitter added to level weights
    assign_split_granularity_sqm: float = 220, # target chunk size per allocation step
    min_chunk_sqm: float = 60,                 # never allocate less than this per step (unless program need is tiny)
    max_splits_per_program: int = 400,         # safety
) -> Dict[str, Any]:
    """
    Assigns programs from a brief to level nodes in a massing graph without changing topology.
    - Only adds attributes on *level* nodes:
        program_assignments: [{program_id, typology, area}]
        remaining_area: float
        primary_program: str | None (program_id with max area on that level)
    - Uses soft weights (not rules) + randomness, so each run can differ.
    - Programs are split into chunks and scattered where capacity + weight suggest.

    Returns enriched graph with a 'meta' block recording seed and allocation summary.
    """

    # ---- RNG ----
    if rng_seed is None:
        rng_seed = int(time.time() * 1000) % (2**31 - 1)
    rng = random.Random(rng_seed)

    # ---- load if paths ----
    def _load(x):
        if isinstance(x, str):
            with open(x, "r", encoding="utf-8") as f:
                return json.load(f)
        return x

    massing_graph = _load(massing_graph_or_path)
    brief_graph = _load(brief_graph_or_path)

    # ---- defensive copy ----
    g = deepcopy(massing_graph)
    nodes = g.get("nodes", [])
    link_key = "links" if "links" in g else ("edges" if "edges" in g else "links")
    links = g.get(link_key, [])

    # --------- helpers: identify levels ----------
    def is_level(node: Dict[str, Any]) -> bool:
        if node.get("type") == "level":
            return True
        nid = node.get("id", "")
        if "-L" in nid:
            suf = nid.split("-L", 1)[1]
            return suf.isdigit()
        return False

    def level_index(nid: str) -> int:
        if "-L" in nid:
            try:
                return int(nid.split("-L", 1)[1])
            except Exception:
                return 0
        return 0

    level_nodes = [n for n in nodes if is_level(n)]
    if not level_nodes:
        # Nothing to do; return original, but record meta
        out = deepcopy(g)
        out["meta"] = {
            "note": "No level nodes detected; enrichment skipped.",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "version": VERSION,
        }
        return out

    capacities = {n["id"]: float(n.get("area", 0.0)) for n in level_nodes}
    used = defaultdict(float)

    # ---------- simple graph measures: degree centrality ----------
    degree = defaultdict(int)
    for e in links:
        s, t = e.get("source"), e.get("target")
        if s:
            degree[s] += 1
        if t:
            degree[t] += 1

    # link type "plot" marks plot-connected nodes if present (optional)
    plot_edges = [e for e in links if e.get("type") == "plot"]
    plot_connected_ids = set()
    for e in plot_edges:
        s, t = e.get("source"), e.get("target")
        if s and s != "PLOT":
            plot_connected_ids.add(s)
        if t and t != "PLOT":
            plot_connected_ids.add(t)

    # ---------- brief: normalize typologies minimally ----------
    def norm_typ(t: str) -> str:
        if not t:
            return ""
        return t.strip().lower().replace("-", "_").replace(" ", "_")

    brief_program_nodes = [n for n in brief_graph.get("nodes", []) if n.get("id") != "masterplan"]
    for n in brief_program_nodes:
        n["typology"] = norm_typ(n.get("typology", ""))

    # "Site-only" items we don't put on floors (optional)
    site_like = {"public_space", "open_space", "green_space", "park", "plaza", "landscape", "square"}
    building_programs = [n for n in brief_program_nodes if n.get("typology") not in site_like]
    site_programs = [n for n in brief_program_nodes if n.get("typology") in site_like]

    program_need = {n["id"]: float(n.get("footprint", 0.0)) for n in building_programs}
    program_meta = {n["id"]: n for n in building_programs}

    # ---------- level weights (soft, non-binding) ----------
    def base_weight(level_node: Dict[str, Any], program_id: str) -> float:
        """A light-touch weight: ground helps 'public-ish' but doesn't force it; all levels remain eligible."""
        typ = program_meta[program_id].get("typology", "")
        nid = level_node["id"]
        z = level_index(nid)
        area = float(level_node.get("area", 0.0))
        deg = degree.get(nid, 0)

        # start from something benign
        w = 0.05 * math.log(max(area, 1.0) + 1.0) + 0.02 * deg

        # soft public-ish encouragement near ground/plot, but not exclusive
        if typ in {"leisure", "cultural", "retail", "education", "healthcare"}:
            if z == 0:
                w += 1.2
            if nid in plot_connected_ids:
                w += 0.5
            # very light decay with height, still allowing high floors
            w += max(0.0, 0.4 - 0.03 * z)

        # very light preferences (all soft)
        if typ in {"office"}:
            w += 0.02 * min(z, 12)  # higher is ok but tiny effect
        if typ in {"residential"}:
            w += 0.03 * min(z, 15)  # upper floors slightly attractive, but very soft
        if typ in {"parking"} and z <= 1:
            w += 0.8  # helps ground/low placement but still not forced

        return w

    # ---------- randomization helpers ----------
    def jitter(rng: random.Random) -> float:
        # zero-mean noise; scale controlled by noise_strength
        return noise_strength * rng.triangular(-1.0, 1.0, 0.0)

    def softmax_choice(weights: Dict[str, float], rng: random.Random) -> Optional[str]:
        """Turn weights into probabilities via softmax; select a level id."""
        if not weights:
            return None
        vals = list(weights.values())
        m = max(vals)
        exps = [math.exp(v - m) for v in vals]
        s = sum(exps)
        if s <= 0:
            return max(weights.items(), key=lambda kv: kv[1])[0]
        probs = [e / s for e in exps]
        r = rng.random()
        cum = 0.0
        keys = list(weights.keys())
        for k, p in zip(keys, probs):
            cum += p
            if r <= cum:
                return k
        return keys[-1]

    # ---------- precompute base weights per program x level ----------
    level_ids = [ln["id"] for ln in level_nodes]
    base_weights = {
        pid: {lid: base_weight(next(n for n in level_nodes if n["id"] == lid), pid) for lid in level_ids}
        for pid in program_need.keys()
    }

    # ---------- allocation loop with chunking & randomness ----------
    # randomize program processing order each run
    program_order = list(program_need.keys())
    rng.shuffle(program_order)

    allocations = defaultdict(list)  # level_id -> [(program_id, typology, area)]
    remaining = dict(program_need)
    allocation_trace = []

    for pid in program_order:
        need = remaining[pid]
        typ = program_meta[pid].get("typology", "")
        splits = 0

        while need > 1e-6 and splits < max_splits_per_program:
            # candidate levels with capacity left
            cand = {}
            for lid in level_ids:
                cap_left = capacities[lid] - used[lid]
                if cap_left > 1e-6:
                    # soft weight + random jitter; keep everything eligible
                    w = base_weights[pid][lid] + jitter(rng)
                    # mild clustering bonus if this level already hosts the same program
                    if any(a[0] == pid for a in allocations[lid]):
                        w += 0.15
                    # small encouragement for levels with more leftover
                    w += 0.01 * math.log(max(cap_left, 1.0))
                    cand[lid] = w

            if not cand:
                break

            chosen = softmax_choice(cand, rng)
            if chosen is None:
                break

            cap_left = capacities[chosen] - used[chosen]
            if cap_left <= 1e-6:
                continue

            # pick a chunk size with randomness around target granularity
            base_chunk = assign_split_granularity_sqm
            # 60%..140% of base, but not smaller than min_chunk_sqm
            chunk = max(min_chunk_sqm, base_chunk * (0.6 + 0.8 * rng.random()))
            take = float(min(cap_left, need, chunk))

            if take <= 1e-6:
                break

            allocations[chosen].append((pid, typ, take))
            used[chosen] += take
            need -= take
            splits += 1

            allocation_trace.append({
                "program_id": pid,
                "typology": typ,
                "chosen_level": chosen,
                "take_sqm": round(take, 3),
                "need_remaining": round(need, 3),
            })

        remaining[pid] = float(need)

    # ---------- write back to level nodes ----------
    for n in nodes:
        if not is_level(n):
            continue
        lid = n["id"]
        assigned = allocations.get(lid, [])
        n["program_assignments"] = [
            {"program_id": pid, "typology": typ, "area": float(a)}
            for (pid, typ, a) in assigned
        ]
        n["remaining_area"] = float(capacities.get(lid, 0.0) - used.get(lid, 0.0))
        # primary program: by area
        n["primary_program"] = (max(assigned, key=lambda x: x[2])[0] if assigned else None)

    # ---------- output meta & return ----------
    out = {k: deepcopy(v) for k, v in g.items()}
    out["nodes"] = nodes
    out[link_key] = links

    out["meta"] = out.get("meta", {})
    out["meta"]["site_programs"] = [
        {"id": n["id"], "typology": n["typology"], "need_sqm": float(n.get("footprint", 0.0))}
        for n in site_programs
    ]
    out["meta"]["unallocated_program_area"] = {
        pid: float(v) for pid, v in remaining.items() if v > 1e-6
    }
    out["meta"]["allocation_log"] = {
        "rng_seed": int(rng_seed),
        "noise_strength": float(noise_strength),
        "assign_split_granularity_sqm": float(assign_split_granularity_sqm),
        "min_chunk_sqm": float(min_chunk_sqm),
        "max_splits_per_program": int(max_splits_per_program),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "version": VERSION,
        "trace_sample": allocation_trace[:2000],  # cap to keep file reasonable
        "notes": "Soft weights + randomness. Topology unchanged; only level node attributes were added.",
    }

    return out


# ------------------------- Batch / CLI --------------------------------

def generate_enriched_variants(
    massing_path: str,
    brief_path: str,
    *,
    n_variants: int = 1,
    outdir: Optional[str] = None,
    rng_seed: Optional[int] = None,       # if provided -> reproducible batch
    noise_strength: float = 1.0,
    assign_split_granularity_sqm: float = 220,
    min_chunk_sqm: float = 60,
    stdout: bool = False,                 # if True, print JSON for the FIRST variant to STDOUT
    log_csv: Optional[str] = None,        # if provided, append run/variant info as CSV rows
) -> List[str]:
    """
    Generate N variants. If rng_seed is None, each variant uses a time-based seed.
    Returns list of output file paths (empty if stdout-only).
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    mstem = os.path.splitext(os.path.basename(massing_path))[0]
    outdir = outdir or os.path.dirname(massing_path) or "."
    os.makedirs(outdir, exist_ok=True)

    out_paths = []
    base = rng_seed if rng_seed is not None else int(time.time() * 1000)

    # CSV logging setup
    if log_csv:
        header = ["timestamp", "version", "massing", "brief", "variant_index", "seed", "noise",
                  "granularity", "min_chunk", "out_path"]
        file_exists = os.path.exists(log_csv)
        with open(log_csv, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(header)

    for i in range(n_variants):
        seed_i = (base + i) if rng_seed is not None else (int(time.time() * 1000) + i)
        enriched = enrich_without_changing_topology(
            massing_path,
            brief_path,
            rng_seed=seed_i,
            noise_strength=noise_strength,
            assign_split_granularity_sqm=assign_split_granularity_sqm,
            min_chunk_sqm=min_chunk_sqm,
        )

        # STDOUT option: emit first variant as full JSON (for piping to an API)
        if stdout and i == 0:
            sys.stdout.write(json.dumps(enriched, indent=2))
            sys.stdout.write("\n")
            sys.stdout.flush()

        out_path = os.path.join(
            outdir,
            f"{mstem}_enriched_seed{seed_i}_{ts}_v{i+1}.json"
        )
        # Always write files (even if stdout), unless the caller wants *only* stdout:
        if not stdout or n_variants > 1:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(enriched, f, indent=2)
            out_paths.append(out_path)

        # per-variant structured line (easy to parse)
        print(json.dumps({
            "type": "ENRICHED_VARIANT",
            "index": i + 1,
            "n_variants": n_variants,
            "seed": seed_i,
            "path": out_path if (not stdout or n_variants > 1) else "(stdout)",
            "timestamp": ts,
            "version": VERSION,
        }))

        # append CSV log if requested
        if log_csv:
            with open(log_csv, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    ts, VERSION, massing_path, brief_path, i + 1, seed_i,
                    noise_strength, assign_split_granularity_sqm, min_chunk_sqm,
                    out_path if (not stdout or n_variants > 1) else "(stdout)"
                ])

    return out_paths


def _parse_args():
    p = argparse.ArgumentParser(description="Enrich massing graph with randomized, soft-weighted program assignments (no topology changes). Emits structured logs for seeds/paths.")
    p.add_argument("--massing", type=str, required=False,
                   default=r"C:\path\to\massing_graph.json",
                   help="Path to massing_graph.json")
    p.add_argument("--brief", type=str, required=False,
                   default=r"C:\path\to\brief_graph.json",
                   help="Path to brief_graph.json")

    p.add_argument("--n_variants", type=int, default=1,
                   help="How many variants to generate (>=1).")
    p.add_argument("--seed", type=int, default=None,
                   help="Optional base seed for reproducible results. If omitted, each run differs.")
    p.add_argument("--noise", type=float, default=1.0,
                   help="Random noise strength added to level weights (higher => more variety).")
    p.add_argument("--granularity", type=float, default=220.0,
                   help="Target chunk size (sqm) for splitting programs across levels.")
    p.add_argument("--min_chunk", type=float, default=60.0,
                   help="Minimum chunk size (sqm) per allocation step.")
    p.add_argument("--outdir", type=str, default=None,
                   help="Output directory; defaults to massing file directory.")
    p.add_argument("--stdout", action="store_true",
                   help="Emit the FIRST variant to STDOUT as JSON (still saves files unless n_variants==1).")
    p.add_argument("--log_csv", type=str, default=None,
                   help="Optional CSV file to append seeds/params/paths.")
    return p.parse_args()


def main():
    args = _parse_args()

    # Allow environment override for seed if CLI omitted (handy for containerized runs)
    env_seed = os.getenv("ENRICHED_SEED_BASE")
    base_seed = args.seed if args.seed is not None else (int(env_seed) if env_seed not in (None, "") else None)

    # Structured one-line banner (easy to parse)
    banner = {
        "type": "ENRICHED_RUN",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "version": VERSION,
        "massing": args.massing,
        "brief": args.brief,
        "n_variants": args.n_variants,
        "base_seed": base_seed,
        "noise": args.noise,
        "granularity": args.granularity,
        "min_chunk": args.min_chunk,
        "outdir": args.outdir or (os.path.dirname(args.massing) or "."),
        "stdout": bool(args.stdout),
        "log_csv": args.log_csv or "",
    }
    print(json.dumps(banner))

    paths = generate_enriched_variants(
        massing_path=args.massing,
        brief_path=args.brief,
        n_variants=args.n_variants,
        outdir=args.outdir,
        rng_seed=base_seed,
        noise_strength=args.noise,
        assign_split_granularity_sqm=args.granularity,
        min_chunk_sqm=args.min_chunk,
        stdout=args.stdout,
        log_csv=args.log_csv,
    )

    # Summary line at end
    print(json.dumps({
        "type": "ENRICHED_DONE",
        "count": len(paths) if paths else (1 if args.stdout else 0),
        "version": VERSION
    }))


if __name__ == "__main__":
    main()
