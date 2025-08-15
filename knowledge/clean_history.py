# clean_history.py

from pathlib import Path
import shutil, os, stat

def _make_writable(p: Path):
    try:
        os.chmod(str(p), os.stat(str(p)).st_mode | stat.S_IWRITE)
    except: # stay silent
        pass

def _on_rm_error(func, path, exc_info):
    try:
        _make_writable(Path(path))
        func(path)
    except:
        pass

def main(): # @César
    base = Path(__file__).resolve().parent  # .../knowledge
    targets = [
        base / "massing_graph.json", # file
        # base / "osm/", # dir
        # add more files here if needed
    ]

    for t in targets:
        try:
            if t.is_file():
                _make_writable(t)
                t.unlink()
            elif t.is_dir():
                shutil.rmtree(str(t), onerror=_on_rm_error)
        except:
            pass

if __name__ == "__main__":
    main()
