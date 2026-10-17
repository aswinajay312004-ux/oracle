"""Ask AI accuracy check on THIS computer's Oracle (read-only account), from the command line:

    python scripts\\run_benchmark.py            # golden questions: config\\golden_questions.json + data\\golden_questions.json

Every question is asked exactly as a person would ask it in Ask AI; its correct answer is worked out separately
by the hand-written SQL in the golden file, on the same database; the results are compared. The run is saved
(Setup → Answer accuracy shows it) and audited. Exit code 0 when every question is correct, 1 otherwise.
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import evidence, local_store as store, main  # noqa: E402


def main_():
    store.init(); evidence.init()
    try:
        report = main.run_benchmark('script')
    except Exception as exc:
        print(f'Could not run the accuracy check: {exc}')
        return 1
    width = max((len(i['question']) for i in report['items']), default=10)
    for i in report['items']:
        print(f'{i["status"]:>15}  {i["question"]:<{width}}  {i.get("detail") or ""}')
    print(f'\n{report["correct"]} of {report["total"]} correct'
          + ('' if report.get('live') else '  (WARNING: Oracle dictionary not live: offline snapshot)'))
    return 0 if report['total'] and report['correct'] == report['total'] else 1


if __name__ == '__main__':
    sys.exit(main_())
