"""CI pipeline (.github/workflows/ci.yml) and the local check scripts (scripts/check.sh, scripts/check.ps1).

The workflow is checked as data (pinned actions, no passwords, every job runs a check-script step), and the
secret scan and JavaScript check are run for real on small throwaway git repositories with planted problems."""
import os
import re
import secrets
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8'))
JOBS = WORKFLOW['jobs']
BASH, GIT, NODE = shutil.which('bash'), shutil.which('git'), shutil.which('node')
PWSH = shutil.which('pwsh') or shutil.which('powershell')
needs_bash_git = pytest.mark.skipif(not (BASH and GIT) or os.name == 'nt', reason='needs bash and git (Linux/macOS)')


def steps(job):
    return JOBS[job]['steps']


def test_workflow_has_every_job_and_least_privilege():
    assert {'secrets', 'lint', 'js', 'tests', 'oracle', 'audit'} <= set(JOBS)
    assert WORKFLOW['permissions'] == {'contents': 'read'}
    assert all(j.get('timeout-minutes') for j in JOBS.values()), 'every job has a time limit'
    tests_job = JOBS['tests']['steps']
    assert any(s.get('with', {}).get('python-version') == '3.13' and s.get('with', {}).get('cache') == 'pip' for s in tests_job)


def test_actions_and_images_are_pinned():
    for name, job in JOBS.items():
        for s in job['steps']:
            if 'uses' in s:
                ref = s['uses']
                official = re.fullmatch(r'actions/(checkout|setup-python|setup-node)@v\d+(\.\d+){0,2}', ref)
                by_commit = re.fullmatch(r'[\w.-]+/[\w./-]+@[0-9a-f]{40}', ref)
                assert official or by_commit, f'{name}: {ref} must be an official action tag or a full commit SHA'
    gitleaks = next(s for s in steps('secrets') if 'GITLEAKS_IMAGE' in s.get('env', {}))['env']['GITLEAKS_IMAGE']
    assert re.fullmatch(r'zricethezav/gitleaks:v\d+\.\d+\.\d+@sha256:[0-9a-f]{64}', gitleaks)


