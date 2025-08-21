# enriched.py
# Soft-weighted, randomized program allocation without changing topology.
# - No modes. Each run varies automatically unless --seed is provided.
# - Programs split and scatter across levels.
# - Resolves default relative paths:
#     massing = <project_root>/knowledge/massing_graph.json
#     brief   = most recently modified 'brief_graph*.json' anywhere under <project_root>/knowledge/
# - Emits structured logs for seeds/paths, and can print JSON to stdout.
# - Output filenames: it{N}.json (auto-incrementing, no overwrite)
# - Default output directory: <project_root>/enriched_graph/iteration/

import json
from copy import deepcopy
from collections import defaultdict
import math
import random
import time
from datetime import datetime, timezone
from typing import Union, Dict, Any, List, Optional, Tuple
import argparse
import os
import sys
import csv
import re
from pathlib import Path

VERSION = "3.2.7"

# ---------------------------------------------------------------------
# Timestamp helper
# ---------------------------------------------------------------------
def utc_now_isoz() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

# =============================================================================
# Default path resolution (relative, robust)
# =============================================================================


def _find_project_root(start: Path, search_subdir: str = "knowledge",
                       max_up: int = 7) -> Path:
    """
    Walk up from 'start' to find a directory that contains 'search_subdir'.
    """
    cur = start.resolve()
    for _ in range(max_up):
        if (cur / search_subdir).is_dir():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return start

def _find_any_brief_graph(knowledge_dir: Path) -> Optional[Path]:
    """
    Recursively search for any 'brief_graph*.json' under 'knowledge_dir' and
    return the most recently modified one. Returns None if none found.
    """
    if not knowledge_dir.is_dir():
        return None
    candidates = list(knowledge_dir.rglob("brief_graph*.json"))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]

def resolve_default_paths() -> Tuple[Path, Path]:
    """
    Resolve massing and brief paths based on the script location:
      massing = <project_root>/knowledge/massing_graph.json
      brief   = most recent 'brief_graph*.json' anywhere under <project_root>/knowledge/
    Raises FileNotFoundError if massing or brief cannot be found.
    """
    script_dir = Path(__file__).resolve().parent
    project_root = _find_project_root(script_dir)
    knowledge_dir = (project_root / "knowledge")
    massing_path = knowledge_dir / "massing_graph.json"
    brief_path = _find_any_brief_graph(knowledge_dir)

    if not massing_path.is_file():
        raise FileNotFoundError(
            f"Could not find massing_graph.json at '{massing_path}'. "
            f"Looked under project_root='{project_root}'."
        )
    if brief_path is None or not brief_path.is_file():
        raise FileNotFoundError(
            f"Could not find any 'brief_graph*.json' under '{knowledge_dir}'."
        )
    return massing_path, brief_path

def resolve_default_outdir(massing_path: Optional[Union[str, Path]] = None) -> Path:
    """
    Default iterations folder: <project_root>/enriched_graph/iteration/
    If massing_path is provided, derive project_root as parent of its 'knowledge' dir.
    Otherwise, derive from script location.
    """
    if massing_path is not None:
        mp = Path(massing_path).resolve()
        knowledge_dir = mp.parent
        project_root = knowledge_dir.parent
    else:
        script_dir = Path(__file__).resolve().parent
        project_root = _find_project_root(script_dir)
    return project_root / "enriched_graph" / "iteration"

# =============================================================================
# Utils: robust field access
# =============================================================================

_AREA_KEYS_NODE = ["area", "area_m2", "area_doc", "gfa", "area_sqm", "sqm", "net_area", "gross_area"]
_AREA_KEYS_BRIEF = ["footprint", "area", "area_m2", "gfa", "area_sqm", "sqm", "required_area", "need_sqm", "target_sqm"]

def get_area_from_node(n: Dict[str, Any]) -> float:
    for k in _AREA_KEYS_NODE:
        v = n.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    # nested structures like {"area": {"m2": 123}}
    v = n.get("area")
    if isinstance(v, dict):
        for kk in ("m2", "sqm", "value"):
            if isinstance(v.get(kk), (int, float)):
                return float(v[kk])
    return 0.0

