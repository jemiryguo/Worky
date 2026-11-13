import tempfile
import json
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from model import Store, today


def grid_payload(app):
    for component in app.get('component_instance'):
        payload = json.loads(component.proto.json_args)
        if 'items' in payload and 'allColumns' in payload:
            return payload
    raise AssertionError('Task grid component was not rendered')


class UITests(unittest.TestCase):
    def test_grid_payload_preserves_types_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'tasks.json')
            task = store.add('**任务 A**', '完整备注 <script>坏代码</script>', start_date=today() - timedelta(days=5))
            store.add('说明文本', parent_id=task, kind='note')
            custom = store.add_column('工时', 'number')
            store.set_value(task, custom, '2.5')
            update_id=store.add_update(task,'**阶段总结** <script>bad()</script>')
            store.set_preference('visible_columns', ['title', 'status', f'custom_{custom}'])
            with patch('model.Store', return_value=store):
                app = AppTest.from_file('app.py', default_timeout=30).run()
                self.assertFalse(app.exception)
                self.assertEqual(len(app.sidebar.button), 0)
                payload = grid_payload(app)
                self.assertEqual(len(payload['items']), 2)
                self.assertEqual(payload['items'][1]['kind'], 'note')
                self.assertEqual(payload['items'][1]['parent_id'], task)
                self.assertEqual(payload['values'][str(task)][f'custom_{custom}'], 2.5)
                self.assertEqual([c['key'] for c in payload['columns']], ['title', 'priority', 'labels', 'updates', f'custom_{custom}'])
                self.assertNotIn('status', [c['key'] for c in payload['allColumns']])
                sortable = {col['key']: col['sortable'] for col in payload['allColumns']}
                self.assertFalse(sortable['notes'])
                self.assertFalse(sortable['labels'])
                self.assertFalse(sortable['title'])
                self.assertEqual(store.preference('column_sortable')['labels'], False)
                self.assertEqual(payload['stateFilter'], '全部状态')
                self.assertEqual(len(app.selectbox), 1)
                self.assertIn('<strong>任务 A</strong>', payload['rendered'][str(task)]['title'])
                self.assertNotIn('<script>', payload['rendered'][str(task)]['notes'])
                self.assertIn('&lt;script&gt;', payload['rendered'][str(task)]['notes'])
                self.assertIn('<strong>阶段总结</strong>', payload['renderedUpdates'][str(task)][update_id])
                self.assertNotIn('<script>', payload['renderedUpdates'][str(task)][update_id])
                self.assertFalse(sortable['updates'])
                self.assertEqual(payload['timeUnit'], '按天')
                app.selectbox[0].select('按年').run()
                self.assertFalse(app.exception)
                changed = grid_payload(app)
                self.assertEqual(changed['timeUnit'], '按年')
                app.button(key='undo_action').click().run()
                self.assertFalse(app.exception)
                self.assertTrue(store.history_status()['can_redo'])
                app.button(key='redo_action').click().run()
                self.assertFalse(app.exception)
                self.assertFalse(store.history_status()['can_redo'])

    def test_dark_theme_toggle_persists_and_reaches_grid(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'tasks.json')
            store.add('测试任务')
            with patch('model.Store',return_value=store):
                app=AppTest.from_file('app.py',default_timeout=30).run()
                payload=grid_payload(app)
                self.assertFalse(payload['darkMode'])
                app.button(key='theme_action').click().run()
                self.assertFalse(app.exception)
                self.assertEqual(store.preference('color_theme'),'dark')
                payload=grid_payload(app)
                self.assertTrue(payload['darkMode'])
                reopened=AppTest.from_file('app.py',default_timeout=30).run()
                self.assertFalse(reopened.exception)
                self.assertTrue(grid_payload(reopened)['darkMode'])
                reopened.button(key='theme_action').click().run()
                self.assertEqual(store.preference('color_theme'),'light')
                self.assertFalse(grid_payload(reopened)['darkMode'])

    def test_effective_priorities_inherit_and_allow_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'tasks.json')
            root = store.add('根任务', priority='P1')
            child = store.add('子任务', parent_id=root)
            leaf = store.add('独立覆盖', parent_id=child, priority='P0')
            note = store.add('说明容器', parent_id=root, kind='note')
            nested = store.add('说明里的子任务', parent_id=note)
            with patch('model.Store', return_value=store):
                app = AppTest.from_file('app.py', default_timeout=30).run()
                self.assertFalse(app.exception)
                payload = grid_payload(app)
                self.assertEqual(payload['effectivePriorities'][str(child)], 'P1')
                self.assertEqual(payload['effectivePriorities'][str(leaf)], 'P0')
                self.assertEqual(payload['effectivePriorities'][str(nested)], 'P1')
                self.assertEqual(next(t for t in payload['items'] if t['id'] == child)['priority'], '')


if __name__ == '__main__':
    unittest.main()
