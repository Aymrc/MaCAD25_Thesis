import json
from copy import deepcopy
from collections import defaultdict
import math
from typing import Union

import json
from copy import deepcopy
from collections import defaultdict
import math
from typing import Union, Dict, Any, List

def enrich_without_changing_topology(
    massing_graph_or_path: Union[str, Dict[str, Any]],
    brief_graph_or_path: Union[str, Dict[str, Any]],
    # which program typologies are eligible to sit on building floors
    assignable_typologies: List[str] = None,
    # typologies that must NOT be put on floors (treated as site programs)
    site_like_typologies: List[str] = None,
    # map aliases/synonyms to normalized typology keys
    typology_aliases: Dict[str, str] = None,
) -> Dict[str, Any]:
    """
    Assigns *building* programs to level nodes WITHOUT changing topology:
      - nodes/links untouched (only adds attributes on level nodes)
      - site-like programs (green/public/open-space) are *not* assigned to floors
    Adds on level nodes:
      program_assignments: [{program_id, area}]
      remaining_area: float
      primary_program: str | None
    Also returns meta.site_programs for unplaced site programs.
    """

    # --- load if paths were provided ---
    def _load(x):
        if isinstance(x, str):
            with open(x, "r", encoding="utf-8") as f:
                return json.load(f)
        return x

    massing_graph = _load(massing_graph_or_path)
    brief_graph = _load(brief_graph_or_path)

    # --- preserve original link key ---
    link_key = "links" if "links" in massing_graph else ("edges" if "edges" in massing_graph else "links")

    g = deepcopy(massing_graph)
    nodes = g.get("nodes", [])
    links = g.get(link_key, [])

    # --------- normalization & sets ---------
    if typology_aliases is None:
        typology_aliases = {
            # normalize dash/space variants
            "public space": "public_space",
            "open space": "open_space",
            "green space": "green_space",
            "recreational": "leisure",   # treat as leisure for buildings
            "culture": "cultural",
            "residence": "residential",
            "housing": "residential",
            "retail/commercial": "retail",
        }

    def norm_typ(t: str) -> str:
        t = (t or "").strip().lower().replace("-", "_")
        return typology_aliases.get(t, t)

    # By default, what CAN live on a floor:
    if assignable_typologies is None:
        assignable_typologies = [
            "office", "residential", "leisure", "cultural",
            "retail", "education", "healthcare", "hotel",
            "parking", "lab", "industrial"
        ]

    # By default, what must *not* be on floors (site-level programs):
    if site_like_typologies is None:
        site_like_typologies = [
            "public_space", "open_space", "green_space", "park",
            "plaza", "landscape", "square"
        ]

    assignable_typologies = set(assignable_typologies)
    site_like_typologies = set(site_like_typologies)

    # --------- helpers ---------
    def is_level(node):
        if node.get("type") == "level":
            return True
        nid = node.get("id", "")
        if "-L" in nid:
            suf = nid.split("-L", 1)[1]
            return suf.isdigit()
        return False

    def level_index(nid):
        if "-L" in nid:
            try:
                return int(nid.split("-L", 1)[1])
            except Exception:
                return 0
        return 0

    # Identify levels & capacities
    level_nodes = [n for n in nodes if is_level(n)]
    capacities = {n["id"]: float(n.get("area", 0.0)) for n in level_nodes}
    used = defaultdict(float)

    # Plot-connected levels (for public-facing ground bias)
    plot_edges = [e for e in links if e.get("type") == "plot"]
    plot_connected_ids = set()
    for e in plot_edges:
        s, t = e.get("source"), e.get("target")
        if s and s != "PLOT": plot_connected_ids.add(s)
        if t and t != "PLOT": plot_connected_ids.add(t)

    # Programs from brief (normalize typology)
    brief_program_nodes = [n for n in brief_graph.get("nodes", []) if n.get("id") != "masterplan"]
    for n in brief_program_nodes:
        n["typology"] = norm_typ(n.get("typology", ""))

    program_need = {n["id"]: float(n.get("footprint", 0.0)) for n in brief_program_nodes}
    program_meta = {n["id"]: n for n in brief_program_nodes}

    # Split into site vs assignable
    site_programs = [n for n in brief_program_nodes if n["typology"] in site_like_typologies]
    building_programs = [n for n in brief_program_nodes if n["typology"] in assignable_typologies]

    # ---- scoring heuristic (for building programs only) ----
    def score(level_node, program_id):
        typ = program_meta[program_id].get("typology", "")
        nid = level_node["id"]
        z = level_index(nid)
        area = float(level_node.get("area", 0.0))

        ground_bonus = 0.0
        height_pref = 0.0

        # Ground/plot bias: leisure, cultural, retail, education, healthcare
        if typ in ("leisure", "cultural", "retail", "education", "healthcare"):
            if z == 0: ground_bonus += 8.0
            if nid in plot_connected_ids: ground_bonus += 3.0
            height_pref -= 0.3 * z

        elif typ == "office":
            height_pref += 0.6 * min(z, 8)

        elif typ == "residential":
            height_pref += 0.8 * min(z, 12)

        elif typ in ("parking", "industrial", "lab", "hotel"):
            # mild preferences
            if typ == "parking" and z <= 1:
                ground_bonus += 5.0
            if typ == "hotel":
                height_pref += 0.4 * min(z, 10)

        size_term = math.log(max(area, 1.0)) * 0.05
        return ground_bonus + height_pref + size_term

    # Program order: public-facing (leisure/cultural/retail/education/healthcare) first, then bigger ones
    def prog_key(pid):
        typ = program_meta[pid].get("typology", "")
        publicish = {"leisure","cultural","retail","education","healthcare"}
        priority = -2 if typ in publicish else (-1 if typ in {"office","residential","hotel"} else 0)
        return (priority, -program_need[pid])

    program_order = sorted([n["id"] for n in building_programs], key=prog_key)

    # Precompute level ranking per building program
    ranked_levels = {
        pid: [ln["id"] for ln in sorted(level_nodes, key=lambda ln: score(ln, pid), reverse=True)]
        for pid in program_order
    }

    # Greedy fill (no topology changes)
    allocations = defaultdict(list)  # level_id -> [(program_id, area)]
    remaining = dict(program_need)   # includes site programs too; we won't touch those

    for pid in program_order:
        need = remaining[pid]
        if need <= 0:
            continue
        for lid in ranked_levels[pid]:
            if need <= 0:
                break
            cap_left = capacities[lid] - used[lid]
            if cap_left <= 0:
                continue
            take = min(cap_left, need)
            allocations[lid].append((pid, take))
            used[lid] += take
            need -= take
        remaining[pid] = need

    # Attach attributes ONLY on level nodes
    for n in nodes:
        if not is_level(n):
            continue
        lid = n["id"]
        assigned = allocations.get(lid, [])
        n["program_assignments"] = [{"program_id": pid, "area": float(a)} for pid, a in assigned]
        n["remaining_area"] = float(capacities.get(lid, 0.0) - used.get(lid, 0.0))
        n["primary_program"] = (max(assigned, key=lambda x: x[1])[0] if assigned else None)

    # Preserve topology exactly
    out = {k: deepcopy(v) for k, v in g.items()}
    out["nodes"] = nodes
    out[link_key] = links

    # Record site programs (not assigned to floors)
    out["meta"] = out.get("meta", {})
    out["meta"]["site_programs"] = [
        {
            "id": n["id"],
            "typology": n["typology"],
            "need_sqm": float(n.get("footprint", 0.0))
        }
        for n in site_programs
    ]
    # And any building-program shortfall
    out["meta"]["unallocated_program_area"] = {
        k: float(v) for k, v in remaining.items()
        if v > 1e-6 and k in {bp["id"] for bp in building_programs}
    }

    return out


massing = r"C:\Users\broue\Documents\IAAC MaCAD\Master_Thesis\MaCAD25_Thesis\knowledge\massing_graph.json"
brief   = r"C:\Users\broue\Documents\IAAC MaCAD\Master_Thesis\MaCAD25_Thesis\knowledge\briefs\brief_20250815_182114\brief_graph.json"

enriched = enrich_without_changing_topology(massing, brief)

# write next to the massing file
out_path = massing.replace(".json", "_enriched.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(enriched, f, indent=2)
print("Saved:", out_path)
