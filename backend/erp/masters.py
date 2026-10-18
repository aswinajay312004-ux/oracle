"""Matching a value from a file to the existing master record (docs/RESEARCH_DECISIONS.md, "Master-data matching").

1. Exact: the same text after upper-casing and collapsing spaces (the planner's normalize).
2. Fingerprint: the same letters and digits once case, spacing and punctuation are ignored ("&" counts as AND, leading
   zeros of numbers are dropped, OpenRefine style): "Next Ltd." is the buyer NEXT LTD, "ZK 02002" the style ZK-2002.
   One such record → it is used, and the preview says so. Values of 3 characters or fewer (sizes S/XS, units) match
   exactly only: one character decides which record they are.
3. Similar names: nothing matched and a new record would be created. Names (buyer, colour, description, merchandiser)
   that are close to existing ones are listed (token-set similarity ≥ 0.8, as RapidFuzz's token_set_ratio, here on the
   standard library), so the approver sees "new colour NAVY BLUES; existing NAVY BLUE" before saving. Codes (style
   numbers) are not compared this way: a new style is nearly always close to an existing one (ZK-2002 / ZK-2003).
"""
from __future__ import annotations
from difflib import SequenceMatcher
import re
import unicodedata

NAMES = {'buyer', 'color', 'description', 'merch'}
SIMILAR = 0.8
SHORT = 3


def fingerprint(text):
    t = unicodedata.normalize('NFKC', str(text or '')).upper().replace('&', ' AND ')
    t = re.sub(r'\d+', lambda m: m.group().lstrip('0') or '0', re.sub(r'[^0-9A-Z]+', ' ', t))
    return ''.join(t.split())


def _tokens(text):
    return re.sub(r'[^0-9A-Z]+', ' ', unicodedata.normalize('NFKC', str(text or '')).upper().replace('&', ' AND ')).split()


def similarity(a, b):
    """Token-set similarity 0–1: word order and repeated words do not matter, a name inside a longer one scores high."""
    x, y = set(_tokens(a)), set(_tokens(b))
    if not x or not y:
        return 0.0
    common = ' '.join(sorted(x & y)); rest_a = ' '.join(sorted(x - y)); rest_b = ' '.join(sorted(y - x))
    joined_a = f'{common} {rest_a}'.strip(); joined_b = f'{common} {rest_b}'.strip()
    ratio = lambda p, q: SequenceMatcher(None, p, q).ratio() if p and q else 0.0
    return max(ratio(common, joined_a), ratio(common, joined_b), ratio(joined_a, joined_b))


class Index:
    """The existing records of one master, indexed for the three steps above."""

    def __init__(self, records, normalize):
        self.records = records; self.exact = {}; self.prints = {}; self.words = {}
        for r in records:
            label = r['label']; fp = fingerprint(label)
            self.exact.setdefault(normalize(label), []).append(r)
            self.prints.setdefault(fp, []).append(r)
            for w in set(_tokens(label)) | {'^' + fp[:3]}:      # its words, and how it starts (a one-word typo shares no word)
                self.words.setdefault(w, []).append(r)

    def same_letters(self, label):
        fp = fingerprint(label)
        return self.prints.get(fp, []) if len(fp) > SHORT else []

    def similar(self, label, limit=3):
        """Existing names close to label, best first: [(score, record)]."""
        if len(fingerprint(label)) <= SHORT:
            return []
        seen = {}; words = set(_tokens(label)) | {'^' + fingerprint(label)[:3]}
        for w in words:
            for r in self.words.get(w, [])[:5000]:     # only records sharing a word: fast on a big master
                seen[id(r)] = r
        scored = [(round(similarity(label, r['label']), 2), r) for r in seen.values() if len(fingerprint(r['label'])) > SHORT]
        return sorted((x for x in scored if x[0] >= SIMILAR), key=lambda x: -x[0])[:limit]