def get_area_from_program_node(n: Dict[str, Any]) -> float:
    for k in _AREA_KEYS_BRIEF:
        v = n.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    v = n.get("area")
    if isinstance(v, dict):
        for kk in ("m2", "sqm", "value"):
            if isinstance(v.get(kk), (int, float)):
                return float(v[kk])
    return 0.0

def norm_typ(t: Optional[str]) -> str:
    if not t:
        return ""
    return t.strip().lower().replace("-", "_").replace(" ", "_")

def extract_programs_from_brief(brief_graph: Dict[str, Any]) -> Tuple[Dict[str, float], Dict[str, Dict[str, Any]]]:
    """
    Try multiple structures to get program needs:
      1) program nodes with area fields
      2) meta dictionaries like 'unallocated_program_area', 'programs', 'program_targets', etc.
    Returns (program_need, program_meta).
    """
    program_need: Dict[str, float] = {}
    program_meta: Dict[str, Dict[str, Any]] = {}

    # 1) nodes
    for n in brief_graph.get("nodes", []):
        if n.get("id") == "masterplan":
            continue
        pid = str(n.get("id") or n.get("name") or "").strip()
        if not pid:
            continue
        area = get_area_from_program_node(n)
        if area and area > 0:
            typ = norm_typ(n.get("typology") or pid)
            program_need[pid] = float(area)
            program_meta[pid] = {"id": pid, "typology": typ, **{k: n.get(k) for k in n.keys()}}

    # 2) meta fallbacks
    meta = brief_graph.get("meta", {})
    for key in ["unallocated_program_area", "programs", "program_requirements", "program_targets", "program_area_targets"]:
        maybe = meta.get(key)
        if isinstance(maybe, dict):
            for pid, area in maybe.items():
                if not isinstance(area, (int, float)):
                    continue
                pid_str = str(pid)
                typ = norm_typ(pid_str)
                if pid_str not in program_need or program_need[pid_str] <= 0:
                    program_need[pid_str] = float(area)
                if pid_str not in program_meta:
                    program_meta[pid_str] = {"id": pid_str, "typology": typ}

    # Filter zero/negative
    program_need = {k: float(v) for k, v in program_need.items() if v and v > 0}
    return program_need, program_meta

# =============================================================================
# Core enrichment
# =============================================================================

