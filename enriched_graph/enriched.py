# enriched.py
# Soft-weighted, randomized program allocation without changing topology.
# Output: human-readable only (no machine-JSON logs except --stdout graph dump).

import os, sys, csv, json, re, math, random, time, argparse
import statistics as stats
from copy import deepcopy
from collections import defaultdict
from datetime import datetime, timezone
from typing import Union, Dict, Any, List, Optional, Tuple
from pathlib import Path

VERSION = "3.2.9"

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
    cur = start.resolve()
    for _ in range(max_up):
        if (cur / search_subdir).is_dir():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return start

def _find_any_brief_graph(knowledge_dir: Path) -> Optional[Path]:
    if not knowledge_dir.is_dir():
        return None
    candidates = list(knowledge_dir.rglob("brief_graph*.json"))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]

def resolve_default_paths() -> Tuple[Path, Path]:
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
    if massing_path is not None:
        mp = Path(massing_path).resolve()
        knowledge_dir = mp.parent
        project_root = knowledge_dir.parent
    else:
        script_dir = Path(__file__).resolve().parent
        project_root = _find_project_root(script_dir)
    return project_root / "knowledge" / "iteration"

# =============================================================================
# Utils: robust field access
# =============================================================================

_AREA_KEYS_NODE = ["area", "area_m2", "area_doc", "gfa", "area_sqm", "sqm", "net_area", "gross_area"]
_AREA_KEYS_BRIEF = ["footprint", "area", "area_m2", "gfa", "area_sqm", "sqm", "required_area", "need_sqm", "target_sqm"]

SITE_LIKE_SET = {"public_space", "open_space", "green_space", "park", "plaza", "landscape", "square"}

def get_area_from_node(n: Dict[str, Any]) -> float:
    for k in _AREA_KEYS_NODE:
        v = n.get(k)
        if isinstance(v, (int, float)):
            return float(v)
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

    program_need = {k: float(v) for k, v in program_need.items() if v and v > 0}
    return program_need, program_meta

def _auto_base_granularity(level_nodes: List[Dict[str, Any]]) -> float:
    """Derive a sensible grain from the massing so the user never has to tune it.
    ~half the median level area, clamped to a sane band."""
    areas = [get_area_from_node(n) for n in level_nodes if get_area_from_node(n) > 0]
    if not areas:
        return 400.0  # safe fallback
    med = stats.median(areas)
    return max(200.0, min(1200.0, 0.5 * med))

def _split_budget_for_pass(need: float, chunk: float, user_cap: Optional[int]) -> int:
    """Ensure we always have enough steps to finish the pass; never blocks."""
    if chunk <= 0:
        chunk = 1.0
    est = max(1, math.ceil(need / chunk))               # how many steps to finish at this chunk
    buf = max(10, int(0.35 * est))                      # 35% buffer
    budget = est + buf
    # IMPORTANT: do not let a low user cap block the run — lift to at least the budget.
    if user_cap is None:
        return budget
    return max(budget, user_cap)


# =============================================================================
# Core enrichment
# =============================================================================

