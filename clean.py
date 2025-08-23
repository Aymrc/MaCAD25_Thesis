# Inspect + sanitize rhino_listener.py around the failing line
import os, sys, io, re

PATH = r"C:\Users\broue\Documents\IAAC MaCAD\Master_Thesis\MaCAD25_Thesis\rhino\rhino_listener.py"   # <-- put the full path here
LINE = 59                                   # offending line number from the error

def show_lines(path, line, radius=3):
    with io.open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    start = max(1, line - radius)
    end   = min(len(lines), line + radius)
    print("\n--- Context lines {}..{} ---".format(start, end))
    for i in range(start, end+1):
        s = lines[i-1]
        print("{:>4}: {}{}".format(i, s, "  <-- HERE" if i == line else ""))

def sanitize(path):
    with io.open(path, "r", encoding="utf-8", errors="replace") as f:
        src = f.read()

    # Replace non-ASCII punctuation that often breaks IronPython
    trans = {
        u"\u2013": "-",   # en dash –
        u"\u2014": "-",   # em dash —
        u"\u2026": "...", # ellipsis …
        u"\u2192": "->",  # arrow →
        u"\u2018": "'",   # left single quote ‘
        u"\u2019": "'",   # right single quote ’
        u"\u201c": '"',   # left double quote “
        u"\u201d": '"',   # right double quote ”
    }
    for k, v in trans.items():
        src = src.replace(k, v)

    # Comment out any line made entirely of dashes/equals/underscores
    def fix_rule(m):
        line = m.group(0)
        return "# " + line if not line.lstrip().startswith("#") else line
    src = re.sub(r"^(?:\s*[-=_]{5,}\s*)$", fix_rule, src, flags=re.M)

    with io.open(path, "w", encoding="utf-8") as f:
        f.write(src)

print("[pre] Showing lines around", LINE)
show_lines(PATH, LINE)

print("\n[sanitize] Rewriting problem characters & uncommenting bare rulers…")
sanitize(PATH)

print("\n[post] Showing lines around", LINE)
show_lines(PATH, LINE)

print("\nDone. Save/reload the script and try again.")
