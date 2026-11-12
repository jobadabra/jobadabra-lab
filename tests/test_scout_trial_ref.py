"""A trial run of the central scout takes only a branch of our own engine repo (owner, 11 Nov 2026, asked via AskUserQuestion: "Accept, branches only (Recommended)").
Guard: security: the trial runs the branch's code with the index key, so a pull-request ref, a tag, a raw commit id or a fork's branch must be refused before the engine is checked out.
The guard step's own shell is run here with a stand-in `git` that knows two branches, for every job that checks the engine out (the scout and the board pass, 12 Nov 2026)."""
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / '.github' / 'workflows' / 'central-scout.yml'
STEP = 'A trial takes only a branch of our own engine repo'


def step_scripts():
    """The guard step's shell, once per job that has it."""
    lines, scripts = WORKFLOW.read_text(encoding='utf-8').splitlines(), []
    for start in (i for i, line in enumerate(lines) if STEP in line):
        run = next(i for i in range(start, len(lines)) if lines[i].strip() == 'run: |')
        indent = len(lines[run + 1]) - len(lines[run + 1].lstrip())
        body = []
        for line in lines[run + 1:]:
            if line.strip() and len(line) - len(line.lstrip()) < indent:
                break
            body.append(line[indent:])
        scripts.append('\n'.join(body))
    return scripts


class TrialTakesOnlyOurBranches(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        git = Path(self.dir.name) / 'git'
        git.write_text('#!/bin/sh\n# stand-in: `git ls-remote --exit-code --heads <url> refs/heads/<name>` knows two branches\n'
                       'case "$5" in refs/heads/sk-probe-allowance|refs/heads/main) exit 0;; *) exit 2;; esac\n', encoding='utf-8')
        git.chmod(0o755)

    def run_step(self, script, ref):
        env = {**os.environ, 'PATH': f'{self.dir.name}:{os.environ["PATH"]}', 'ENGINE_REF': ref, 'ENGINE_REPO': 'org/jobadabra'}
        return subprocess.run(['bash', '-c', script], env=env, capture_output=True, text=True)

    def test_every_job_s_guard_is_the_same_shell(self):
        scripts = step_scripts()
        self.assertEqual(len(scripts), 2, 'the scout job and the board pass job')
        self.assertEqual(len(set(scripts)), 1, 'one guard: a change to one copy goes to the other')

    def test_a_branch_of_our_repo_passes(self):
        for script in step_scripts():
            self.assertEqual(self.run_step(script, 'sk-probe-allowance').returncode, 0)

    def test_everything_else_is_refused(self):
        for script in step_scripts():
            for ref in ('refs/pull/5/head', 'pull/5/head', 'refs/tags/v1', 'v1.2.3', 'a3f9c2d', 'a3f9c2d41b5e6f708192a3b4c5d6e7f801234567', 'no-such-branch',
                        'main; echo hi', '../main', '-x', 'fork:branch', 'sk-probe-allowance '):
                with self.subTest(ref):
                    done = self.run_step(script, ref)
                    self.assertNotEqual(done.returncode, 0, ref)
                    self.assertIn('is refused', done.stdout)

    def test_the_guard_runs_before_the_engine_is_checked_out_and_only_for_a_trial(self):
        text = WORKFLOW.read_text(encoding='utf-8')
        guards = [m.start() for m in re.finditer(re.escape(STEP), text)]
        for guard in guards:
            checkout = text.index('- name: The public engine', guard)
            self.assertNotIn('actions/checkout', text[guard:checkout])
            self.assertRegex(text[guard:checkout], r"if: env\.TRIAL == 'true'")


if __name__ == '__main__':
    unittest.main()
