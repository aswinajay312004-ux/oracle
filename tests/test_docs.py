"""The documents operators and reviewers rely on exist, and every relative link between them points at a real file."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ['README.md', 'SECURITY.md', 'CHANGELOG.md', 'docs/ARCHITECTURE.md', 'docs/EXCEL_UNDERSTANDING.md',
            'docs/RESEARCH_DECISIONS.md', 'docs/RUNBOOK.md', 'docs/WINDOWS_SETUP.md', 'docs/USER_GUIDE.md',
            'docs/SECURITY_ASVS_L2.md', 'docs/adr/0001-posting-path.md']
LINK = re.compile(r'\]\(([^)\s]+)\)')


def documents():
    return [p for p in [ROOT / 'README.md', ROOT / 'SECURITY.md', ROOT / 'CHANGELOG.md', *sorted((ROOT / 'docs').rglob('*.md'))]
            if p.is_file()]


def test_required_documents_exist():
    missing = [d for d in REQUIRED if not (ROOT / d).is_file()]
    assert not missing, missing


def test_relative_links_resolve():
    broken = []
    for doc in documents():
        for target in LINK.findall(doc.read_text(encoding='utf-8')):
            if re.match(r'[a-z]+:', target) or target.startswith('#'):
                continue                                   # web links and links inside the same page
            path = target.split('#')[0]
            if path and not (doc.parent / path).exists():
                broken.append(f'{doc.relative_to(ROOT)} → {target}')
    assert not broken, broken


def test_the_changelog_names_the_current_version():
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    assert f'## [{version}]' in (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')


def test_the_scope_is_spreadsheets_and_json_only():
    """PDF was removed from scope; user-facing docs must not offer it."""
    for name in ('README.md', 'docs/USER_GUIDE.md', 'docs/WINDOWS_SETUP.md', 'docs/ARCHITECTURE.md'):
        text = (ROOT / name).read_text(encoding='utf-8')
        offers = [line for line in text.splitlines() if 'PDF' in line and not re.search(r'\bno PDF|PDF, OCR|out of scope', line, re.I)]
        assert not offers, (name, offers)
