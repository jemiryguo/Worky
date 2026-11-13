import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from model import Store, today, flatten


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Store(Path(self.tmp.name) / 'tasks.json')

    def tearDown(self):
        self.tmp.cleanup()

    def update(self, tid, **changes):
        task = next(t for t in self.db.all() if t['id'] == tid)
        kwargs = dict(title=task['title'], notes=task['notes'], parent_id=task['parent_id'],
                      status=task['status'], start_date=today())
        kwargs.update(changes)
        self.db.update(tid, **kwargs)

    def test_nested_move_prevents_cycle_and_keeps_notes(self):
        a = self.db.add('A')
        b = self.db.add('B', '子任务备注', a)
        c = self.db.add('C', parent_id=b)
        with self.assertRaises(ValueError):
            self.update(a, parent_id=c)
        self.update(b, parent_id=None)
        tasks = self.db.all()
        self.assertEqual(next(t for t in tasks if t['id'] == b)['notes'], '子任务备注')
        self.assertEqual(Store.descendants(tasks, b), {c})
        self.assertEqual([(t['id'], d) for t, d in flatten(tasks)], [(a, 0), (b, 0), (c, 1)])

    def test_status_dates_reopen_and_parent_independence(self):
        a = self.db.add('A')
        b = self.db.add('B', parent_id=a)
        self.update(a, status='已经完结')
        tasks = {t['id']: t for t in self.db.all()}
        self.assertEqual(tasks[a]['closed_date'], today().isoformat())
        self.assertEqual(tasks[b]['status'], '进行中')
        self.update(a, status='已经废弃')
        self.assertEqual(self.db.all()[0]['closed_date'], today().isoformat())
        self.update(a, status='进行中')
        self.assertIsNone(self.db.all()[0]['closed_date'])

    def test_dates_and_persistence(self):
        start = today() - timedelta(days=5)
        tid = self.db.add('older', start_date=start)
        self.update(tid, status='已经完结', start_date=start)
        self.update(tid, closed_date=today() - timedelta(days=2), start_date=start)
        self.update(tid, notes='保持结束日期', start_date=start)
        saved = Store(self.db.path).all()[0]
        self.assertEqual(saved['closed_date'], (today() - timedelta(days=2)).isoformat())
        with self.assertRaises(ValueError):
            self.update(tid, start_date=today(), closed_date=start)
        with self.assertRaises(ValueError):
            self.db.add('future', start_date=today() + timedelta(days=1))

    def test_reorder_exact_siblings(self):
        a, b = self.db.add('A'), self.db.add('B')
        child = self.db.add('child', parent_id=a)
        self.db.reorder(None, [b, a])
        self.assertEqual([t['id'] for t, _ in flatten(self.db.all())], [b, a, child])
        with self.assertRaises(ValueError):
            self.db.reorder(None, [a, child])
        with self.assertRaises(ValueError):
            self.db.reorder(None, [a, a])

    def test_drag_move_subtree_and_reorder(self):
        a = self.db.add('A')
        b = self.db.add('B')
        note = self.db.add('参考信息', kind='note', parent_id=a)
        child = self.db.add('C', parent_id=note)
        self.db.move(note, b, 'inside')
        tasks = {t['id']: t for t in self.db.all()}
        self.assertEqual(tasks[note]['parent_id'], b)
        self.assertEqual(tasks[child]['parent_id'], note)
        self.db.move(b, a, 'before')
        self.assertEqual([t['id'] for t, _ in flatten(self.db.all())], [b, note, child, a])
        with self.assertRaises(ValueError):
            self.db.move(b, child, 'inside')
        self.db.move(note, None, 'after')
        self.assertIsNone(next(t for t in self.db.all() if t['id'] == note)['parent_id'])

    def test_custom_columns_values_and_display_persist(self):
        task = self.db.add('A')
        note = self.db.add('补充文本', kind='note')
        number = self.db.add_column('工时', 'number')
        text = self.db.add_column('优先级', 'text')
        day = self.db.add_column('复查日期', 'date')
        flag = self.db.add_column('已复核', 'checkbox')
        self.db.set_value(task, number, '2.5')
        self.db.set_value(note, text, '高')
        self.db.set_value(task, day, today().isoformat())
        self.db.set_value(note, flag, True)
        self.db.set_preference('visible_columns', ['title', f'custom_{text}'])
        restored = Store(self.db.path)
        self.assertEqual(restored.values()[task][f'custom_{number}'], 2.5)
        self.assertEqual(restored.values()[note][f'custom_{text}'], '高')
        self.assertTrue(restored.values()[note][f'custom_{flag}'])
        self.assertEqual(restored.preference('visible_columns'), ['title', f'custom_{text}'])
        with self.assertRaises(ValueError):
            self.db.set_value(task, number, 'nan')
        with self.assertRaises(ValueError):
            self.db.add_column('工时', 'text')

    def test_completion_abandon_and_reopen(self):
        task = self.db.add('待办')
        note = self.db.add('文本', kind='note')
        self.db.set_status(task, '已经完结')
        saved = next(t for t in self.db.all() if t['id'] == task)
        self.assertEqual(saved['closed_date'], today().isoformat())
        self.db.set_status(task, '进行中')
        self.assertIsNone(self.db.all()[0]['closed_date'])
        self.db.set_status(task, '已经废弃')
        self.assertEqual(self.db.all()[0]['closed_date'], today().isoformat())
        self.db.set_status(task, '进行中')
        self.assertIsNone(self.db.all()[0]['closed_date'])
        with self.assertRaises(ValueError):
            self.db.set_status(note, '已经完结')

    def test_insert_before_at_root_and_child_boundaries(self):
        first = self.db.add('父任务')
        last = self.db.add('后一项')
        child = self.db.add('已有子任务', parent_id=first)
        new_child = self.db.add('父子间插入', parent_id=first, before_id=child)
        new_root = self.db.add('根级中间插入', before_id=last)
        self.assertEqual([t['id'] for t, _ in flatten(self.db.all())], [first, new_child, child, new_root, last])
        with self.assertRaises(ValueError):
            self.db.add('错误位置', before_id=child)
        self.assertEqual(len(self.db.all()), 5)

    def test_automatic_completion_rolls_up_and_reopens(self):
        root = self.db.add('总任务')
        parent = self.db.add('父任务', parent_id=root)
        first = self.db.add('子任务一', parent_id=parent)
        second = self.db.add('子任务二', parent_id=parent)
        note = self.db.add('纯文本容器', parent_id=parent, kind='note')
        nested = self.db.add('文本下的任务', parent_id=note)
        self.db.set_status(first, '已经完结')
        self.db.set_status(second, '已经废弃')
        self.assertEqual(next(t for t in self.db.all() if t['id'] == parent)['status'], '进行中')
        self.db.set_status(nested, '已经完结')
        snapshot = {t['id']: t for t in self.db.all()}
        for tid in (root, parent):
            self.assertEqual(snapshot[tid]['status'], '已经完结')
            self.assertEqual(snapshot[tid]['auto_closed'], 1)
            self.assertEqual(snapshot[tid]['closed_date'], today().isoformat())
        self.db.set_status(nested, '进行中')
        snapshot = {t['id']: t for t in self.db.all()}
        for tid in (root, parent):
            self.assertEqual(snapshot[tid]['status'], '进行中')
            self.assertIsNone(snapshot[tid]['closed_date'])
        self.db.set_status(nested, '已经完结')
        added = self.db.add('新子任务', parent_id=parent)
        self.assertEqual(next(t for t in self.db.all() if t['id'] == parent)['status'], '进行中')
        self.db.delete(added)
        self.assertEqual(next(t for t in self.db.all() if t['id'] == root)['status'], '已经完结')

    def test_pure_text_does_not_complete_task_and_abandonment_is_preserved(self):
        parent = self.db.add('父任务')
        self.db.add('只有说明', parent_id=parent, kind='note')
        self.assertEqual(self.db.all()[0]['status'], '进行中')
        child = self.db.add('实际子任务', parent_id=parent)
        self.db.set_status(parent, '已经废弃')
        self.db.set_status(child, '已经完结')
        self.assertEqual(next(t for t in self.db.all() if t['id'] == parent)['status'], '已经废弃')

    def test_parent_end_date_tracks_latest_child_and_propagates(self):
        start = today() - timedelta(days=12)
        root = self.db.add('顶层', start_date=start)
        parent = self.db.add('父任务', parent_id=root, start_date=start)
        a = self.db.add('完成子任务', parent_id=parent, start_date=start)
        b = self.db.add('废弃子任务', parent_id=parent, start_date=start)
        self.db.set_status(a, '已经完结')
        self.db.set_status(b, '已经废弃')
        def end(tid, state, days_ago):
            self.db.update(tid, '子任务', '', parent, state, start, today() - timedelta(days=days_ago))
        end(a, '已经完结', 5)
        end(b, '已经废弃', 3)
        snapshot = {t['id']: t for t in self.db.all()}
        self.assertEqual(snapshot[parent]['closed_date'], (today()-timedelta(days=3)).isoformat())
        self.assertEqual(snapshot[root]['closed_date'], snapshot[parent]['closed_date'])
        end(b, '已经废弃', 7)
        snapshot = {t['id']: t for t in self.db.all()}
        self.assertEqual(snapshot[parent]['closed_date'], (today()-timedelta(days=5)).isoformat())
        self.assertEqual(snapshot[root]['closed_date'], snapshot[parent]['closed_date'])
        end(a, '已经完结', 1)
        snapshot = {t['id']: t for t in self.db.all()}
        self.assertEqual(snapshot[root]['closed_date'], (today()-timedelta(days=1)).isoformat())

    def test_task_priority_defaults_and_persists(self):
        task = self.db.add('任务')
        self.assertEqual(self.db.all()[0]['priority'], '')
        for priority in ('P0', 'P1', 'P2', 'P3', ''):
            self.db.set_priority(task, priority)
            self.assertEqual(Store(self.db.path).all()[0]['priority'], priority)
        created = self.db.add('创建时选择优先级', priority='P1')
        self.assertEqual(next(t for t in self.db.all() if t['id'] == created)['priority'], 'P1')
        with self.assertRaises(ValueError):
            self.db.set_priority(task, 'P9')
        note = self.db.add('纯文本', kind='note')
        with self.assertRaises(ValueError):
            self.db.set_priority(note, 'P0')

    def test_completion_clears_priority_and_records_exact_time(self):
        parent = self.db.add('父任务', priority='P1')
        child = self.db.add('子任务', parent_id=parent, priority='P0')
        self.db.set_status(child, '已经完结')
        snapshot = {t['id']: t for t in self.db.all()}
        for tid in (parent, child):
            self.assertEqual(snapshot[tid]['priority'], '')
            self.assertIsNotNone(snapshot[tid]['closed_at'])
        self.assertEqual(snapshot[parent]['closed_at'], snapshot[child]['closed_at'])
        self.db.set_status(child, '进行中')
        snapshot = {t['id']: t for t in self.db.all()}
        for tid in (parent, child):
            self.assertEqual(snapshot[tid]['priority'], '')
            self.assertIsNone(snapshot[tid]['closed_at'])
        self.assertIsNotNone(snapshot[child]['start_at'])
        self.db.set_priority(child, 'P2')
        self.db.set_status(child, '已经废弃')
        self.assertEqual(next(t for t in self.db.all() if t['id']==child)['priority'], '')
        with self.assertRaises(ValueError):
            self.db.set_priority(child, 'P0')

    def test_manual_move_preserves_sorted_peers_and_hierarchy(self):
        d,c,b,a = [self.db.add(name) for name in ['D','C','B','A']]
        nested = self.db.add('C 的说明', parent_id=c, kind='note')
        self.db.move(d,b,'after',view_order=[a,b,c,nested,d])
        self.assertEqual([t['id'] for t, _ in flatten(self.db.all())], [a,b,d,c,nested])
        with self.assertRaises(ValueError):
            self.db.move(a,c,'after',view_order=[a,b,c,d])
        self.assertEqual([t['id'] for t, _ in flatten(self.db.all())], [a,b,d,c,nested])

    def test_rename_and_delete_custom_column_preserves_other_data(self):
        task = self.db.add('任务')
        first = self.db.add_column('原列','text')
        other = self.db.add_column('其他列','text')
        self.db.set_value(task,first,'保留到确认删除')
        self.db.set_value(task,other,'其他列保留')
        self.db.rename_column(first,'新列名')
        self.assertEqual(self.db.columns()[0]['name'],'新列名')
        self.assertEqual(self.db.values()[task][f'custom_{first}'],'保留到确认删除')
        key = f'custom_{first}'
        self.db.set_preference('visible_columns',['title',key])
        self.db.set_preference('column_order',['title',key])
        self.db.set_preference('column_widths',{key:300,'title':250})
        self.db.set_preference('sort_state',{'key':key,'direction':'asc'})
        with self.assertRaises(ValueError):
            self.db.delete_column(first,expected_name='旧的错误名称')
        self.db.delete_column(first,expected_name='新列名')
        self.assertEqual(self.db.preference('visible_columns'),['title'])
        self.assertEqual(self.db.preference('column_widths'),{'title':250})
        self.assertIsNone(self.db.preference('sort_state'))
        self.assertNotIn(key,self.db.values()[task])
        self.assertEqual(self.db.values()[task][f'custom_{other}'],'其他列保留')

    def test_infers_task_or_note_and_can_convert_without_data_loss(self):
        note = self.db.add('', '**只有备注**')
        item = next(t for t in self.db.all() if t['id']==note)
        self.assertEqual(item['kind'],'note')
        self.assertEqual(item['title'],'')
        self.assertEqual(item['notes'],'**只有备注**')
        self.db.update(note,'现在是任务',item['notes'],None,'进行中',today())
        self.db.set_priority(note,'P0')
        self.db.set_status(note,'已经完结')
        self.db.update(note,'','保留备注',None,'已经完结',today(),today())
        item = self.db.all()[0]
        self.assertEqual(item['kind'],'note')
        self.assertEqual(item['notes'],'保留备注')
        self.assertEqual(item['priority'],'')
        self.assertIsNone(item['closed_date'])
        with self.assertRaises(ValueError):
            self.db.add('','  ')

    def test_legacy_note_title_migrates_into_notes(self):
        item_id = self.db.add('占位')
        import json
        state = json.loads(self.db.path.read_text())
        state['format_version'] = 1
        state['items'][0].update(kind='note',title='旧文本',notes='原备注')
        self.db.path.write_text(json.dumps(state,ensure_ascii=False))
        item = Store(self.db.path).all()[0]
        self.assertEqual(item['id'],item_id)
        self.assertEqual(item['title'],'')
        self.assertEqual(item['notes'],'旧文本\n\n原备注')

    def test_confirmed_delete_removes_subtree_and_its_values(self):
        root = self.db.add('根任务')
        first = self.db.add('前一项', parent_id=root)
        target = self.db.add('要删除的项', parent_id=root)
        child = self.db.add('保留子任务', parent_id=target)
        grandchild = self.db.add('保留说明', parent_id=child, kind='note')
        after = self.db.add('后一项', parent_id=root)
        col = self.db.add_column('备注列', 'text')
        self.db.set_value(target, col, '移除')
        self.db.set_value(child, col, '保留')
        self.db.set_value(first, col, '保留相邻条目')
        with self.assertRaises(ValueError):
            self.db.delete(target, expected_ids={target})
        self.assertEqual(len(self.db.all()), 6)
        self.db.delete(target, expected_ids={target, child, grandchild})
        items = self.db.all()
        self.assertEqual([t['id'] for t, _ in flatten(items)], [root, first, after])
        self.assertNotIn(target, self.db.values())
        self.assertNotIn(child, self.db.values())
        self.assertEqual(self.db.values()[first][f'custom_{col}'], '保留相邻条目')
        self.db.delete(root)
        self.assertEqual(self.db.all(), [])
        self.assertEqual(self.db.values(), {})


if __name__ == '__main__':
    unittest.main()
