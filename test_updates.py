import copy
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from model import Store, TZ


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'tasks.json'
        self.store=Store(self.path)
        self.task=self.store.add('项目',priority='P1')

    def tearDown(self):
        self.tmp.cleanup()

    def entry(self, update_id):
        return next(e for e in self.store.all()[0]['updates'] if e['id']==update_id)

    def test_add_multiple_edit_delete_and_history_keep_timestamps(self):
        before=self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.add_update(self.task,'   ')
        self.assertEqual(self.path.read_bytes(),before)
        first=self.store.add_update(self.task,'**完成第一阶段**\n\n- 结果一')
        initial=copy.deepcopy(self.entry(first))
        self.assertEqual(datetime.fromisoformat(initial['created_at']).utcoffset(),datetime.now(TZ).utcoffset())
        second=self.store.add_update(self.task,'第二阶段')
        self.assertNotEqual(first,second)
        self.assertEqual(self.store.all()[0]['priority'],'P1')
        self.store.edit_update(self.task,first,'修订总结',expected_content=initial['content'])
        edited=copy.deepcopy(self.entry(first))
        self.assertEqual(edited['created_at'],initial['created_at'])
        self.assertIsNotNone(edited['edited_at'])
        before=self.path.read_bytes()
        self.store.edit_update(self.task,first,'修订总结')
        self.assertEqual(self.path.read_bytes(),before)
        with self.assertRaises(ValueError):
            self.store.delete_update(self.task,first,expected_content='过期内容')
        self.assertEqual(self.path.read_bytes(),before)
        self.store.delete_update(self.task,first,expected_content='修订总结')
        self.assertEqual(len(self.store.all()[0]['updates']),1)
        self.store.undo()
        self.assertEqual(self.entry(first),edited)
        self.store.undo()
        self.assertEqual(self.entry(first),initial)
        self.store.redo()
        self.assertEqual(self.entry(first),edited)
        self.store.redo()
        self.assertEqual(len(self.store.all()[0]['updates']),1)

    def test_editable_timestamps_timezone_history_and_conflict_checks(self):
        uid=self.store.add_update(self.task,'阶段总结',created_at='2026-01-02T12:34:56')
        original=copy.deepcopy(self.entry(uid))
        self.assertEqual(original['created_at'],'2026-01-02T12:34:56+08:00')
        self.store.edit_update(self.task,uid,'阶段总结',created_at='2025-12-20T09:10:11',
                               expected_content=original['content'],expected_created_at=original['created_at'])
        changed=copy.deepcopy(self.entry(uid))
        self.assertEqual(changed['created_at'],'2025-12-20T09:10:11+08:00')
        self.assertIsNotNone(changed['edited_at'])
        before=self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.edit_update(self.task,uid,'过期编辑',expected_created_at=original['created_at'])
        with self.assertRaises(ValueError):
            self.store.delete_update(self.task,uid,expected_created_at=original['created_at'])
        with self.assertRaises(ValueError):
            self.store.edit_update(self.task,uid,'无效时间',created_at='not-a-time')
        self.assertEqual(self.path.read_bytes(),before)
        self.store.undo()
        self.assertEqual(self.entry(uid),original)
        self.store.redo()
        self.assertEqual(self.entry(uid),changed)
        self.assertEqual(Store(self.path).all()[0]['updates'][0],changed)
        self.store.edit_update(self.task,uid,'阶段总结',created_at='2025-12-20T01:10:11Z')
        self.assertEqual(self.entry(uid),changed)

    def test_updates_roundtrip_and_subtree_delete_restore(self):
        child=self.store.add('子任务',parent_id=self.task)
        self.store.add_update(self.task,'父任务总结')
        self.store.add_update(child,'子任务总结')
        before=self.store.all()
        other=Store(self.path.parent/'copy.json')
        other.import_json(self.store.export_json())
        self.assertEqual(other.all(),before)
        self.store.delete(self.task)
        self.assertEqual(self.store.all(),[])
        self.store.undo()
        self.assertEqual(self.store.all(),before)
        self.assertEqual(Store(self.path).all(),before)

    def test_old_json_and_history_gain_empty_update_lists(self):
        data=json.loads(self.path.read_text())
        history=json.loads(self.store.history_path.read_text())
        data['history']={key:history[key] for key in ('undo','redo')}
        snapshots=[data]
        for entry in data['history']['undo']:
            snapshots.extend([entry['before'],entry['after']])
        for snapshot in snapshots:
            for item in snapshot['items']:
                item.pop('updates',None)
        self.path.write_text(json.dumps(data,ensure_ascii=False))
        reopened=Store(self.path)
        self.assertEqual(reopened.all()[0]['updates'],[])
        reopened.undo()
        self.assertEqual(reopened.all(),[])
        reopened.redo()
        self.assertEqual(reopened.all()[0]['updates'],[])

    def test_invalid_import_updates_leave_file_unchanged(self):
        uid=self.store.add_update(self.task,'保留内容')
        before=self.path.read_bytes()
        original=json.loads(before)
        for field,value in [('content',''),('created_at','bad'),('created_at','2026-11-12T10:00:00'),('edited_at','bad')]:
            data=copy.deepcopy(original)
            data['items'][0]['updates'][0][field]=value
            with self.assertRaises(ValueError):
                self.store.import_json(json.dumps(data))
            self.assertEqual(self.path.read_bytes(),before)
        data=copy.deepcopy(original)
        data['items'][0]['updates'].append(copy.deepcopy(data['items'][0]['updates'][0]))
        with self.assertRaises(ValueError):
            self.store.import_json(json.dumps(data))
        self.assertEqual(self.path.read_bytes(),before)


if __name__=='__main__':
    unittest.main()
