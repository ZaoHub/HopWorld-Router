"""Static checks for node-page subscription options and test-all controls."""

import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HTML = (ROOT / 'router' / 'static' / 'index.html').read_text(encoding='utf-8')
JS = (ROOT / 'router' / 'static' / 'v1.js').read_text(encoding='utf-8')
CSS = (ROOT / 'router' / 'static' / 'v1.css').read_text(encoding='utf-8')


class NodeProbeUiTests(unittest.TestCase):
    def test_node_page_has_subscription_select_and_menu_test_all(self):
        self.assertIn('<select id="v1-node-sub-options">', HTML)
        self.assertIn('for="v1-node-sub-options"', HTML)
        self.assertIn('订阅：全部', HTML)
        self.assertIn('>全部测试<', HTML)
        self.assertIn('data-node-batch="probe-all"', HTML)
        self.assertEqual(HTML.count('data-node-batch="probe-all"'), 1)
        self.assertIn('id="v1-node-batch-menu"', HTML)
        menu_start = HTML.index('id="v1-node-batch-menu"')
        menu_end = HTML.index('</div>', menu_start)
        self.assertIn('data-node-batch="probe-all"', HTML[menu_start:menu_end])
        self.assertNotIn('id="v1-node-probe-all"', HTML)
        self.assertNotIn('id="v1-node-sub-options-open"', HTML)
        self.assertNotIn('id="v1-node-sub-options-form"', HTML)
        self.assertNotIn('id="v1-node-sub-groups"', HTML)
        self.assertNotIn('aria-controls="v1-node-sub-options"', HTML)
        self.assertNotRegex(HTML, r'<dialog[^>]*id="v1-node-sub-options"')

    def test_test_all_uses_selected_subscription_group(self):
        self.assertIn('function selectedProbeGroups()', JS)
        self.assertIn("const groups = selectedProbeGroups();", JS)
        self.assertIn('{...nodeProbePayload(), groups}', JS)
        self.assertIn('{...nodeProbePayload()}', JS)
        self.assertIn('groups === null', JS)
        self.assertIn("'/api/batch/probe'", JS)
        self.assertIn("'/api/batch/probe/stop'", JS)
        self.assertIn('暂无订阅组可测试', JS)
        self.assertIn('暂无节点可测试', JS)
        self.assertIn('已开始测试所选订阅组的节点', JS)
        self.assertIn('停止请求已发送，正在结束当前任务', JS)
        self.assertIn('id: `source:${source.id}`', JS)
        self.assertNotIn("{id: 'manual', name: '手动导入'}", JS)
        self.assertNotIn("{id: 'local', name: overview?.local_node?.name || '本地网络'}", JS)
        self.assertNotIn("groups.push({id: 'manual'", JS)
        self.assertNotIn("groups.push({id: 'local'", JS)
        self.assertIn("'订阅：' + group.name", JS)
        self.assertIn("{id: 'all', name: '全部'}", JS)
        self.assertIn('return (overview?.sources || []).map(source => ({id: `source:${source.id}`, name: source.name}))', JS)
        self.assertNotIn('!selectedProbeGroups().length', JS)
        self.assertNotIn('订阅选项：', HTML)
        self.assertNotIn('订阅选项：', JS)
        self.assertRegex(JS, r"action === 'probe-all'")
        self.assertIsNotNone(re.search(r"function runNodeBatchProbe\(action\)", JS))
        self.assertIn('node_ids: picked.map(node => node.id)', JS)
        self.assertNotIn('selectedGroups', JS)
        self.assertNotIn("byId('v1-node-sub-options').showModal()", JS)
        self.assertNotIn('v1-sub-option-list', CSS)
        self.assertNotIn('请先在订阅选项中勾选至少一个订阅组', JS)

    def test_probe_progress_summary_stays_on_one_line(self):
        self.assertIn('id="v1-node-probe-progress"', HTML)
        self.assertIn('`上次 ${job.target} · 通过 ${job.done - job.failed}/${job.total}`', JS)
        match = re.search(r'#v1-node-probe-progress\s*\{([^}]+)\}', CSS)
        self.assertIsNotNone(match)
        rules = match.group(1)
        self.assertIn('white-space: nowrap', rules)
        self.assertIn('flex: none', rules)
        self.assertNotIn('overflow-wrap', rules)
        self.assertNotIn('min-width: 0', rules)
        self.assertNotIn('text-overflow: ellipsis', rules)
        board = HTML[HTML.index('class="card v1-nodes-board"'):HTML.index('class="card v1-subs-board"')]
        toolbar = board[board.index('v1-core-toolbar'):board.index('v1-node-tools')]
        tools = board[board.index('v1-node-tools'):]
        self.assertIn('class="v1-node-probe-status"', toolbar)
        self.assertIn('id="v1-node-probe-progress"', toolbar)
        self.assertNotIn('id="v1-node-probe-progress"', tools)
        self.assertIn('id="v1-node-probe-stop"', tools)
        self.assertNotIn('id="v1-node-probe-stop"', toolbar)
        self.assertEqual(HTML.count('id="v1-node-probe-progress"'), 1)
        self.assertEqual(HTML.count('id="v1-node-probe-stop"'), 1)
        self.assertIn('class="v1-node-filters"', HTML)
        self.assertIn('class="v1-node-actions"', HTML)
        self.assertNotIn('v1-node-probe-controls', HTML)
        self.assertNotIn('v1-node-probe-controls', CSS)
        tools_block = CSS[CSS.index('.v1-node-tools,'):CSS.index('#v1-node-probe-settings .v1-core-profile-body')]
        self.assertIn('flex-wrap: wrap', tools_block)
        self.assertNotIn('overflow-x: auto', tools_block)
        self.assertNotIn('::-webkit-scrollbar', tools_block)
        self.assertNotIn('flex-shrink: 0', tools_block)
        self.assertIn('.v1-nodes-board .v1-core-toolbar', CSS)
        self.assertIn('grid-template-columns: minmax(0, 1fr) auto', CSS)
        self.assertIn('.v1-node-probe-status', CSS)
        self.assertIn('function placeMenu(', JS)
        self.assertIn('id="v1-node-connectivity"', HTML)
        for label in ('连通性：全部', '可用', '不可用', '失败', '未测试'):
            self.assertIn(label, HTML)
        self.assertIn('#v1-node-connectivity { flex: 0 0 auto; min-width: auto; max-width: none; width: max-content; }', CSS)
        self.assertIn('min-width: 8rem', tools_block)
        self.assertIn('height: 36px', tools_block)
        self.assertIn('padding-block: 0', tools_block)
        self.assertIn('box-sizing: border-box', tools_block)
        self.assertIn('.v1-node-tools :is(input[type="search"], select) { width: auto; min-height: 36px; }', tools_block)
        self.assertNotIn('min-height: 32px', tools_block)
        self.assertNotIn('font-size: 13px', tools_block)
        self.assertIn('padding: 0 10px', tools_block)
        self.assertNotIn('padding: 0 6px', tools_block)
        self.assertIn('font: inherit', tools_block)
        self.assertIn('.v1-node-actions :is(button, .v1-menu > button) { flex: none; white-space: nowrap; }', tools_block)
        self.assertNotIn('min-height: 32px', CSS[CSS.index('.v1-node-actions :is(button'):CSS.index('.v1-node-actions :is(button')+200])


if __name__ == '__main__':
    unittest.main()
