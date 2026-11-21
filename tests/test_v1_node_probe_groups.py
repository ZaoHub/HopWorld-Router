"""Behavioral tests for the node-page subscription dropdown and 全部测试 payload."""

import json
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
V1_JS = ROOT / 'router' / 'static' / 'v1.js'
NODE = 'node'

HARNESS = r'''
const fs = require('fs');
const src = fs.readFileSync(process.env.V1_JS, 'utf8');

function sliceFn(name, nextName) {
  const startAt = (label) => {
    const a = src.indexOf('  function ' + label + '(');
    const b = src.indexOf('  async function ' + label + '(');
    if (a < 0) return b;
    if (b < 0) return a;
    return Math.min(a, b);
  };
  const start = startAt(name);
  const end = startAt(nextName);
  if (start < 0 || end < 0 || end <= start) throw new Error('cannot extract ' + name + ' start=' + start + ' end=' + end);
  return src.slice(start, end);
}

let overview = null;
const select = { value: 'all', dataset: {}, options: [] };
select.replaceChildren = function (...items) { this.options = items; };
const fields = {
  'v1-node-sub-options': select,
  'v1-node-query': {value: ''},
  'v1-node-protocol': {value: 'all'},
  'v1-node-source': {value: 'all'},
  'v1-node-connectivity': {value: 'all'},
  'v1-node-probe-mode': {value: 'light'},
};
let nodeFilter = 'all';
let nodePageIndex = 0;
const location = {hash: '#subscriptions'};
const requests = [];

function byId(id) {
  return fields[id] || null;
}
function element(tag, text) {
  return { tagName: tag, textContent: text, value: '' };
}
const probeTargetPayload = () => ({target: 'cloudflare'});
const note = () => {};
const refresh = async () => {};
const call = async (path, body) => requests.push({path, body});

eval(sliceFn('probeGroupItems', 'selectedProbeGroups'));
eval(sliceFn('selectedProbeGroups', 'syncProbeGroups'));
eval(sliceFn('syncProbeGroups', 'runNodeBatchProbe'));
eval(sliceFn('nodeProbePayload', 'syncNodeProbeMode'));
eval(sliceFn('runSourceAct', 'nodeKind'));
eval(sliceFn('nodeKind', 'protocolName'));
eval(sliceFn('nodeVisible', 'renderNodeDetail'));

function probeAllDecision() {
  syncProbeGroups();
  const groups = selectedProbeGroups();
  if (groups === null) {
    if (!(overview?.nodes || []).length) return { error: '暂无节点可测试' };
    return { body: { mode: 'full' } };
  }
  if (!groups.length) return { error: '暂无订阅组可测试' };
  return { body: { mode: 'full', groups } };
}

function labels() {
  return select.options.map(option => option.textContent);
}
function values() {
  return select.options.map(option => option.value);
}

function allBtnDisabled() {
  const selectedGroups = selectedProbeGroups();
  return selectedGroups === null ? !(overview?.nodes || []).length : !selectedGroups.length;
}

const cases = JSON.parse(process.env.CASES);
const results = cases.map((item) => {
  overview = item.overview;
  select.value = item.selected;
  select.dataset = {};
  select.options = [];
  syncProbeGroups();
  const decision = probeAllDecision();
  return {
    name: item.name,
    labels: labels(),
    values: values(),
    selectValue: select.value,
    selectedGroups: selectedProbeGroups(),
    decision,
    allBtnDisabled: allBtnDisabled(),
    itemIds: probeGroupItems().map(group => group.id),
    itemNames: probeGroupItems().map(group => group.name),
  };
});

async function checkSubscriptionActions() {
  overview = {
    sources: [{id: 1, name: '1'}, {id: 2, name: '2'}],
    nodes: [
      {id: 'only-1', groups: ['source:1']},
      {id: 'shared', groups: ['source:1', 'source:2']},
      {id: 'only-2', groups: ['source:2', 'manual'], manual: true},
      {id: 'manual', groups: ['manual'], manual: true},
    ],
  };
  syncProbeGroups();
  select.value = 'source:2';
  const displayed = () => overview.nodes.filter(nodeVisible).map(node => node.id);
  const filtered = displayed();

  select.value = 'source:1';
  fields['v1-node-protocol'].value = 'vless';
  nodePageIndex = 5;
  await runSourceAct('nodes', '2');
  const navigation = {hash: location.hash, selected: select.value, page: nodePageIndex, displayed: displayed()};

  await runSourceAct('probe', '2');
  fields['v1-node-probe-mode'].value = 'full';
  await runSourceAct('probe', '2');
  process.stdout.write(JSON.stringify({results, filtered, navigation, requests}));
}
checkSubscriptionActions().catch(error => { console.error(error); process.exitCode = 1; });
'''


