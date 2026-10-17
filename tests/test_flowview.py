"""“Where the data goes” rows (frontend/js/flowview.js), run as the real ES module in Node; skipped where Node is not
installed. A planned row's generated ID is {ref: key} (planner.Ref), so text that starts with $ is shown as text;
after a save, the one-row form view marks each field that differs from what was saved."""
import json
import shutil
import subprocess
from pathlib import Path
import pytest

FLOW = Path(__file__).resolve().parents[1] / 'frontend' / 'js' / 'flowview.js'
NODE = shutil.which('node')
needs_node = pytest.mark.skipif(not NODE, reason='Node.js is not installed')


def js(expr):
    code = f'const f = await import({json.dumps(FLOW.as_uri())});\nprocess.stdout.write(JSON.stringify({expr}));'
    out = subprocess.run([NODE, '--input-type=module', '-e', code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


NODE_SPEC = "{table: 'ORDMAS', key: 'ORDMASID', kind: 'header', labels: {DOCID: 'Order reference', BONO: 'PO'}}"


@needs_node
def test_references_are_chips_and_dollar_text_is_text():
    rec = "{live: {ORDMASID: {ref: 'o1'}, BONO: '$o1', BUYER: {ref: 'm1'}, STYLENO: {ref: 'm2'}}, decoded: {BUYER: 'NEXT'}}"
    out = js(f"['ORDMASID', 'BONO', 'BUYER', 'STYLENO'].map(c => f.valueHtml({NODE_SPEC}, {rec}, c))")
    assert 'new ID on save' in out[0]
    assert out[1] == '$o1'                                     # typed text, never "linked to new record"
    assert out[2].startswith('NEXT') and 'new master' in out[2]
    assert 'linked to new record' in out[3]


@needs_node
def test_saved_form_view_marks_the_fields_that_differ():
    rec = "{live: {DOCID: 'SOMETHING ELSE', BONO: 'PO-1'}, mismatched: ['DOCID'], found: true, matches: false}"
    opts = "{planned: false, columns: {}, written: new Set(['DOCID', 'BONO'])}"
    fields = js(f"f.formFields({NODE_SPEC}, {rec}, ['DOCID', 'BONO'], {opts})")
    assert [('differs from what was saved' in x['html'], x['label'], x['written']) for x in fields] == [
        (True, 'Order reference', True), (False, 'PO', True)]
    planned = js(f"f.formFields({NODE_SPEC}, {rec}, ['DOCID'], {{planned: true, columns: {{}}, written: new Set()}})")
    assert 'differs' not in planned[0]['html']                 # nothing is compared before the save