def enrich_without_changing_topology(
    massing_graph_or_path: Union[str, Path, Dict[str, Any]],
    brief_graph_or_path: Union[str, Path, Dict[str, Any]],
    *,
    rng_seed: Optional[int] = None,            # None => auto from current time
    noise_strength: float = 1.0,               # random jitter added to level weights
    assign_split_granularity_sqm: float = 220, # target chunk size per allocation step
    min_chunk_sqm: float = 60,                 # minimum chunk per step
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
    # RNG
    if rng_seed is None:
        rng_seed = int(time.time() * 1000) % (2**31 - 1)
    rng = random.Random(rng_seed)

    # Load helpers
    def _load(x):
        if isinstance(x, (str, Path)):
            with open(x, "r", encoding="utf-8") as f:
                return json.load(f)
        return x

    massing_graph = _load(massing_graph_or_path)
    brief_graph = _load(brief_graph_or_path)

    # Copy & basics
    g = deepcopy(massing_graph)
    nodes = g.get("nodes", [])
    link_key = "links" if "links" in g else ("edges" if "edges" in g else "links")
    links = g.get(link_key, [])

    # Level helpers
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
        out = deepcopy(g)
        out["meta"] = {
            "note": "No level nodes detected; enrichment skipped.",
            "timestamp": utc_now_isoz(),
            "version": VERSION,
        }
        return out

    # Capacities
    capacities = {n["id"]: float(get_area_from_node(n)) for n in level_nodes}
    used = defaultdict(float)

    # Simple degree centrality
    degree = defaultdict(int)
    for e in links:
        s, t = e.get("source"), e.get("target")
        if s:
            degree[s] += 1
        if t:
            degree[t] += 1

    # Plot connectivity (rare in your sample)
    plot_edges = [e for e in links if e.get("type") == "plot"]
    plot_connected_ids = set()
    for e in plot_edges:
        s, t = e.get("source"), e.get("target")
        if s and s != "PLOT":
            plot_connected_ids.add(s)
        if t and t != "PLOT":
            plot_connected_ids.add(t)

    # Brief programs
    program_need, program_meta = extract_programs_from_brief(brief_graph)

    # Site-like vs building programs
    site_like = {"public_space", "open_space", "green_space", "park", "plaza", "landscape", "square"}
    building_program_ids = [pid for pid in program_need.keys() if norm_typ(program_meta.get(pid, {}).get("typology", pid)) not in site_like]
    site_program_ids = [pid for pid in program_need.keys() if norm_typ(program_meta.get(pid, {}).get("typology", pid)) in site_like]

    # Level weights (soft tendencies + noise)
    def base_weight(level_node: Dict[str, Any], program_id: str) -> float:
        typ = norm_typ(program_meta.get(program_id, {}).get("typology") or program_id)
        nid = level_node["id"]
        z = level_index(nid)
        area_val = float(get_area_from_node(level_node))
        deg = degree.get(nid, 0)

        w = 0.05 * math.log(max(area_val, 1.0) + 1.0) + 0.02 * deg

        if typ in {"leisure", "cultural", "retail", "education", "healthcare", "recreational"}:
            if z == 0:
                w += 1.2
            if nid in plot_connected_ids:
                w += 0.5
            w += max(0.0, 0.4 - 0.03 * z)

        if typ in {"office"}:
            w += 0.02 * min(z, 12)
        if typ in {"residential"}:
            w += 0.03 * min(z, 15)
        if typ in {"parking"} and z <= 1:
            w += 0.8

        return w

    def jitter(rng: random.Random) -> float:
        return noise_strength * rng.triangular(-1.0, 1.0, 0.0)

    def softmax_choice(weights: Dict[str, float], rng: random.Random) -> Optional[str]:
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

    # Precompute base weights
    id_to_level = {ln["id"]: ln for ln in level_nodes}
    level_ids = list(id_to_level.keys())
    base_weights = {
        pid: {lid: base_weight(id_to_level[lid], pid) for lid in level_ids}
        for pid in building_program_ids
    }

    # Allocation
    program_order = list(building_program_ids)
    random.Random(rng_seed + 13).shuffle(program_order)

    allocations = defaultdict(list)  # level_id -> List[(program_id, typology, area)]
    remaining = {pid: float(program_need[pid]) for pid in building_program_ids}
    allocation_trace = []

    for pid in program_order:
        need = remaining[pid]
        typ = norm_typ(program_meta.get(pid, {}).get("typology") or pid)
        splits = 0

        while need > 1e-6 and splits < max_splits_per_program:
            cand = {}
            for lid in level_ids:
                cap_left = capacities[lid] - used[lid]
                if cap_left > 1e-6:
                    w = base_weights[pid][lid] + jitter(rng)
                    if any(a[0] == pid for a in allocations[lid]):
                        w += 0.15  # mild clustering preference
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

            base_chunk = assign_split_granularity_sqm
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

    # Annotate nodes
    for n in nodes:
        nid = n.get("id")
        if nid not in capacities:
            continue
        assigned = allocations.get(nid, [])
        n["program_assignments"] = [
            {"program_id": pid, "typology": typ, "area": float(a)}
            for (pid, typ, a) in assigned
        ]
        n["remaining_area"] = float(capacities.get(nid, 0.0) - used.get(nid, 0.0))
        n["primary_program"] = (max(assigned, key=lambda x: x[2])[0] if assigned else None)

    # Output
    out = {k: deepcopy(v) for k, v in g.items()}
    out["nodes"] = nodes
    out[link_key] = links
    out["meta"] = out.get("meta", {})

    # Record site programs (just documented, not placed)
    out["meta"]["site_programs"] = out["meta"].get("site_programs", [])
    for pid in site_program_ids:
        out["meta"]["site_programs"].append({
            "id": pid,
            "typology": norm_typ(program_meta.get(pid, {}).get("typology") or pid),
            "need_sqm": float(program_need[pid])
        })

    out["meta"]["unallocated_program_area"] = {
        pid: float(v) for pid, v in remaining.items() if v > 1e-6
    }
    out["meta"]["allocation_log"] = {
        "rng_seed": int(rng_seed),
        "noise_strength": float(noise_strength),
        "assign_split_granularity_sqm": float(assign_split_granularity_sqm),
        "min_chunk_sqm": float(min_chunk_sqm),
        "max_splits_per_program": int(max_splits_per_program),
        "timestamp": utc_now_isoz(),
        "version": VERSION,
        "trace_sample": allocation_trace[:2000],
        "notes": "Soft weights + randomness. Topology unchanged; only level node attributes were added.",
    }
    return out

# =============================================================================
# Filenames: auto-incrementing it{N}.json
# =============================================================================

_IT_FILE_RE = re.compile(r"^it(\d+)\.json$", re.IGNORECASE)

def _existing_it_indices(outdir: Path) -> List[int]:
    idxs: List[int] = []
    if not outdir.exists():
        return idxs
    for p in outdir.glob("it*.json"):
        m = _IT_FILE_RE.match(p.name)
        if m:
            try:
                idxs.append(int(m.group(1)))
            except ValueError:
                pass
    idxs.sort()
    return idxs

def _next_it_index(outdir: Path) -> int:
    idxs = _existing_it_indices(outdir)
    return (max(idxs) + 1) if idxs else 1

def _it_path(outdir: Path, index_one_based: int) -> Path:
    return outdir / f"it{index_one_based}.json"

# =============================================================================
# Batch / CLI
# =============================================================================

def generate_enriched_variants(
    massing_path: Union[str, Path],
    brief_path: Union[str, Path],
    *,
    n_variants: int = 1,
    outdir: Optional[Union[str, Path]] = None,
    rng_seed: Optional[int] = None,       # if provided -> reproducible batch
    noise_strength: float = 1.0,
    assign_split_granularity_sqm: float = 220,
    min_chunk_sqm: float = 60,
    stdout: bool = False,                 # if True, print JSON for the FIRST variant to STDOUT
    log_csv: Optional[Union[str, Path]] = None,  # append run/variant info as CSV rows
) -> List[str]:
    """
    Generate N variants. If rng_seed is None, each variant uses a time-based seed.
    Files are saved to auto-incrementing 'it{N}.json' (no overwrite).
    Returns list of output file paths (empty if stdout-only single variant).
    """
    massing_path = Path(massing_path).resolve()
    brief_path = Path(brief_path).resolve()

    # Default: <project_root>/enriched_graph/iteration/
    if outdir is None:
        outdir_path = resolve_default_outdir(massing_path)
    else:
        outdir_path = Path(outdir).resolve()

    outdir_path.mkdir(parents=True, exist_ok=True)

    out_paths: List[str] = []
    base = rng_seed if rng_seed is not None else int(time.time() * 1000)

    # Determine starting index by scanning existing files
    start_idx = _next_it_index(outdir_path)

    # CSV header
    if log_csv:
        log_csv = Path(log_csv)
        header = ["timestamp", "version", "massing", "brief", "variant_index", "seed",
                  "noise", "granularity", "min_chunk", "out_path"]
        file_exists = log_csv.exists()
        with log_csv.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(header)

    # Generate
    next_idx = start_idx
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

        # Find the next free it{N}.json (numeric, not alphabetical)
        out_path = _it_path(outdir_path, next_idx)
        while out_path.exists():
            next_idx += 1
            out_path = _it_path(outdir_path, next_idx)

        # Optional STDOUT for the first variant
        if stdout and i == 0:
            sys.stdout.write(json.dumps(enriched, indent=2))
            sys.stdout.write("\n")
            sys.stdout.flush()

        # Save unless the user explicitly wants only stdout for a single variant
        if not (stdout and n_variants == 1):
            with out_path.open("w", encoding="utf-8") as f:
                json.dump(enriched, f, indent=2)
            out_paths.append(str(out_path))

        print(json.dumps({
            "type": "ENRICHED_VARIANT",
            "index": next_idx,  # the actual file index used
            "n_variants": n_variants,
            "seed": seed_i,
            "path": str(out_path) if not (stdout and n_variants == 1) else "(stdout)",
            "timestamp": utc_now_isoz(),
            "version": VERSION,
        }))

        # CSV row
        if log_csv:
            with Path(log_csv).open("a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    utc_now_isoz(), VERSION, str(massing_path), str(brief_path), next_idx, seed_i,
                    noise_strength, assign_split_granularity_sqm, min_chunk_sqm,
                    str(out_path) if not (stdout and n_variants == 1) else "(stdout)"
                ])

        # Increment for the next file
        next_idx += 1

    return out_paths