def enrich_without_changing_topology(
    massing_graph_or_path: Union[str, Path, Dict[str, Any]],
    brief_graph_or_path: Union[str, Path, Dict[str, Any]],
    *,
    rng_seed: Optional[int] = None,
    noise_strength: float = 1.0,
    assign_split_granularity_sqm: float = 220,
    min_chunk_sqm: float = 60,
    max_splits_per_program: int = 400,
) -> Dict[str, Any]:
    """
    Assign programs to level nodes (no topology change).
    Now uses an adaptive coarse→fine chunk schedule and a non-blocking split budget,
    so fulfillment is only limited by actual capacity — not by granularity or caps.
    """
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

    # Plot connectivity
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
    building_program_ids = [pid for pid in program_need.keys()
                            if norm_typ(program_meta.get(pid, {}).get("typology", pid)) not in SITE_LIKE_SET]
    site_program_ids = [pid for pid in program_need.keys()
                        if norm_typ(program_meta.get(pid, {}).get("typology", pid)) in SITE_LIKE_SET]

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

    id_to_level = {ln["id"]: ln for ln in level_nodes}
    level_ids = list(id_to_level.keys())
    base_weights = {
        pid: {lid: base_weight(id_to_level[lid], pid) for lid in level_ids}
        for pid in building_program_ids
    }

    # === Adaptive chunk schedule (coarse → normal → polish) ===
    base_gran = assign_split_granularity_sqm if assign_split_granularity_sqm and assign_split_granularity_sqm > 0 \
                else _auto_base_granularity(level_nodes)
    passes = [
        max(min_chunk_sqm, 4.0 * base_gran),
        max(min_chunk_sqm, 1.0 * base_gran),
        max(min_chunk_sqm, 0.5 * base_gran),
    ]

    # Allocation
    program_order = list(building_program_ids)
    random.Random(rng_seed + 13).shuffle(program_order)

    allocations = defaultdict(list)  # level_id -> List[(program_id, typology, area)]
    remaining = {pid: float(program_need[pid]) for pid in building_program_ids}
    allocation_trace = []

    def total_capacity_left() -> float:
        return sum(max(0.0, capacities[lid] - used[lid]) for lid in level_ids)

    for pid in program_order:
        need = remaining[pid]
        typ = norm_typ(program_meta.get(pid, {}).get("typology") or pid)
        splits_total = 0

        for pass_chunk in passes:
            if need <= 1e-6 or total_capacity_left() <= 1e-6:
                break

            # Non-blocking, auto-lifted split budget for this pass
            step_budget = _split_budget_for_pass(need, pass_chunk, max_splits_per_program)
            steps = 0

            while need > 1e-6 and steps < step_budget and total_capacity_left() > 1e-6:
                # candidate levels with capacity
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
                    break

                # draw a chunk around the pass size, honoring min_chunk and available cap/need
                chunk = max(min_chunk_sqm, pass_chunk * (0.6 + 0.8 * rng.random()))
                take = float(min(cap_left, need, chunk))
                if take <= 1e-6:
                    break

                allocations[chosen].append((pid, typ, take))
                used[chosen] += take
                need -= take
                splits_total += 1
                steps += 1

                allocation_trace.append({
                    "program_id": pid,
                    "typology": typ,
                    "chosen_level": chosen,
                    "take_sqm": round(take, 3),
                    "need_remaining": round(need, 3),
                })

        # Emergency polish if some need remains and capacity still exists (use very small chunks)
        if need > 1e-6 and total_capacity_left() > 1e-6:
            tiny_chunk = max(1.0, 0.5 * min_chunk_sqm)
            step_budget = _split_budget_for_pass(need, tiny_chunk, max_splits_per_program)
            steps = 0
            while need > 1e-6 and steps < step_budget and total_capacity_left() > 1e-6:
                cand = {}
                for lid in level_ids:
                    cap_left = capacities[lid] - used[lid]
                    if cap_left > 1e-6:
                        w = base_weights[pid][lid] + jitter(rng)
                        if any(a[0] == pid for a in allocations[lid]):
                            w += 0.15
                        w += 0.01 * math.log(max(cap_left, 1.0))
                        cand[lid] = w
                if not cand:
                    break
                chosen = softmax_choice(cand, rng)
                if chosen is None:
                    break
                cap_left = capacities[chosen] - used[chosen]
                if cap_left <= 1e-6:
                    break
                chunk = max(1.0, tiny_chunk * (0.6 + 0.8 * rng.random()))
                take = float(min(cap_left, need, chunk))
                if take <= 1e-6:
                    break
                allocations[chosen].append((pid, typ, take))
                used[chosen] += take
                need -= take
                steps += 1
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
        "notes": "Soft weights + randomness. Adaptive coarse→fine chunking; split budget auto-lifted to avoid blocking.",
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
    rng_seed: Optional[int] = None,
    noise_strength: float = 1.0,
    assign_split_granularity_sqm: float = 220,
    min_chunk_sqm: float = 60,
    stdout: bool = False,
    log_csv: Optional[Union[str, Path]] = None,
) -> List[str]:
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

    start_idx = _next_it_index(outdir_path)

    # ---- brief targets (for summary) ----
    with brief_path.open("r", encoding="utf-8") as f:
        brief_json = json.load(f)
    program_need_all, program_meta = extract_programs_from_brief(brief_json)

    targets_by_typ_all = defaultdict(float)
    for pid, area in program_need_all.items():
        typ = norm_typ(program_meta.get(pid, {}).get("typology") or pid)
        targets_by_typ_all[typ] += float(area)

    targets_building_by_typ = {t: a for t, a in targets_by_typ_all.items() if t not in SITE_LIKE_SET}
    targets_site_by_typ = {t: a for t, a in targets_by_typ_all.items() if t in SITE_LIKE_SET}

    # ---- capacity preflight ----
    with massing_path.open("r", encoding="utf-8") as f:
        massing_json = json.load(f)
    def _is_level_local(n: Dict[str, Any]) -> bool:
        if n.get("type") == "level":
            return True
        nid = n.get("id", "")
        if "-L" in nid:
            suf = nid.split("-L", 1)[1]
            return suf.isdigit()
        return False
    level_nodes = [n for n in massing_json.get("nodes", []) if _is_level_local(n)]
    building_capacity_raw = sum(get_area_from_node(n) for n in level_nodes)
    building_target_raw = sum(targets_building_by_typ.values())
    support_ratio = (building_capacity_raw / building_target_raw) if building_target_raw > 0 else None

    # Print info paths, seed, variants, ...
    # print("ENRICHED RUN")
    # print(f"  version:  {VERSION}")
    # print(f"  massing:  {massing_path}")
    # print(f"  brief:    {brief_path}")
    # print(f"  variants: {n_variants}")
    # print(f"  seed:     {('random' if rng_seed is None else rng_seed)}")
    # print(f"  outdir:   {outdir_path}\n")

    print("CAPACITY CHECK (building programs only)")
    print(f"  - Level capacity available: {building_capacity_raw:.2f} m²")
    print(f"  - Building program target:  {building_target_raw:.2f} m²")
    if support_ratio is not None:
        print(f"  - Support ratio: {(support_ratio*100):.1f}%")
        if building_capacity_raw < building_target_raw:
            print("  - WARNING: Capacity is insufficient; allocation will fill only available area and leave the rest unmet.\n")
        else:
            print("  - OK: Capacity is sufficient to host the requested building program.\n")
    else:
        print("  - No building program target specified.\n")

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

        out_path = _it_path(outdir_path, next_idx)
        while out_path.exists():
            next_idx += 1
            out_path = _it_path(outdir_path, next_idx)

        if stdout and i == 0:
            sys.stdout.write(json.dumps(enriched, indent=2))
            sys.stdout.write("\n")
            sys.stdout.flush()

        if not (stdout and n_variants == 1):
            with out_path.open("w", encoding="utf-8") as f:
                json.dump(enriched, f, indent=2)
            out_paths.append(str(out_path))

        print(f"SAVED  it{next_idx}  (seed {seed_i})")
        # print(f"  path: {out_path}\n")

        # ---- per-variant program summary (BUILDING ONLY) ----
        assigned_by_typ_all = defaultdict(float)
        for n in enriched.get("nodes", []):
            for a in n.get("program_assignments", []):
                typ = norm_typ(a.get("typology") or a.get("program_id"))
                assigned_by_typ_all[typ] += float(a.get("area", 0.0))

        building_typs = sorted(set(list(targets_building_by_typ.keys()) +
                                   [t for t in assigned_by_typ_all.keys() if t not in SITE_LIKE_SET]))

        overall_assigned_building_raw = sum(assigned_by_typ_all[t] for t in assigned_by_typ_all if t not in SITE_LIKE_SET)
        overall_target_building_raw = sum(targets_building_by_typ.values())
        overall_delta_building_raw = overall_assigned_building_raw - overall_target_building_raw
        overall_building_pct = (overall_assigned_building_raw / overall_target_building_raw) if overall_target_building_raw > 0 else None

        lines = []
        lines.append(f"PROGRAM SUMMARY (building only)  it{next_idx}  seed {seed_i}")
        for t in building_typs:
            a = assigned_by_typ_all.get(t, 0.0)
            tgt = targets_building_by_typ.get(t, 0.0)
            d = a - tgt
            pct_txt = (f"{(a/tgt)*100:.1f}%" if tgt > 0 else "—")
            lines.append(f"  - {t}: {a:.2f} m² / {tgt:.2f} m²   Δ {d:.2f}   ({pct_txt} fulfilled)")
        if overall_target_building_raw > 0:
            lines.append(f"  = Building-only total: {overall_assigned_building_raw:.2f} m² / {overall_target_building_raw:.2f} m²   Δ {overall_delta_building_raw:.2f}   ({(overall_building_pct*100):.1f}% fulfilled)")
        else:
            lines.append(f"  = Building-only total: {overall_assigned_building_raw:.2f} m² (no target specified)")

        if targets_site_by_typ:
            lines.append("  Site-only targets (not placed by this script):")
            for t, v in sorted(targets_site_by_typ.items()):
                lines.append(f"    • {t}: {v:.2f} m²")

        if support_ratio is not None:
            head = "WARNING" if building_capacity_raw < building_target_raw else "INFO"
            lines.append(f"  {head}: current massing provides {building_capacity_raw:.2f} m² capacity vs building target {building_target_raw:.2f} m² → supports {(support_ratio*100):.1f}%.")

        print("\n".join(lines) + "\n")

        if log_csv:
            with Path(log_csv).open("a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    utc_now_isoz(), VERSION, str(massing_path), str(brief_path), next_idx, seed_i,
                    noise_strength, assign_split_granularity_sqm, min_chunk_sqm,
                    str(out_path) if not (stdout and n_variants == 1) else "(stdout)"
                ])

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
            print(f"[ERROR] {e}")
            sys.exit(1)
        massing_path = Path(args.massing) if args.massing else auto_massing
        brief_path = Path(args.brief) if args.brief else auto_brief
    else:
        massing_path = Path(args.massing)
        brief_path = Path(args.brief)

    env_seed = os.getenv("ENRICHED_SEED_BASE")
    base_seed = args.seed if args.seed is not None else (int(env_seed) if env_seed not in (None, "") else None)

    outdir_path = Path(args.outdir) if args.outdir else resolve_default_outdir(massing_path)
    outdir_path.mkdir(parents=True, exist_ok=True)

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

    print("Enriched graph done.")

if __name__ == "__main__":
    main()
