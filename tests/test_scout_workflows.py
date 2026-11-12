"""The central scout's and the product brain's workflows, which moved here from the public repo (1 Nov 2026, rs 6b/6; owner: "jobadabra should have no web, no worker").
The wiring tests that read their text moved with them, so the public repo has no server workflow to read. The engine side of each assertion
(scout_core's budgets, the relay, the brain's Codex step) stays tested in the public repo; JOBADABRA_PUBLIC points at its checkout."""
import http.server
import io
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parents[1]
ENGINE = Path(os.getenv('JOBADABRA_PUBLIC') or Path.home() / 'jobadabra')
WORKFLOWS = OPS / '.github' / 'workflows'
ACTIVE = True   # Why (2 Nov 2026, rs 6b-4): the public copies are deleted (jobadabra#2962), so lab is the only scheduler; set back to False only together with removing the schedule: lines.


def code(name):
    """-> the workflow's text with its comments cut, so a word in a comment never counts."""
    text = (WORKFLOWS / name).read_text(encoding='utf-8')
    return '\n'.join(re.sub(r'\s#.*$', '', line) for line in text.splitlines() if not line.lstrip().startswith('#'))


def budget_seconds(name):
    """-> an engine budget in seconds, read from src/scout_core.py's text (`NAME = 8 * 60`), so this test never imports the engine."""
    match = re.search(r'(?m)^%s\s*=\s*(\d+)\s*\*\s*(\d+)' % name, (ENGINE / 'src' / 'scout_core.py').read_text(encoding='utf-8'))
    assert match, f'{name} is no longer "N * M" in src/scout_core.py: update budget_seconds'
    return int(match.group(1)) * int(match.group(2))


def step(text, title):
    """-> the text of the workflow step named `title`, up to the next step."""
    match = re.search(r'(?ms)^      - (?:&[\w-]+\n        )?name: %s\n.*?(?=^      - |\Z)' % re.escape(title), text)   # a step may carry an anchor (&memory-from-site)
    assert match, f'no step named "{title}"'
    return match.group(0)


def script_of(step_text):
    """-> the dedented `run: |` block of a step, as the runner writes it to a file."""
    lines = step_text.splitlines()
    start = next(i for i, line in enumerate(lines) if re.match(r'\s+run: \|\s*$', line))
    indent = len(lines[start]) - len(lines[start].lstrip()) + 2
    body = []
    for line in lines[start + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line[indent:])
    return '\n'.join(body) + '\n'


def memory_tar():
    """-> a gzipped tar of the three things the scout keeps, as the site would hand it back."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
        for name, data in {'jobs.sqlite': b'sqlite', 'cache/a.json': b'{"a":1}', 'geo.json': b'{"Zurich":[47,8]}'}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class FakeSite:
    """The site's /api/scout-memory as far as a workflow step can tell: it answers what it is told and records what it was asked."""

    def __init__(self):
        self.status, self.body, self.headers, self.requests = 200, b'', {}, []
        self.queue, self.short = [], 0   # answers to give first, one each; bytes the Content-Length promises and the body never delivers
        site = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def answer(self):
                length = int(self.headers.get('Content-Length') or 0)
                site.requests.append({'method': self.command, 'path': self.path, 'auth': self.headers.get('Authorization'), 'body': self.rfile.read(length) if length else b''})
                status, body, headers = site.queue.pop(0) if site.queue else (site.status, site.body, site.headers)
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header('Content-Length', str(len(body) + site.short))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_PUT = answer

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.server.server_port}/api/scout-memory'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class EveryWorkflowIsValidYaml(unittest.TestCase):
    def test_no_plain_value_holds_a_colon_and_space(self):
        """Why (2 Nov 2026, rs): central-scout.yml had `- name: ... 2026: no App token`; a `: ` inside an unquoted value is a YAML error, so GitHub
        showed "workflow file issue", ran no job and refused workflow_dispatch (422) until the line was reworded. PyYAML is not a lab dependency,
        so this checks the class by text: a value that is not quoted, a block scalar or a flow collection may not contain `: `."""
        bad = []
        for path in sorted(WORKFLOWS.glob('*.yml')):
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                match = re.match(r'^\s*(?:-\s+)?[\w.-]+:\s+(?P<value>\S.*)$', re.sub(r'\s#.*$', '', line))
                if not match or re.match(r'''['"|>\[{&*!%@`]''', match['value']) or '${{' in line:
                    continue
                if ': ' in match['value']:
                    bad.append(f'{path.name}:{number}: {line.strip()[:100]}')
        self.assertEqual(bad, [], 'an unquoted ": " in a YAML value: quote the value or reword it')


