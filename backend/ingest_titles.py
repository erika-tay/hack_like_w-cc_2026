"""Map each sample document's Title line to its file name, e.g. 'Weekly Status Meeting (Oct 1)' -> '04_status_meeting_oct1'.

The answer key refers to documents by file name; the database knows them by title.
"""
from db import ROOT


def file_stem_by_title() -> dict[str, str]:
    out = {}
    for path in sorted((ROOT / "sample_data").glob("[0-9][0-9]_*.txt")):
        first = path.read_text(encoding="utf-8").split("\n", 1)[0]
        if first.startswith("Title: "):
            out[first[len("Title: "):].strip()] = path.stem
    return out
