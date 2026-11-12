"""Every lab workflow step that can tell the owner on an old CI bot carries the switchboard key, so the owner's Notifications switch applies.

Guard: release - a step with the CI-alerts bot (or a direct Telegram call) and no SELFHEAL_PUBLISH_KEY / switchboard-key bypasses Admin → Notifications; the call then fails open to the old bot unseen.
Why (12 Nov 2026, owner: "Still getting on old telegram bots: CI Alerts, Build Log ..."): Board discovery's "AI calls failed" card came from ai-skipped-alert with bot-token only; central-scout's failure was a curl.
Scope of a step = the workflow's env + its job's env + the step; comments are not code. The Brain's own briefs and buttons (BRAIN_BOT_TOKEN, a conversation) are not alerts and are out."""
import re
import unittest
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / '.github' / 'workflows'
OLD_BOT = re.compile(r'secrets\.(CI_ALERTS_TELEGRAM_BOT_TOKEN|BUILDLOG_TELEGRAM_TOKEN)\b|api\.telegram\.org|uses: \./\.github/actions/ai-skipped-alert|product_brain\.telegram\(')
KEY = re.compile(r'SELFHEAL_PUBLISH_KEY|switchboard-key')


def code(text):
    return '\n'.join(re.sub(r'\s+#.*$', '', line) for line in text.split('\n') if not line.lstrip().startswith('#'))


def steps(text):
    """-> [(label, scope, step)]"""
    head, _, jobs = code(text).partition('\njobs:')
    out = []
    for block in re.split(r'(?m)\n(?=  [A-Za-z0-9_-]+:\s*$)', jobs)[1:]:
        job = block.split('\n', 1)[0].strip().rstrip(':')
        pre, _, body = block.partition('\n    steps:')
        for index, step in enumerate(re.split(r'\n(?=      - )', body)[1:], 1):
            name = re.search(r'name:\s*(.+)', step)
            out.append((f'{job} step {index}' + (f' ({name.group(1).strip()[:50]})' if name else ''), head + pre, step))
    return out


class SwitchboardKeyTest(unittest.TestCase):
    def test_no_step_can_tell_the_owner_on_an_old_bot_without_the_switchboard_key(self):
        found = 0
        for path in sorted(WORKFLOWS.glob('*.yml')):
            for where, scope, step in steps(path.read_text(encoding='utf-8')):
                if not OLD_BOT.search(step):
                    continue
                found += 1
                with self.subTest(workflow=path.name, step=where):
                    self.assertRegex(scope + step, KEY, f'{path.name}: {where} can send on an old bot but has no switchboard key')
        self.assertGreaterEqual(found, 5, f'positive control: {found} sending steps found')


if __name__ == '__main__':
    unittest.main()