class CentralScout(unittest.TestCase):
    def setUp(self):
        self.text = (WORKFLOWS / 'central-scout.yml').read_text(encoding='utf-8')
        self.code = code('central-scout.yml')

    def test_the_engine_is_the_other_checkout_and_the_owner_is_a_variable(self):
        """Why (1 Nov 2026): in this repo "this repo" is the lab; the scout runs the public engine, so it is checked out at the root and the lab at ops. The org move changes the owner once, in OWNER_OF_JOBADABRA."""
        self.assertIn('repository: ${{ vars.OWNER_OF_JOBADABRA }}/jobadabra', self.code)
        self.assertRegex(self.code, r'(?s)uses: actions/checkout@v\d+\s+with:\s+path: ops')
        self.assertNotRegex(self.code, r'(?i)GarryOne/', 'no literal owner in a uses: or repository: line')
        self.assertNotIn('create-github-app-token', self.code, 'the lab is public since 1 Nov 2026: no App token to read it')

    def test_it_waits_for_the_switch(self):
        self.assertEqual(bool(re.search(r'(?m)^\s{2}schedule:', self.code)), ACTIVE, 'scheduled since 6b-4; both also start by hand')
        self.assertIn('workflow_dispatch:', self.code)

    def test_the_scout_keeps_its_places_between_runs(self):
        # Why (5 Nov 2026, jobadabra#2915): a comment after a path in a `path: |` block is part of the path, so the places were never kept; the line must be the path alone.
        self.assertEqual(len(re.findall(r'(?m)^\s*data/geo\.json$', self.text)), self.text.count('data/jobs.sqlite') - self.text.count('--db data/jobs.sqlite'),
                         'every cache restore and save keeps the places the AI judged (asked once, ever)')

    def test_the_one_time_restore_of_the_old_path_list_is_gone(self):
        # Why (6 Nov 2026): #80 added it for the first run after the path list changed; run 42118339535 saved under the new list, so the cache is found again and the site's copy covers a lost one.
        self.assertNotIn('Restore a cache saved under the old path list', self.text)
        self.assertNotIn('data/geo.json   # where each job place is', self.text)

    def test_its_ai_runs_behind_the_broker_relay_on_the_200_call_cap(self):
        """Owner, 26 Oct 2026 "keep claude for the scout"; 31 Oct 2026 "We should use fully the Codex": no Anthropic key, no Free AI, no address off this machine."""
        self.assertNotRegex(self.code, r'(?i)anthropic', 'the scout holds no Anthropic key and no base URL')
        self.assertNotRegex(self.code, r'(?i)free_ai|JOBADABRA_AI_ENGINE', 'the scout never switches to Free AI or another engine')
        self.assertIn('id-token: write', self.code)
        self.assertIn("CENTRAL_AI_CAP: '200'", self.code)   # 12 Nov 2026: with no cap a run was cancelled at its 30 minutes and published nothing (run 43135722488)
        # 12 Nov 2026 (bp): the relay also lets through the calls the engine counts outside its cap (board pass, Google's judge, a board's country, the probe), and no more
        # 12 Nov 2026 (bj, owner: "own job"): the board pass left the scout's run for its own job, behind its own relay, capped by the engine's JOB_CALLS; no extra calls on the scout's cap
        self.assertNotIn('CENTRAL_AI_EXTRA', self.code)
        self.assertIn('broker-relay-run.mjs --job central-scout --cap "$CENTRAL_AI_CAP" -- python -m src scout', self.code)
        self.assertIn("steps.scout.outputs.skipped == 'true'", self.code)
        self.assertNotIn('--board-pass', self.code, '12 Nov 2026 (bd): the board pass runs in board-discovery.yml (BoardDiscovery below)')
        self.assertIn('ai-skipped-alert', self.code, 'a refused call tells the owner once')

    def test_it_is_given_the_title_seed_and_the_country_and_the_search_key_under_their_own_names(self):
        self.assertIn('SCOUT_TITLE_KINDS: ops/config/title_kinds.json.gz', self.text)
        self.assertIn('SCOUT_BOARD_DB: data/boards.sqlite', self.text, "board discovery's boards are published with the index")

    @unittest.skipUnless((ENGINE / 'src' / 'scout_core.py').exists(), 'the public engine is not checked out next to this repo (lab-ci sets JOBADABRA_PUBLIC, so it runs there)')
    def test_the_job_limit_covers_setup_both_budgets_and_the_publish_reserve(self):
        limit = int(re.search(r'timeout-minutes: (\d+)', self.text[self.text.index('\n  scout:'):]).group(1))   # the scout job's, not the gate's (11 Nov 2026)
        setup, reserve = 6, 3   # minutes: checkout, installs, Common Crawl (3 min cap) before the scout; upload and cost report after
        self.assertLessEqual(setup + (budget_seconds('CENTRAL_BUDGET_S') + budget_seconds('VERIFY_BUDGET_S')) / 60 + reserve, limit)


