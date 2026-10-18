"""Score the real extraction against the answer key (sample_data/expected_items.json).

    python eval_extraction.py

Run it right after `python seed.py && python -m ai.extract`. It prints:
  * recall per item type (how many answer-key items were found)
  * extras (items the model found that aren't in the key; some may be legitimate)
  * for matched commitments: were the owner, due date and status right?
Matching is by item type plus word overlap, boosted when both cite the same documents, and the
matched pairs are printed so you can check them by eye. Treat the numbers as a guide, not a grade.

Do not run this against data loaded with `snapshot.py load`... it would work, but it measures
a past run, not the current prompt.
"""
import json
import re

from db import ROOT, get_conn
from ingest_titles import file_stem_by_title

KEY = json.loads((ROOT / "sample_data" / "expected_items.json").read_text(encoding="utf-8"))
_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are", "be", "that", "this", "with",
         "at", "by", "as", "from", "about", "it", "its", "has", "have", "will", "not", "no", "may", "can"}


def tokens(s: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9]+", s.lower()):
        if w not in _STOP and len(w) > 1:
            out.add(re.sub(r"(ing|ed|es|s)$", "", w) if len(w) > 4 else w)
    return out


def score(exp: dict, got: dict) -> float:
    a, b = tokens(exp["summary"]), tokens(got["summary"])
    if not a or not b:
        return 0.0
    s = len(a & b) / min(len(a), len(b))
    if set(exp["sources"]) & set(got["source_stems"]):
        s += 0.25
    return s


def main() -> None:
    conn = get_conn()
    stems = file_stem_by_title()
    rows = [dict(r) for r in conn.execute(
        "SELECT i.*, p.name AS owner_name FROM items i LEFT JOIN people p ON p.id = i.owner_id ORDER BY i.id")]
    for r in rows:
        titles = [t["title"] for t in conn.execute(
            "SELECT d.title FROM item_sources s JOIN documents d ON d.id = s.document_id WHERE s.item_id = ?", (r["id"],))]
        r["source_stems"] = [stems.get(t) for t in titles if stems.get(t)]

    pairs = sorted(((score(e, g), e["id"], g["id"]) for e in KEY["items"] for g in rows
                    if e["type"] == g["item_type"]), reverse=True)
    exp_by_id, got_by_id = {e["id"]: e for e in KEY["items"]}, {g["id"]: g for g in rows}
    matched, used_e, used_g = [], set(), set()
    for s, eid, gid in pairs:
        if s >= 0.5 and eid not in used_e and gid not in used_g:
            matched.append((eid, gid, s))
            used_e.add(eid)
            used_g.add(gid)

    print(f"AI items: {len(rows)} | answer key: {len(KEY['items'])}\n")
    print(f"{'type':12s} {'found':>7s} {'required found':>16s}")
    for kind in ("commitment", "decision", "risk", "opportunity"):
        of_type = [e for e in KEY["items"] if e["type"] == kind]
        found = [e for e in of_type if e["id"] in used_e]
        req = [e for e in of_type if e.get("required")]
        req_found = [e for e in req if e["id"] in used_e]
        print(f"{kind:12s} {len(found):>3d}/{len(of_type):<3d} {len(req_found):>10d}/{len(req)}")

    print("\nMISSED (required):")
    for e in KEY["items"]:
        if e.get("required") and e["id"] not in used_e:
            print(f"  [{e['type']}] {e['summary']}")

    print("\nEXTRAS (not in the key; judge for yourself):")
    for g in rows:
        if g["id"] not in used_g:
            print(f"  [{g['item_type']}] {g['summary']}")

    print("\nCOMMITMENT FIELD CHECK (matched pairs):")
    ok = {"owner": 0, "due": 0, "status": 0}
    total = 0
    for eid, gid, s in matched:
        e, g = exp_by_id[eid], got_by_id[gid]
        if e["type"] != "commitment":
            continue
        total += 1
        owner_ok = (e["owner"] or None) == (g["owner_name"] or None)
        due_ok = (e.get("due") or None) == (g["due_date"] or None)
        status_ok = e["status"] == g["status"]
        ok["owner"] += owner_ok
        ok["due"] += due_ok
        ok["status"] += status_ok
        flags = "".join(c if good else "." for c, good in (("O", owner_ok), ("D", due_ok), ("S", status_ok)))
        print(f"  {flags}  key: {e['summary'][:48]:48s} | got: {g['summary'][:48]}")
    if total:
        print(f"\n  owner {ok['owner']}/{total}   due date {ok['due']}/{total}   status {ok['status']}/{total}"
              "   (O/D/S shown when right, '.' when wrong)")


if __name__ == "__main__":
    main()