CASES = [
    {
        'name': 'real_and_synthetic_coexist',
        'selected': 'all',
        'overview': {
            'sources': [{'id': 1, 'name': '1'}, {'id': 2, 'name': '2'}],
            'nodes': [
                {'id': 's1', 'manual': False, 'local': False},
                {'id': 'm1', 'manual': True, 'local': False},
                {'id': 'l1', 'manual': False, 'local': True},
            ],
            'local_node': {'name': '本地网络'},
        },
    },
    {
        'name': 'real_group_named_manual_import',
        'selected': 'source:9',
        'overview': {
            'sources': [{'id': 9, 'name': '手动导入'}, {'id': 2, 'name': '2'}],
            'nodes': [
                {'id': 's9', 'manual': False, 'local': False},
                {'id': 'm1', 'manual': True, 'local': False},
            ],
        },
    },
    {
        'name': 'no_sources_with_manual_nodes',
        'selected': 'all',
        'overview': {
            'sources': [],
            'nodes': [{'id': 'm1', 'manual': True, 'local': False}],
        },
    },
    {
        'name': 'no_sources_no_nodes',
        'selected': 'all',
        'overview': {'sources': [], 'nodes': []},
    },
    {
        'name': 'specific_real_group_only',
        'selected': 'source:1',
        'overview': {
            'sources': [{'id': 1, 'name': '1'}, {'id': 2, 'name': '2'}],
            'nodes': [{'id': 's1'}, {'id': 's2'}],
        },
    },
    {
        'name': 'stale_synthetic_selection',
        'selected': 'manual',
        'overview': {
            'sources': [{'id': 1, 'name': '1'}],
            'nodes': [{'id': 'm1', 'manual': True}],
        },
    },
]


def run_cases():
    env = {**os.environ, 'V1_JS': str(V1_JS), 'CASES': json.dumps(CASES, ensure_ascii=False)}
    with tempfile.TemporaryDirectory() as tmp:
        script = pathlib.Path(tmp) / 'harness.js'
        script.write_text(HARNESS, encoding='utf-8')
        proc = subprocess.run(
            [NODE, script], capture_output=True, text=True, env=env, timeout=20,
        )
        if proc.returncode != 0:
            raise AssertionError(f'node harness failed: {proc.stderr or proc.stdout}')
        return json.loads(proc.stdout)


class NodeProbeGroupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.actions = run_cases()
        cls.results = {item['name']: item for item in cls.actions['results']}

    def result(self, name):
        self.assertIn(name, self.results)
        return self.results[name]

    def test_subscription_filter_and_view_nodes_keep_shared_members(self):
        self.assertEqual(self.actions['filtered'], ['shared', 'only-2'])
        self.assertEqual(self.actions['navigation'], {
            'hash': '#nodes', 'selected': 'source:2', 'page': 0, 'displayed': ['shared', 'only-2'],
        })

    def test_subscription_probe_uses_the_configured_mode_and_target(self):
        self.assertEqual(self.actions['requests'], [
            {'path': '/api/batch/probe', 'body': {'mode': mode, 'target': 'cloudflare', 'groups': ['source:2']}}
            for mode in ('light', 'full')
        ])

    def test_dropdown_lists_only_real_subscription_groups(self):
        row = self.result('real_and_synthetic_coexist')
        self.assertEqual(row['labels'], ['订阅：全部', '订阅：1', '订阅：2'])
        self.assertEqual(row['values'], ['all', 'source:1', 'source:2'])
        self.assertNotIn('订阅：手动导入', row['labels'])
        self.assertNotIn('订阅：本地网络', row['labels'])
        self.assertNotIn('manual', row['values'])
        self.assertNotIn('local', row['itemIds'])
        self.assertIsNone(row['selectedGroups'])
        self.assertEqual(row['decision'], {'body': {'mode': 'full'}})
        self.assertNotIn('groups', row['decision']['body'])
        self.assertFalse(row['allBtnDisabled'])

    def test_real_group_with_same_name_as_manual_import_is_kept(self):
        row = self.result('real_group_named_manual_import')
        self.assertEqual(row['labels'], ['订阅：全部', '订阅：手动导入', '订阅：2'])
        self.assertEqual(row['values'], ['all', 'source:9', 'source:2'])
        self.assertIn('source:9', row['itemIds'])
        self.assertIn('手动导入', row['itemNames'])
        self.assertNotIn('manual', row['values'])
        self.assertEqual(row['selectedGroups'], ['source:9'])
        self.assertEqual(row['decision'], {'body': {'mode': 'full', 'groups': ['source:9']}})
        self.assertEqual(row['selectValue'], 'source:9')
        self.assertFalse(row['allBtnDisabled'])

    def test_all_tests_still_cover_manual_nodes_when_no_subscriptions(self):
        row = self.result('no_sources_with_manual_nodes')
        self.assertEqual(row['labels'], ['订阅：全部'])
        self.assertEqual(row['values'], ['all'])
        self.assertEqual(row['itemIds'], [])
        self.assertIsNone(row['selectedGroups'])
        self.assertEqual(row['decision'], {'body': {'mode': 'full'}})
        self.assertNotIn('groups', row['decision']['body'])
        self.assertNotEqual(row['decision'].get('error'), '暂无订阅组可测试')
        self.assertFalse(row['allBtnDisabled'])

    def test_all_tests_disabled_when_there_are_no_nodes(self):
        row = self.result('no_sources_no_nodes')
        self.assertEqual(row['labels'], ['订阅：全部'])
        self.assertEqual(row['decision'], {'error': '暂无节点可测试'})
        self.assertTrue(row['allBtnDisabled'])

    def test_specific_real_group_only_tests_that_group(self):
        row = self.result('specific_real_group_only')
        self.assertEqual(row['selectedGroups'], ['source:1'])
        self.assertEqual(row['decision']['body']['groups'], ['source:1'])
        self.assertNotIn('source:2', row['decision']['body']['groups'])
        self.assertFalse(row['allBtnDisabled'])

    def test_stale_synthetic_option_is_not_sent_to_probe(self):
        row = self.result('stale_synthetic_selection')
        self.assertEqual(row['labels'], ['订阅：全部', '订阅：1'])
        self.assertEqual(row['selectValue'], 'all')
        self.assertIsNone(row['selectedGroups'])
        self.assertEqual(row['decision'], {'body': {'mode': 'full'}})


if __name__ == '__main__':
    unittest.main()