def _parse_args():
    p = argparse.ArgumentParser(
        description=(
            "Enrich massing graph with randomized, soft-weighted program assignments "
            "(no topology changes). Resolves default relative paths automatically."
        )
    )
    p.add_argument("--massing", type=str, required=False, default=None,
                   help="Path to massing_graph.json. If omitted, auto-resolve under ./knowledge/")
    p.add_argument("--brief", type=str, required=False, default=None,
                   help="Path to brief_graph.json. If omitted, picks most recent brief_graph*.json under ./knowledge/")
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
                   help="Output directory; defaults to <project_root>/enriched_graph/iteration/")
    p.add_argument("--stdout", action="store_true",
                   help="Emit the FIRST variant to STDOUT as JSON (still saves files unless n_variants==1).")
    p.add_argument("--log_csv", type=str, default=None,
                   help="Optional CSV file to append seeds/params/paths.")
    return p.parse_args()

def main():
    args = _parse_args()

    # Resolve defaults if not provided
    if args.massing is None or args.brief is None:
        try:
            auto_massing, auto_brief = resolve_default_paths()
        except FileNotFoundError as e:
            print(json.dumps({
                "type": "ENRICHED_ERROR",
                "message": str(e),
                "version": VERSION
            }))
            sys.exit(1)
        massing_path = Path(args.massing) if args.massing else auto_massing
        brief_path = Path(args.brief) if args.brief else auto_brief
    else:
        massing_path = Path(args.massing)
        brief_path = Path(args.brief)

    # Allow environment override for seed if CLI omitted
    env_seed = os.getenv("ENRICHED_SEED_BASE")
    base_seed = args.seed if args.seed is not None else (int(env_seed) if env_seed not in (None, "") else None)

    # Default outdir is <project_root>/enriched_graph/iteration/
    outdir_path = Path(args.outdir) if args.outdir else resolve_default_outdir(massing_path)
    outdir_path.mkdir(parents=True, exist_ok=True)

    banner = {
        "type": "ENRICHED_RUN",
        "timestamp": utc_now_isoz(),
        "version": VERSION,
        "massing": str(massing_path.resolve()),
        "brief": str(brief_path.resolve()),
        "n_variants": args.n_variants,
        "base_seed": base_seed,
        "noise": args.noise,
        "granularity": args.granularity,
        "min_chunk": args.min_chunk,
        "outdir": str(outdir_path),
        "stdout": bool(args.stdout),
        "log_csv": args.log_csv or "",
        "filenames": "it{N}.json (auto-incrementing, no overwrite)"
    }
    print(json.dumps(banner))

    paths = generate_enriched_variants(
        massing_path=massing_path,
        brief_path=brief_path,
        n_variants=args.n_variants,
        outdir=outdir_path,
        rng_seed=base_seed,
        noise_strength=args.noise,
        assign_split_granularity_sqm=args.granularity,
        min_chunk_sqm=args.min_chunk,
        stdout=args.stdout,
        log_csv=args.log_csv,
    )

    print(json.dumps({
        "type": "ENRICHED_DONE",
        "count": len(paths) if paths else (1 if args.stdout else 0),
        "version": VERSION
    }))

if __name__ == "__main__":
    main()