def test_oracle_job_uses_a_throwaway_database_and_no_stored_passwords():
    job = JOBS['oracle']; svc = job['services']['oracle']
    assert svc['image'] == 'gvenzl/oracle-free:23-slim-faststart'
    assert '--health-cmd healthcheck.sh' in svc['options']
    assert svc['env'] == {'ORACLE_RANDOM_PASSWORD': 'true'}
    text = '\n'.join(s.get('run', '') for s in job['steps'])
    for script in ('tests/oracle_replica.py', 'scripts/bootstrap_local.py', 'scripts/grant_bom_tables.py', 'check.sh oracle'):
        assert script in text
    assert text.count('::add-mask::') >= 2, 'generated passwords are masked in the log'
    assert not re.search(r'\$\{\{\s*secrets\.', (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')), 'no repository secrets are needed'
    assert any(s.get('if') == 'always()' and 'rm -f .env.poppys' in s['run'] for s in job['steps'])
    # Every env value that names a password is an expression or a reference, never a literal.
    for j in JOBS.values():
        for env in [WORKFLOW.get('env', {}), j.get('env', {})] + [s.get('env', {}) for s in j['steps']]:
            for k, v in env.items():
                assert 'PASSWORD' not in k or str(v).startswith('${{') or k == 'ORACLE_RANDOM_PASSWORD', k


def test_jobs_run_the_same_steps_as_the_local_scripts():
    for job, step in (('secrets', 'secrets'), ('lint', 'lint'), ('js', 'js'), ('tests', 'tests'), ('oracle', 'oracle')):
        assert any(f'scripts/check.sh {step}' in s.get('run', '') for s in steps(job)), job
    assert any('check.ps1 all' in s.get('run', '') for s in steps('windows'))
    assert any('pip-audit -r requirements.txt' in s.get('run', '') for s in steps('audit'))


def test_dev_tools_are_pinned_and_ruff_keeps_to_bug_rules():
    lines = [x.split('#')[0].strip() for x in (ROOT / 'requirements-dev.txt').read_text(encoding='utf-8').splitlines()]
    tools = [x for x in lines if x and not x.startswith('-r')]
    assert tools and all(re.fullmatch(r'[\w.\[\]-]+==[\w.]+', x) for x in tools), tools
    assert {'ruff', 'pip-audit', 'pytest'} <= {x.split('==')[0] for x in tools}
    lint = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['tool']['ruff']['lint']
    assert {'F', 'E9', 'B'} <= set(lint['select'])
    style = {'E501', 'E701', 'E702', 'E703', 'E741', 'Q', 'I', 'D', 'ANN', 'N', 'C4', 'SIM', 'E', 'W'}
    assert not style & set(lint['select']), 'the compact code style is deliberate: no formatting rules'


def shell_patterns():
    out = subprocess.run([BASH, '-c', 'eval "$(sed -n "/^SECRET_PATTERNS=(/,/^)/p;/^SECRET_FILES=/p" scripts/check.sh)"; '
                          'printf "%s\\n" "${SECRET_PATTERNS[@]}" "$SECRET_FILES"'], cwd=ROOT, capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


@needs_bash_git
def test_powershell_script_uses_the_same_secret_rules():
    ps = (ROOT / 'scripts/check.ps1').read_text(encoding='utf-8')
    assert all(ord(c) < 128 for c in ps), 'Windows PowerShell 5.1 needs an ASCII-only script'
    block = re.search(r'\$SecretPatterns = @\((.*?)\n\)', ps, re.S).group(1)
    ps_patterns = [m.replace("''", "'") for m in re.findall(r"^\s*'((?:[^']|'')*)'\s*$", block, re.M)]
    ps_files = re.search(r"^\$SecretFiles = '((?:[^']|'')*)'", ps, re.M).group(1).replace("''", "'")
    assert ps_patterns + [ps_files] == shell_patterns()


def throwaway_repo(tmp_path, files):
    repo = tmp_path / 'repo'
    (repo / 'scripts').mkdir(parents=True)
    shutil.copy(ROOT / 'scripts/check.sh', repo / 'scripts/check.sh')
    shutil.copy(ROOT / 'scripts/check.ps1', repo / 'scripts/check.ps1')
    shutil.copy(ROOT / '.gitignore', repo / '.gitignore')
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True); (repo / name).write_text(text, encoding='utf-8')
    git(repo, 'init', '-q'); git(repo, 'add', '-A')
    return repo


def git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    subprocess.run([GIT, *args], cwd=repo, env=env, check=True, capture_output=True)


def check(repo, step, ps=False):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_') and k != 'CI'}
    cmd = [PWSH, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', 'scripts/check.ps1', step] if ps else [BASH, 'scripts/check.sh', step]
    r = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout + r.stderr


def planted_leaks():
    password = 'Pp' + secrets.token_hex(24)            # the format scripts/bootstrap_local.py generates
    other = 'Zq' + secrets.token_hex(8)
    return password, other, [
        ('.env.poppys', f'ORACLE_USER=POPPYS_APP\nORACLE_PASSWORD={password}\n', 'must not be committed: .env.poppys'),
        ('backend/settings.py', f"ORACLE_PASSWORD = '{other}'\n", 'possible password or key at backend/settings.py:1'),
        ('docs/notes.md', f'connect with POPPYS_APP/{other}@127.0.0.1:1521/FREEPDB1\n', 'possible password or key at docs/notes.md:1'),
        ('data/poppys.sqlite3', 'local store\n', 'must not be committed: data/poppys.sqlite3'),
        ('docs/setup.md', f'The app password is {password}\n', 'possible password or key at docs/setup.md:1'),
        ('deploy/server.txt', '-----BEGIN ' + 'RSA PRIVATE KEY-----\nMIIEow\n', 'possible password or key at deploy/server.txt:1'),
    ]


CLEAN = {'.env.example': 'ORACLE_USER=POPPYS_APP\nORACLE_PASSWORD=\n', 'README.md': 'Set ORACLE_PASSWORD=... in .env.poppys\n',
         'backend/app.py': "password = os.environ.get('POPPYS_ORACLE_PASSWORD')\n"}


@needs_bash_git
def test_secret_scan_passes_a_clean_repository(tmp_path):
    code, out = check(throwaway_repo(tmp_path, CLEAN), 'secrets')
    assert code == 0 and 'OK: nothing secret is committed.' in out, out


@needs_bash_git
@pytest.mark.parametrize('case', range(6))
def test_secret_scan_fails_on_committed_settings_and_passwords_without_printing_them(tmp_path, case):
    password, other, leaks = planted_leaks()
    name, text, message = leaks[case]
    repo = throwaway_repo(tmp_path, CLEAN)
    (repo / name).parent.mkdir(parents=True, exist_ok=True); (repo / name).write_text(text, encoding='utf-8')
    git(repo, 'add', '-f', name)                       # force-added past .gitignore, as a real leak would be
    code, out = check(repo, 'secrets')
    assert code == 1 and message in out, out
    assert password not in out and other not in out, 'the scan never prints the secret itself'


@needs_bash_git
def test_secret_scan_fails_when_gitignore_stops_ignoring_the_settings_file(tmp_path):
    repo = throwaway_repo(tmp_path, CLEAN)
    (repo / '.gitignore').write_text('data/\n', encoding='utf-8')
    code, out = check(repo, 'secrets')
    assert code == 1 and 'no longer ignores .env.poppys' in out, out


@pytest.mark.skipif(not (PWSH and GIT), reason='PowerShell and git are not installed')
def test_powershell_secret_scan_matches_the_shell_one(tmp_path):
    password, other, leaks = planted_leaks()
    repo = throwaway_repo(tmp_path, CLEAN)
    assert check(repo, 'secrets', ps=True)[0] == 0
    for name, text, _ in leaks[1:3]:
        (repo / name).parent.mkdir(parents=True, exist_ok=True); (repo / name).write_text(text, encoding='utf-8')
    code, out = check(repo, 'secrets', ps=True)
    assert code == 1 and 'backend/settings.py:1' in out and 'docs/notes.md:1' in out, out
    assert other not in out


GOOD_JS = {'frontend/index.html': '<!doctype html>\n<script type="module" src="/static/js/app.js"></script>\n',
           'frontend/js/app.js': "import { x } from './util.js';\nexport const y = () => x + 1;\n",
           'frontend/js/util.js': 'export const x = 1;\n'}


@needs_bash_git
@pytest.mark.skipif(not NODE, reason='Node.js is not installed')
@pytest.mark.parametrize('name,text,message', [
    (None, None, 'OK: 2 modules parse'),
    ('frontend/js/bad.js', 'export const z = (;\n', 'frontend/js/bad.js'),
    ('frontend/js/view.js', 'export const html = `<button onclick="go()">Go</button>`;\n', 'inline on*= handler'),
    ('frontend/js/cdn.js', "export const s = '<script src=\"https://cdn.example.com/x.js\"></script>';\n", 'resource from another site'),
    ('frontend/js/eval.js', "export const run = (t) => eval(t);\n", 'code built from strings'),
    ('frontend/index.html', '<!doctype html>\n<script>alert(1)</script>\n', 'inline <script>'),
])
def test_js_check_finds_syntax_errors_and_csp_breaks(tmp_path, name, text, message):
    files = dict(GOOD_JS)
    if name:
        files[name] = text
    code, out = check(throwaway_repo(tmp_path, files), 'js')
    assert (code == 0) == (name is None) and message in out, out
