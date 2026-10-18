"""Ingest step 2: redact personal data BEFORE any text can reach an LLM. No AI involved.

Design choice: we redact *employee* data (SSNs, birth dates, pay, personal contact details,
employee IDs, names inside record rows) but keep the *project team's* names and work emails.
The extraction step needs those to assign owners, so they come in through an allowlist built
from the people table.

This is regex-based on purpose: deterministic, fast, and easy to explain. The limit is that it
only catches structured identifiers. Free-text names of employees would need an NER model
(for example Microsoft Presidio) in production, and that is a good "next step" for the demo.

Offsets in each Redaction refer to the text passed to redact(), which is what lets the UI
highlight exactly what was removed in a before/after view.
"""
import re
from collections import Counter
from dataclasses import dataclass, field


@dataclass
class Redaction:
    kind: str
    start: int
    end: int


@dataclass
class RedactionResult:
    text: str
    redactions: list[Redaction] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        return dict(Counter(r.kind for r in self.redactions))


# (kind, pattern, which regex group to replace). Group 0 is the whole match; for patterns
# with a keyword ("DOB", "Salary") we keep the label and replace only the value.
_PATTERNS = [
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), 0),
    ("DOB", re.compile(r"(?i)\b(?:dob|date of birth|born)\b[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})"), 1),
    # Only amounts next to a pay keyword. Business figures like a $62,500 contract fee stay.
    ("SALARY", re.compile(r"(?i)\b(?:salary|wage|pay rate|annual pay|compensation)\b[:\s]*(\$\s?\d[\d,]*(?:\.\d+)?)"), 1),
    ("RATE", re.compile(r"(?i)\$\s?\d+(?:\.\d{2})?\s*/\s*(?:hr|hour)\b"), 0),
    ("EMP_ID", re.compile(r"\bEMP-\d{3,}\b"), 0),
    ("PHONE", re.compile(r"(?<![\d-])(?:\(?\d{3}\)?[-. ])?\d{3}[-.]\d{4}(?![\d-])"), 0),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), 0),
]

_NAME_FIELD = re.compile(r"^[A-Z][a-z]+(?: [A-Z][a-z]+)+$")


def _names_in_record_rows(text: str, allow_names: set[str]) -> list[Redaction]:
    """In pipe-delimited record rows (like a pasted export row), redact fields that look like names."""
    found = []
    offset = 0
    for line in text.split("\n"):
        if line.count("|") >= 3:
            for m in re.finditer(r"[^|]+", line):
                raw = m.group(0)
                value = raw.strip()
                if _NAME_FIELD.match(value) and value.lower() not in allow_names:
                    start = offset + m.start() + (len(raw) - len(raw.lstrip()))
                    found.append(Redaction("NAME", start, start + len(value)))
        offset += len(line) + 1
    return found


def redact(text: str, allow_emails=(), allow_names=()) -> RedactionResult:
    allow_e = {e.lower() for e in allow_emails}
    allow_n = {n.lower() for n in allow_names}

    found: list[Redaction] = []
    for kind, pattern, group in _PATTERNS:
        for m in pattern.finditer(text):
            if kind == "EMAIL" and m.group(0).lower() in allow_e:
                continue
            found.append(Redaction(kind, m.start(group), m.end(group)))
    found += _names_in_record_rows(text, allow_n)

    # If two matches overlap, keep the earlier (and then longer) one.
    found.sort(key=lambda r: (r.start, -(r.end - r.start)))
    kept: list[Redaction] = []
    last_end = -1
    for r in found:
        if r.start >= last_end:
            kept.append(r)
            last_end = r.end

    out, cursor = [], 0
    for r in kept:
        out.append(text[cursor:r.start])
        out.append(f"[{r.kind}]")
        cursor = r.end
    out.append(text[cursor:])
    return RedactionResult("".join(out), kept)
