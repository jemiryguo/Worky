import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from model import Store, today, flatten


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'tasks.json'
        self.db = Store(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def current(self):
        data = json.loads(self.path.read_text(encoding='utf-8'))
        return {key:data[key] for key in Store.STATE_KEYS}

    def test_json_is_only_store_and_updates_after_each_change(self):
        tid = self.db.add('**任务**', '中文备注', priority='P0')
        col = self.db.add_column('工时', 'number')
        self.db.set_value(tid,col,'2.5')
        data = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(data['items'][0]['notes'], '中文备注')
        self.assertEqual(data['values'][str(tid)][f'custom_{col}'], 2.5)
        self.assertEqual(data['items'][0]['priority'], 'P0')
        self.assertFalse(list(self.path.parent.glob('*.sqlite*')))
        self.assertFalse(list(self.path.parent.glob('*.db')))
        self.assertNotIn('history', data)
        self.assertTrue(json.loads(self.db.history_path.read_text(encoding='utf-8'))['undo'])

    def test_parent_completion_and_exact_moment_restore_as_one_step(self):
        root = self.db.add('父任务', priority='P1')
        child = self.db.add('子任务',parent_id=root,priority='P0')
        before = self.current()
        self.db.set_status(child,'已经完结')
        finished = self.current()
        self.assertEqual(finished['items'][0]['closed_at'],finished['items'][1]['closed_at'])
        self.assertTrue(self.db.undo())
        self.assertEqual(self.current(),before)
        self.assertTrue(self.db.redo())
        self.assertEqual(self.current(),finished)

    def test_history_survives_reopen_and_new_edit_discards_redo(self):
        self.db.add('A')
        before = self.current()
        self.db.add('B')
        self.db = Store(self.path)
        self.assertTrue(self.db.undo())
        self.assertEqual(self.current(),before)
        self.assertTrue(Store(self.path).history_status()['can_redo'])
        self.db.add('C')
        self.assertFalse(self.db.history_status()['can_redo'])
        self.assertFalse(self.db.redo())
        while self.db.undo():
            pass
        self.assertEqual(self.db.all(),[])
        self.assertFalse(self.db.undo())

    def test_deletion_restores_subtree_values_and_order(self):
        a = self.db.add('A')
        b = self.db.add('B',parent_id=a)
        c = self.db.add('C',parent_id=b)
        self.db.add('D')
        col = self.db.add_column('说明','text')
        self.db.set_value(c,col,'原始 Markdown **内容**')
        before = self.current()
        self.db.delete(b,expected_ids={b,c})
        deleted = self.current()
        self.db.undo()
        self.assertEqual(self.current(),before)
        self.db.redo()
        self.assertEqual(self.current(),deleted)

    def test_custom_column_deletion_restores_values_and_layout(self):
        task = self.db.add('A')
        col = self.db.add_column('说明','text')
        key = f'custom_{col}'
        self.db.set_value(task,col,'保留')
        self.db.set_preference('column_order',[key,'title'])
        self.db.set_preference('pinned_columns',[key])
        self.db.set_preference('column_widths',{key:300})
        before = self.current()
        self.db.delete_column(col,expected_name='说明')
        self.db.undo()
        self.assertEqual(self.current(),before)

    def test_compound_move_and_sort_reset_are_one_action(self):
        a,b = self.db.add('A'),self.db.add('B')
        self.db.set_preference('sort_state',dict(key='title',direction='desc'))
        before = self.current()
        with self.db.action('移动条目'):
            self.db.move(a,b,'inside')
            self.db.set_preference('sort_state',None)
        moved = self.current()
        self.db.undo()
        self.assertEqual(self.current(),before)
        self.db.redo()
        self.assertEqual(self.current(),moved)

    def test_invalid_noop_and_failed_write_leave_file_and_history_intact(self):
        tid = self.db.add('A')
        self.db.add('B')
        self.db.undo()
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            with self.db.action():
                self.db.set_priority(tid,'P0')
                self.db.move(tid,tid,'inside')
        self.assertEqual(self.path.read_bytes(),before)
        self.assertTrue(self.db.history_status()['can_redo'])
        with patch('model.os.replace', side_effect=OSError('test disk error')):
            with self.assertRaises(OSError):
                self.db.add('不能保存')
        self.assertEqual(self.path.read_bytes(),before)
        self.assertFalse(list(self.path.parent.glob('*.tmp')))
        before_count = len(json.loads(self.db.history_path.read_text(encoding='utf-8'))['undo'])
        self.db.set_priority(tid,'')
        self.assertEqual(len(json.loads(self.db.history_path.read_text(encoding='utf-8'))['undo']),before_count)

    def test_multiple_store_instances_do_not_lose_writes(self):
        def add_group(group):
            store = Store(self.path)
            for i in range(6):
                store.add(f'{group}/{i}')
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(add_group,range(3)))
        items = Store(self.path).all()
        self.assertEqual(len(items),18)
        self.assertEqual(len({t['id'] for t in items}),18)
        self.assertEqual(len(json.loads(self.db.history_path.read_text(encoding='utf-8'))['undo']),18)

    def test_full_export_and_import_are_one_undoable_operation(self):
        self.db.add('原项目')
        before=self.current()
        other=Store(self.path.parent/'other.json')
        root=other.add('导入父任务',priority='P1')
        child=other.add('导入子任务',parent_id=root)
        column=other.add_column('自定义','text')
        other.set_value(child,column,'**保留**')
        other.set_preference('column_widths',{'title':400})
        content=other.export_json()
        self.assertEqual(content,other.path.read_bytes())
        self.db.import_json(content)
        expected={key:json.loads(content)[key] for key in Store.STATE_KEYS}
        self.assertEqual(self.current(),expected)
        self.assertEqual(len(json.loads(self.db.history_path.read_text(encoding='utf-8'))['undo']),2)
        self.db.undo()
        self.assertEqual(self.current(),before)
        self.db.redo()
        self.assertEqual(self.current(),expected)

    def test_invalid_import_never_changes_file(self):
        self.db.add('保留数据')
        original=self.db.export_json()
        for content in (b'{broken',b'[]',b'{"items":[] ,"columns":"bad"}',b'{"items":[{"id":1}]}'):
            with self.assertRaises(ValueError):
                self.db.import_json(content)
            self.assertEqual(self.db.export_json(),original)
        data=json.loads(original)
        data['items'][0]['parent_id']=1
        with self.assertRaises(ValueError):
            self.db.import_json(json.dumps(data))
        self.assertEqual(self.db.export_json(),original)

    def test_labels_default_empty_reusable_and_undoable(self):
        root=self.db.add('父任务')
        child=self.db.add('子任务',parent_id=root)
        self.assertEqual([t['labels'] for t in self.db.all()],[[],[]])
        self.db.set_labels(root,['工作','紧急','工作'])
        self.assertEqual(self.db.all()[0]['labels'],['工作','紧急'])
        self.assertEqual(self.db.all()[1]['labels'],[])
        self.db.set_labels(child,['工作'])
        self.assertEqual(self.db.label_options(),['工作','紧急'])
        saved=self.current()
        self.db.set_labels(child,[])
        self.assertEqual(self.db.label_options(),['工作','紧急'])
        self.db.undo()
        self.assertEqual(self.current(),saved)
        self.db.redo()
        self.assertEqual(self.db.all()[1]['labels'],[])
        restored=Store(self.path)
        self.assertEqual(restored.label_options(),['工作','紧急'])
        other=Store(self.path.parent/'copy.json')
        other.import_json(restored.export_json())
        self.assertEqual(other.all()[0]['labels'],['工作','紧急'])
        self.assertEqual(other.label_options(),['工作','紧急'])
        before=self.db.export_json()
        for invalid in ('工作',[''],['a'*51],[1]):
            with self.assertRaises(ValueError):
                self.db.set_labels(root,invalid)
            self.assertEqual(self.db.export_json(),before)

    def test_label_upgrade_keeps_existing_history(self):
        self.db.add('已有任务')
        data=json.loads(self.path.read_text(encoding='utf-8'))
        data['history']=json.loads(self.db.history_path.read_text(encoding='utf-8'))
        data['history']={key:data['history'][key] for key in ('undo','redo')}
        snapshots=[data]
        for entry in data['history']['undo']:
            snapshots.extend([entry['before'],entry['after']])
        for snapshot in snapshots:
            for task in snapshot['items']:
                task.pop('labels',None)
        self.path.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
        store=Store(self.path)
        self.assertEqual(store.all()[0]['labels'],[])
        self.assertTrue(store.undo())
        self.assertEqual(store.all(),[])
        self.assertTrue(store.redo())
        self.assertEqual(store.all()[0]['labels'],[])

    def test_global_label_rename_delete_and_undo(self):
        a,b=self.db.add('A'),self.db.add('B')
        self.db.set_labels(a,['工作','紧急'])
        self.db.set_labels(b,['工作'])
        before=self.current()
        color=self.db.label_colors()['工作']
        self.assertNotEqual(color,self.db.label_colors()['紧急'])
        self.db.rename_label('工作','项目')
        self.assertEqual(self.db.label_colors()['项目'],color)
        self.assertEqual(Store(self.path).label_colors()['项目'],color)
        self.assertEqual(self.db.label_options(),['项目','紧急'])
        self.assertEqual(self.db.all()[0]['labels'],['项目','紧急'])
        self.assertEqual(self.db.all()[1]['labels'],['项目'])
        renamed=self.current()
        self.db.undo()
        self.assertEqual(self.current(),before)
        self.db.redo()
        self.assertEqual(self.current(),renamed)
        with self.assertRaises(ValueError):
            self.db.rename_label('项目','紧急')
        with self.assertRaises(ValueError):
            self.db.delete_label('项目',expected_ids={a})
        self.assertEqual(self.current(),renamed)
        self.db.delete_label('项目',expected_ids={a,b})
        self.assertEqual(self.db.label_options(),['紧急'])
        self.assertEqual(len(self.db.all()),2)
        self.assertEqual(self.db.all()[0]['labels'],['紧急'])
        self.assertEqual(self.db.all()[1]['labels'],[])
        deleted=self.current()
        self.db.undo()
        self.assertEqual(self.current(),renamed)
        self.db.redo()
        self.assertEqual(self.current(),deleted)
        self.db.set_labels(a,[])
        self.db.rename_label('紧急','待处理')
        self.assertEqual(self.db.label_options(),['待处理'])
        self.db.delete_label('待处理',expected_ids=set())
        self.assertEqual(self.db.label_options(),[])

    def test_expanded_colors_preserve_old_colors_and_balance_new_labels(self):
        task=self.db.add('颜色测试')
        labels=[f'标签{i}' for i in range(24)]
        self.db.set_labels(task,labels[:6])
        original=self.db.label_colors()
        self.db.set_labels(task,labels)
        colors=self.db.label_colors()
        self.assertEqual(len(set(colors.values())),24)
        self.assertEqual({name:colors[name] for name in original},original)
        self.assertEqual(Store(self.path).label_colors(),colors)
        self.db.rename_label(labels[-1],'新名称')
        self.assertEqual(self.db.label_colors()['新名称'],colors[labels[-1]])

    def test_label_registry_and_task_orders_are_independent_and_persisted(self):
        task=self.db.add('任务')
        self.db.set_labels(task,['A','B','C'])
        colors=self.db.label_colors()
        before=self.current()
        self.db.reorder_labels(['C','A','B'])
        reordered=self.current()
        self.assertEqual(self.db.label_options(),['C','A','B'])
        self.assertEqual(self.db.all()[0]['labels'],['A','B','C'])
        self.assertEqual(self.db.label_colors(),colors)
        self.db.undo()
        self.assertEqual(self.current(),before)
        self.db.redo()
        self.assertEqual(self.current(),reordered)
        self.db.set_labels(task,['B','C','A'])
        self.assertEqual(Store(self.path).all()[0]['labels'],['B','C','A'])
        self.assertEqual(Store(self.path).label_options(),['C','A','B'])
        with self.assertRaises(ValueError):
            self.db.reorder_labels(['A','B'])
        with self.assertRaises(ValueError):
            self.db.reorder_labels(['A','A','B'])

    def test_legacy_database_is_migrated_read_only_then_json_is_authoritative(self):
        import sqlite3
        legacy = self.path.parent / 'legacy.sqlite3'
        with sqlite3.connect(legacy) as con:
            con.execute('CREATE TABLE tasks (id INTEGER PRIMARY KEY,parent_id INTEGER,title TEXT,notes TEXT,status TEXT,start_date TEXT,closed_date TEXT,position INTEGER,created_at TEXT)')
            con.execute('INSERT INTO tasks VALUES (1,NULL,?,?,?,?,NULL,0,?)',('旧任务','备注','进行中',today().isoformat(),today().isoformat()+'T10:00:00+08:00'))
        con.close()
        before = legacy.read_bytes()
        store = Store(legacy)
        self.assertEqual(store.path.suffix,'.json')
        self.assertEqual(store.all()[0]['notes'],'备注')
        store.add('新任务')
        store.undo()
        self.assertEqual(legacy.read_bytes(),before)
        self.assertEqual(Store(store.path).all()[0]['title'],'旧任务')
        self.assertTrue(Store(store.path).history_status()['can_redo'])


if __name__ == '__main__':
    unittest.main()