class ScoutMemoryOnTheSite(unittest.TestCase):
    """Owner, 6 Nov 2026: "Yes, copy it to R2 (Recommended)". The scout's memory (jobs.sqlite, cache, geo.json) lived only in this repo's GitHub cache; on 2 Nov a repo move left it behind
    (6,833 → 3,871 feeds, the title answers lost). The site keeps a private copy (web#457, /api/scout-memory): it comes back when no cache is found, and every run saves it. These tests play the
    two steps' own scripts against a fake site, so what they prove is what the runner runs: a failed read or save says one line and never fails the run."""
    RESTORE = "Restore the scout's memory from the site when the cache was not found"
    SAVE = "Save the scout's memory to the site"

    def setUp(self):
        self.text = (WORKFLOWS / 'central-scout.yml').read_text(encoding='utf-8')
        self.site = FakeSite()
        self.addCleanup(self.site.close)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        (self.root / 'temp').mkdir()

    def run_step(self, title, key='the-key', url=None):
        """Runs the step's script the way the runner does (bash -e, its environment) and -> its output; it must always exit 0."""
        script = self.root / 'step.sh'
        script.write_text(script_of(step(self.text, title)))
        env = {'PATH': os.environ['PATH'], 'HOME': str(self.root), 'RUNNER_TEMP': str(self.root / 'temp'), 'GITHUB_RUN_ID': '42118339535', 'GITHUB_RUN_ATTEMPT': '1',
               'SCOUT_MEMORY_URL': url or self.site.url, 'INDEX_PUBLISH_KEY': key, 'GITHUB_OUTPUT': str(self.root / 'temp' / 'output')}
        done = subprocess.run(['bash', '-e', str(script)], cwd=self.workspace, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        if key:
            self.assertNotIn(key, done.stdout + done.stderr, 'the key is never printed')
        return done.stdout.strip()

    def make_data(self):
        data = self.workspace / 'data'
        (data / 'cache').mkdir(parents=True)
        (data / 'jobs.sqlite').write_bytes(b'sqlite')
        (data / 'cache' / 'a.json').write_bytes(b'{"a":1}')
        (data / 'geo.json').write_bytes(b'{}')

    def test_the_steps_sit_around_the_scout_and_only_the_site_copy_needs_the_key(self):
        order = re.findall(r'(?m)^      - (?:&[\w-]+\n        )?name: (.*)$', self.text)
        self.assertLess(order.index("Restore the scout's candidate list"), order.index(self.RESTORE))
        self.assertLess(order.index(self.RESTORE), order.index('Scout and publish the index'))
        self.assertLess(order.index('Scout and publish the index'), order.index(self.SAVE))
        restore, save = step(self.text, self.RESTORE), step(self.text, self.SAVE)
        self.assertIn("if: steps.candidates.outputs.cache-matched-key == ''", restore, 'the cache stays the first home: the site is asked only when it found nothing')
        self.assertIn('if: always()', save, 'a run that fails later still saves what it learned, as the cache save does')
        self.assertIn('SCOUT_MEMORY_URL: https://www.jobadabra.top/api/scout-memory', self.text)
        for text in (restore, save):
            self.assertIn('INDEX_PUBLISH_KEY: ${{ secrets.INDEX_PUBLISH_KEY }}', text)
            self.assertIn('continue-on-error: true', text, 'whatever fails here never fails the run')
            self.assertNotRegex(script_of(text), r'secrets\.|\$\{\{', 'the key reaches curl from the environment, never written into the script')

    def test_with_no_cache_the_memory_comes_back_from_the_site(self):
        self.site.body, self.site.headers = memory_tar(), {'X-Scout-Memory': '2026-11-05-42118339535-1'}
        self.assertEqual(self.run_step(self.RESTORE), 'scout memory: restored from R2 2026-11-05')
        data = self.workspace / 'data'
        self.assertEqual((data / 'jobs.sqlite').read_bytes(), b'sqlite')
        self.assertEqual((data / 'cache' / 'a.json').read_bytes(), b'{"a":1}')
        self.assertEqual((data / 'geo.json').read_bytes(), b'{"Zurich":[47,8]}')
        self.assertEqual([(r['method'], r['path'], r['auth']) for r in self.site.requests], [('GET', '/api/scout-memory', 'Bearer the-key')])
        self.assertEqual((self.root / 'temp' / 'output').read_text(), 'restored=true\n', 'the boards job saves only a memory that came back')

    def test_with_nothing_on_the_site_the_step_says_it_did_not_restore(self):
        self.site.status, self.site.body = 404, b'none saved'
        self.run_step(self.RESTORE)
        self.assertFalse((self.root / 'temp' / 'output').exists(), 'nothing came back: no restored=true')

    def test_a_failed_read_says_one_line_with_the_answer_and_never_fails_the_run(self):
        warn = '::warning::'   # a run warning, so a rotated key does not hide in a green line; an empty bucket (the normal first run) is no warning
        cases = [(404, b'{"ok":false,"error":"none saved"}', '', 'HTTP 404: {"ok":false,"error":"none saved"}', 1),   # an empty bucket: asked once, not retried
                 (404, b'Not found', warn, 'HTTP 404: Not found', 1),   # a wrong or rotated key reads differently from "nothing saved"
                 (503, b'{"ok":false,"error":"not configured"}', warn, 'HTTP 503', 3),   # a busy site is asked three times
                 (200, b'this is not a tar', warn, 'the copy did not unpack', 1)]
        for status, body, mark, wanted, asked in cases:
            with self.subTest(status=status, body=body):
                self.site.status, self.site.body = status, body
                self.site.requests.clear()
                out = self.run_step(self.RESTORE)
                self.assertEqual(len(out.splitlines()), 1, out)
                self.assertTrue(out.startswith(f'{mark}scout memory: not restored from R2 ('), out)
                self.assertIn(wanted, out)
                self.assertEqual(len(self.site.requests), asked)
                self.assertFalse((self.workspace / 'data').exists(), 'nothing half-restored is left behind')
        out = self.run_step(self.RESTORE, url='http://127.0.0.1:1/api/scout-memory')
        self.assertTrue(out.startswith(f'{warn}scout memory: not restored from R2 ('), out)
        self.assertIn('HTTP 000', out, 'the site unreachable')
        self.site.requests.clear()
        self.assertEqual(self.run_step(self.RESTORE, key=''), f'{warn}scout memory: not restored from R2 (no INDEX_PUBLISH_KEY)')
        self.assertEqual(self.site.requests, [])

    def test_one_blip_does_not_start_the_scout_empty(self):
        # Why (6 Nov 2026, review): the empty start is cached by the run, so later runs find a cache and never ask the site again; the read is tried three times.
        self.site.queue = [(503, b'busy', {}), (502, b'bad gateway', {})]
        self.site.body, self.site.headers = memory_tar(), {'X-Scout-Memory': '2026-11-05-42118339535-1'}
        self.assertEqual(self.run_step(self.RESTORE), 'scout memory: restored from R2 2026-11-05')
        self.assertEqual(len(self.site.requests), 3)

    def test_a_read_that_breaks_after_the_status_never_prints_the_memory_into_the_public_log(self):
        self.site.body, self.site.short = b'PRIVATE-MEMORY-BYTES' * 20, 100   # promises 100 more bytes than it sends: curl fails with a 200 already read
        out = self.run_step(self.RESTORE)
        self.assertTrue(out.startswith('::warning::scout memory: not restored from R2 (HTTP 200: curl exit '), out)
        self.assertNotIn('PRIVATE-MEMORY', out)

    def test_every_run_saves_what_the_cache_keeps(self):
        self.make_data()
        self.site.body = b'{"ok":true,"key":"2026-11-06-42118339535-1","bytes":1,"dropped":0}'
        out = self.run_step(self.SAVE)
        [request] = self.site.requests
        self.assertEqual((request['method'], request['path'], request['auth']), ('PUT', '/api/scout-memory?run=42118339535-1', 'Bearer the-key'))
        self.assertEqual(out, f"scout memory: saved to R2 {len(request['body'])} bytes")
        with tarfile.open(fileobj=io.BytesIO(request['body']), mode='r:gz') as tar:
            self.assertLessEqual({'jobs.sqlite', 'cache/a.json', 'geo.json'}, set(tar.getnames()))
            self.assertEqual(tar.extractfile('geo.json').read(), b'{}')

    def test_a_refused_or_failed_save_says_one_line_and_never_fails_the_run(self):
        self.make_data()
        self.site.status, self.site.body = 409, b'{"ok":false,"error":"4999 bytes would replace 10000"}'
        out = self.run_step(self.SAVE)
        self.assertEqual(out, '::warning::scout memory: not saved to R2 (HTTP 409: {"ok":false,"error":"4999 bytes would replace 10000"})')   # an empty start: the one a person should see
        out = self.run_step(self.SAVE, url='http://127.0.0.1:1/api/scout-memory')
        self.assertTrue(out.startswith('::warning::scout memory: not saved to R2 (HTTP 000'), out)
        self.site.requests.clear()
        self.assertEqual(self.run_step(self.SAVE, key=''), '::warning::scout memory: not saved to R2 (no INDEX_PUBLISH_KEY)')
        shutil.rmtree(self.workspace / 'data')
        self.assertEqual(self.run_step(self.SAVE), 'scout memory: not saved to R2 (no data folder)')
        self.assertEqual(self.site.requests, [], 'nothing was sent without a key or without data')


class BoardDiscovery(unittest.TestCase):
    """Guard: data. 12 Nov 2026, owner, asked via AskUserQuestion: "Separate lab workflow (Recommended)". Board discovery has its own concurrency group and its own memory
    (data/boards.sqlite): it never saves the scout's memory, so a run of one never replaces the other's newer copy (Gitar on lab#96: a newest near-empty save is what the
    next scout run restores). The scout reads its boards read only and publishes them."""
    def setUp(self):
        self.text = (WORKFLOWS / 'board-discovery.yml').read_text(encoding='utf-8')
        self.scout = (WORKFLOWS / 'central-scout.yml').read_text(encoding='utf-8')

    def test_it_runs_alone_on_its_own_cap_and_by_hand_or_from_the_worker(self):
        self.assertIn("'board-discovery'", self.text, 'its own concurrency group: never waits behind the scout or replaces its pending run')
        self.assertNotIn('schedule:', self.text, 'started by the www Worker, never a GitHub cron')
        self.assertIn('broker-relay-run.mjs --job board-discovery --cap "$cap" -- python -m src scout --board-pass --db data/boards.sqlite', self.text)
        self.assertIn("steps.boards.outputs.skipped == 'true'", self.text)
        for name in ('cells: {description', 'country: {description', 'SCOUT_BOARD_COUNTRY: ${{ inputs.country }}', 'SCOUT_BOARD_CELLS: ${{ inputs.cells }}',
                     'SCOUT_SERPAPI_KEY: ${{ secrets.CENTRAL_SCOUT_SERPAPI_KEY }}', 'ops/config/board_priority.txt',
                     'SCOUT_BROWSER_FINAL: ${{ inputs.browser_final }}'):   # lab#99: a stop a trial met in the browser is never opened again
            self.assertIn(name, self.text)
        self.assertNotIn('SERPAPI_API_KEY:', self.text, "that name turns on the role searches' SerpApi lane, outside the month cap")

    def test_it_never_saves_the_scout_s_memory(self):
        saves = [part for part in self.text.split('\n      - ') if 'uses: actions/cache/save' in part]
        self.assertEqual(len(saves), 1)
        self.assertIn('path: data/boards.sqlite\n', saves[0])
        self.assertIn('key: board-discovery-db-', saves[0])
        self.assertNotIn('central-scout-db-', saves[0])
        self.assertNotIn('-T "$zipped"', self.text, "never the site's copy of the scout's memory either")
        restore = step(self.scout, "Board discovery's boards (its newest memory, read only)")
        self.assertIn('restore-keys: board-discovery-db-', restore)
        self.assertNotIn('boards.sqlite', step(self.scout, "Save the scout's candidate list"), 'the scout never saves discovery\'s memory')


class ProductBrain(unittest.TestCase):
    def setUp(self):
        self.text = (WORKFLOWS / 'product-brain.yml').read_text(encoding='utf-8')
        self.code = code('product-brain.yml')

    def test_the_engine_is_the_first_checkout_with_a_months_commits(self):
        self.assertRegex(self.code, r'(?s)repository: \$\{\{ vars\.OWNER_OF_JOBADABRA \}\}/jobadabra\s+ref: main\s+fetch-depth: 300')
        self.assertNotRegex(self.code, r'(?i)GarryOne/')

    def test_it_waits_for_the_switch(self):
        self.assertEqual(bool(re.search(r'(?m)^\s{2}schedule:', self.code)), ACTIVE)
        self.assertIn('workflow_dispatch:', self.code)

    def test_the_brain_runs_only_on_the_broker_and_a_refusal_alerts_once(self):
        self.assertNotRegex(self.code, r'(?i)claude-code-action|anthropic_api_key|ANTHROPIC|BRAIN_AI|ai_provider', 'no Anthropic path is left in the brain')
        self.assertIn('id-token: write', self.text)
        self.assertRegex(self.text, r'brain-codex\.mjs --step "\$STEP"')
        self.assertRegex(self.text, r'brain-codex\.mjs --step explore')
        self.assertRegex(self.text, r"Send the brief\n\s+if: [^\n]*steps\.brief_ai\.outputs\.skipped != 'true'")
        self.assertRegex(self.text, r"Send the plan\n\s+if: [^\n]*steps\.explore_ai\.outputs\.skipped != 'true'")
        self.assertIn('uses: ./.github/actions/ai-skipped-alert', self.text)


class CentralScoutGate(unittest.TestCase):
    """Owner, 11 Nov 2026, asked via AskUserQuestion: "Yes, the pane builds it (Recommended)" (jobadabra-web#560 starts the scout from the Worker at :25; the cron at :26 is only the backup).
    The concurrency group queues a late backup instead of dropping it, so each slot would run the scout twice (~22 min, ~40 Codex broker calls) without the gate job.
    These tests play the gate's script with the real jq over the run lists GitHub returns (a fake `gh` hands them over; a fake `date` stands in for GNU date, which the Linux runner has)."""

    def setUp(self):
        self.text = (WORKFLOWS / 'central-scout.yml').read_text(encoding='utf-8')
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        (self.root / 'bin').mkdir()
        gh = self.root / 'bin' / 'gh'
        gh.write_text('#!/bin/bash\nif [ -n "$FAKE_GH_FAIL" ]; then echo "HTTP 502" >&2; exit 1; fi\nwhile [ $# -gt 0 ]; do if [ "$1" = --jq ]; then jq -r "$2" "$FAKE_RUNS"; exit; fi; shift; done\n')
        date = self.root / 'bin' / 'date'
        date.write_text('#!/bin/bash\necho 2026-11-11T13:00:00Z\n')
        for tool in (gh, date):
            tool.chmod(0o755)

    def gate(self, event, runs, fail=False):
        """-> (the gate's `skip` output, its log) for a run of `event` while GitHub lists `runs` (workflow_runs objects, created in the last 3 h) besides run 900."""
        (self.root / 'runs.json').write_text(__import__('json').dumps({'workflow_runs': runs}))
        script = self.root / 'gate.sh'
        script.write_text(script_of(step(self.text.replace('      - id: recent', '      - name: gate\n        id: recent'), 'gate')))
        out = self.root / 'output'
        out.write_text('')
        env = {'PATH': f"{self.root / 'bin'}:{os.environ['PATH']}", 'HOME': str(self.root), 'EVENT': event, 'GITHUB_OUTPUT': str(out), 'GITHUB_RUN_ID': '900',
               'GITHUB_REPOSITORY': 'jobadabra/jobadabra-lab', 'FAKE_RUNS': str(self.root / 'runs.json'), 'GH_TOKEN': 'x'}
        if fail:
            env['FAKE_GH_FAIL'] = '1'
        done = subprocess.run(['bash', '-e', str(script)], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        values = dict(line.split('=', 1) for line in out.read_text().splitlines())
        return values['skip'] if 'skip' in values else None, done.stdout + done.stderr

    def test_the_workflow_wires_the_gate_before_the_scout_and_the_backup_follows_the_worker(self):
        self.assertIn("cron: '26 4,10,16,22 * * *'", self.text, "the Worker (jobadabra-web, shared/intel-start.js) starts it at :25; the backup is the next minute")
        self.assertRegex(self.text, r"(?ms)^  gate:\n.*?permissions:\n      actions: read\n")
        self.assertRegex(self.text, r"(?ms)^  scout:\n    needs: gate\n    if: needs\.gate\.outputs\.skip != 'true'\n")
        self.assertNotIn('\n  boards:\n', self.text, '12 Nov 2026 (bd): the board pass has its own workflow, board-discovery.yml')

    def test_a_schedule_run_is_skipped_when_the_worker_started_one_and_it_is_running_or_succeeded(self):
        running = {'id': 100, 'status': 'in_progress', 'conclusion': None}
        done = {'id': 101, 'status': 'completed', 'conclusion': 'success'}
        self.assertEqual(self.gate('schedule', [running])[0], 'true', 'the Worker\'s start is still running: the queued backup must not run it again')
        self.assertEqual(self.gate('schedule', [done])[0], 'true', 'the Worker\'s start finished: the late backup must not run it again')

    def test_a_backup_still_runs_when_nothing_good_started_lately(self):
        self.assertEqual(self.gate('schedule', [])[0], 'false', 'no run in the last 3 h: GitHub\'s run is the only start (the Worker had no token)')
        for conclusion in ('failure', 'cancelled', 'timed_out'):
            self.assertEqual(self.gate('schedule', [{'id': 100, 'status': 'completed', 'conclusion': conclusion}])[0], 'false', f'a {conclusion} start must still be retried')
        self.assertEqual(self.gate('schedule', [{'id': 900, 'status': 'in_progress', 'conclusion': None}])[0], 'false', 'the run itself is never counted')

    def test_a_hand_or_worker_start_is_never_skipped_and_an_unreadable_list_lets_the_run_go(self):
        self.assertEqual(self.gate('workflow_dispatch', [{'id': 100, 'status': 'in_progress', 'conclusion': None}])[0], 'false')
        skip, log = self.gate('schedule', [], fail=True)
        self.assertEqual(skip, 'false', 'a duplicate costs less than a missed slot')
        self.assertIn('::warning::', log)


if __name__ == '__main__':
    unittest.main()
