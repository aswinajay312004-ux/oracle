"""The frontend works under the strict Content-Security-Policy (style-src 'self', no 'unsafe-inline').

The browser blocks every inline style under that policy: a style="…" attribute in index.html or in markup the
JS builds (innerHTML, insertAdjacentHTML), element.setAttribute('style', …) and <style> elements. Blocked
styles fail silently (the screen just looks wrong), so this test keeps them out of the source. Looks belong in
styles.css as classes; truly dynamic values go through the CSSOM after the markup is inserted
(el.style.width = …, el.style.setProperty('--x', …)), which style-src does not block.
tests/test_ui_browser.py drives screens under the strict policy in a real browser."""
import re
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[1] / 'frontend'
FILES = sorted([FRONTEND / 'index.html', *(FRONTEND / 'js').glob('*.js')])
CSS = (FRONTEND / 'styles.css').read_text(encoding='utf-8')

# what style-src 'self' blocks
INLINE = [
    ('a style="…" attribute', re.compile(r'(?<![\w.$-])style\s*=\s*(?!=)["\'`]|(?<![\w.$-])style\s*=\s*\$\{')),
    ("setAttribute('style', …)", re.compile(r'setAttribute(?:NS)?\s*\(\s*(?:null\s*,\s*)?["\'`]style["\'`]', re.I)),
    ('a <style> element', re.compile(r'<style[\s>/]|createElement\s*\(\s*["\'`]style["\'`]', re.I)),
]
HELPER = re.compile(r'(?:mt|mb|ml|w|max-h)-(?:\d+(?:px|vh)?|auto)')   # the spacing/size helpers of the csp-strict section


def problems(text):
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        for what, rx in INLINE:
            if rx.search(line):
                out.append((n, what, line.strip()[:160]))
    return out


@pytest.mark.parametrize('path', FILES, ids=lambda p: p.name)
def test_no_inline_styles(path):
    found = problems(path.read_text(encoding='utf-8'))
    assert not found, (f'{path.relative_to(FRONTEND.parent)}: inline styles are blocked by the CSP (style-src \'self\'). '
                       'Use a class in styles.css, or set a computed value through the CSSOM after inserting the markup: '
                       + '; '.join(f'line {n}: {what}: {line}' for n, what, line in found))


def test_the_check_itself_catches_inline_styles():
    bad = ['<div style="margin:0">', "`<i style='x'>`", '`<svg style=${w}>`', '<p class="a" style = "b">',
           "el.setAttribute('style', 'color:red')", 'el.setAttribute("STYLE", s)', 'svg.setAttributeNS(null, "style", s)',
           '<style>p{}</style>', "document.createElement('style')"]
    good = ["el.style.width = '10px'", "el.style.setProperty('--step', i)", "const keys = ['buyer', 'style']",
            "k === 'style' ? 1 : 2", '${o.style}', 'o.style === x', '<link rel="stylesheet" href="/static/styles.css">',
            'data-style="x"', 'list-style: none']
    assert all(problems(s) for s in bad), [s for s in bad if not problems(s)]
    assert not any(problems(s) for s in good), [s for s in good if problems(s)]


def test_every_helper_class_the_markup_uses_is_defined():
    """A class that replaced an inline style but is misspelt or missing from styles.css silently loses the look."""
    used = set()
    for path in FILES:
        for attr in re.findall(r'class\s*=\s*"([^"]*)"', path.read_text(encoding='utf-8')):
            used |= {c for c in re.sub(r'\$\{[^}]*\}', ' ', attr).split() if HELPER.fullmatch(c)}
    assert used, 'no helper classes found: the pattern is out of date'
    missing = sorted(c for c in used if not re.search(r'\.' + re.escape(c) + r'(?![\w-])', CSS))
    assert not missing, f'used in the markup but not defined in styles.css: {missing}'
