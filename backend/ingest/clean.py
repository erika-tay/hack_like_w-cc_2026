"""Ingest step 1: turn raw text into clean text. No AI involved.

Cleaning does three jobs:
  1. Normalizes characters (curly quotes, odd spaces) so later steps see consistent text.
  2. Removes noise that carries no meaning: quoted replies, "Sent from my iPhone",
     confidentiality footers.
  3. Turns emoji reactions into plain tags. A bare thumbs-up in chat is an acknowledgment,
     not a commitment. Tagging it as [thumbs-up] lets the extraction prompt say so explicitly.

Speaker labels and timestamps are kept on purpose: they are what citations point to.
"""
import re
import unicodedata

_PUNCT = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", " ": " ",
}
_EMOJI_TAGS = {
    "👍": "[thumbs-up]", "👎": "[thumbs-down]", "✅": "[check]", "🙏": "[thanks]", "👀": "[looking]",
}
_OTHER_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿️]")

_FOOTER = re.compile(r"^\s*(CONFIDENTIALITY NOTICE|DISCLAIMER|This email and any attachments)", re.I)
_SENT_FROM = re.compile(r"^\s*Sent from my .+$", re.I)
_REPLY_HEADER = re.compile(r"^\s*On .{3,80}\bwrote:\s*$", re.I)


def clean_text(raw: str, doc_type: str | None = None) -> str:
    text = unicodedata.normalize("NFKC", raw).replace("\r\n", "\n").replace("\r", "\n")
    for old, new in _PUNCT.items():
        text = text.replace(old, new)
    for emoji, tag in _EMOJI_TAGS.items():
        text = text.replace(emoji, tag)
    text = _OTHER_EMOJI.sub("", text)

    lines = []
    for line in text.split("\n"):
        if doc_type == "email":
            if _FOOTER.match(line):
                break  # everything after the footer line is boilerplate
            if line.lstrip().startswith(">") or _SENT_FROM.match(line) or _REPLY_HEADER.match(line):
                continue  # quoted earlier messages and mobile signatures
        lines.append(re.sub(r"[ \t]+", " ", line).strip())

    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
