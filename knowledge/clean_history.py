# clean_history.py
# Run with Python 3 (launched by main.py via an external interpreter).
# - Deletes knowledge/massing_graph.json
# - Deletes knowledge/osm/graph_context.json
# - Purges knowledge/osm temporary workspaces:
#     * folders named "_tmp"
#     * folders starting with "osm_"
#     * folders whose names look like UUIDs
# - Removes knowledge/osm/_last_job.txt marker if present
# - Deletes everything inside knowledge/merge
# - Resets knowledge/iteration (recreates empty)

import os
import re
import stat
import shutil
from pathlib import Path
from typing import Iterable

# -------- Helpers --------

UUID_RX = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

def is_uuid_name(name: str) -> bool:
    return bool(UUID_RX.match(name))

def make_writable(p: Path) -> None:
    """Ensure a path is writable (helps on Windows when removing read-only files)."""
    try:
        os.chmod(str(p), os.stat(str(p)).st_mode | stat.S_IWRITE)
    except Exception:
        pass

def on_rm_error(func, path, exc_info):
    """shutil.rmtree callback to handle read-only files."""
    try:
        make_writable(Path(path))
        func(path)
    except Exception:
        pass

def safe_remove_file(p: Path) -> bool:
    try:
        if p.exists() and p.is_file():
            make_writable(p)
            p.unlink()
            print("[CLEAN] Removed file:", p)
        return True
    except Exception as e:
        print("[CLEAN] Failed to remove file:", p, "-", e)
        return False

def safe_rmtree(p: Path) -> bool:
    """Best-effort recursive delete with rename fallback."""
    try:
        if p.exists() and p.is_dir():
            shutil.rmtree(str(p), onerror=on_rm_error)
            print("[CLEAN] Removed folder:", p)
        return True
    except Exception:
        # Try rename-to-trash fallback, then delete
        try:
            trash = p.with_name(p.name + "_trash")
            p.rename(trash)
            shutil.rmtree(str(trash), onerror=on_rm_error)
            print("[CLEAN] Removed folder via fallback:", p)
            return True
        except Exception as e2:
            print("[CLEAN] Failed to remove folder:", p, "-", e2)
            return False

def ensure_empty_dir(p: Path) -> None:
    """Remove directory if present and recreate it empty."""
    safe_rmtree(p)
    try:
        p.mkdir(parents=True, exist_ok=True)
        print("[CLEAN] Recreated empty folder:", p)
    except Exception as e:
        print("[CLEAN] Failed to recreate folder:", p, "-", e)

# -------- Targeted cleanup --------

def purge_osm_workspaces(osm_dir: Path) -> None:
    """
    Remove temp OSM workspaces in knowledge/osm:
      - '_tmp'
      - 'osm_*'
      - UUID-looking folder names
    Also remove the marker file '_last_job.txt' if present.
    """
    if not osm_dir.exists() or not osm_dir.is_dir():
        return

    # Remove marker
    last_job_mark = osm_dir / "_last_job.txt"
    if last_job_mark.exists():
        safe_remove_file(last_job_mark)

    # Collect candidate folders
    try:
        entries: Iterable[Path] = [p for p in osm_dir.iterdir() if p.is_dir()]
    except Exception:
        entries = []

    for p in entries:
        name = p.name
        if name == "_tmp" or name.startswith("osm_") or is_uuid_name(name):
            safe_rmtree(p)

def remove_known_files(knowledge_dir: Path) -> None:
    """Remove individual files that should not persist across sessions."""
    targets = [
        knowledge_dir / "massing_graph.json",
        knowledge_dir / "osm" / "graph_context.json",
    ]
    for t in targets:
        if t.exists() and t.is_file():
            safe_remove_file(t)

def purge_merge_dir(merge_dir: Path) -> None:
    """Delete all files inside knowledge/merge."""
    if not merge_dir.exists() or not merge_dir.is_dir():
        return
    try:
        for p in merge_dir.iterdir():
            if p.is_file():
                safe_remove_file(p)
            elif p.is_dir():
                safe_rmtree(p)
    except Exception as e:
        print("[CLEAN] Failed to clean merge dir:", merge_dir, "-", e)

def reset_iteration_dir(knowledge_dir: Path) -> None:
    """Reset knowledge/iteration (remove entirely and recreate empty)."""
    iteration_dir = knowledge_dir / "iteration"
    ensure_empty_dir(iteration_dir)

# -------- Entry point --------

def main():
    knowledge_dir = Path(__file__).resolve().parent
    osm_dir = knowledge_dir / "osm"
    merge_dir = knowledge_dir / "merge"
    iteration_dir = knowledge_dir / "iteration"

    remove_known_files(knowledge_dir)
    purge_osm_workspaces(osm_dir)
    purge_merge_dir(merge_dir)
    reset_iteration_dir(knowledge_dir)

if __name__ == "__main__":
    main()
