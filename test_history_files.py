import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from model import Store


class HistoryFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'tasks.json'
        self.db = Store(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def history(self):
        return json.loads(self.db.history_path.read_bytes())

    def test_migrate_embedded_undo_redo_and_export_project_only(self):
        self.db.add('A')
        self.db.add('B')
        self.db.undo()
        state = json.loads(self.path.read_bytes())
        history = self.history()
        state['history'] = {key:history[key] for key in ('undo','redo')}
        self.path.write_text(json.dumps(state, ensure_ascii=False))
        self.db.history_path.unlink()
        migrated = Store(self.path)
        self.assertNotIn('history', json.loads(migrated.export_json()))
        self.assertEqual(migrated.history_status(), dict(can_undo=True, can_redo=True))
        migrated.redo()
        self.assertEqual([t['title'] for t in migrated.all()], ['A','B'])
        Store(self.path).undo()
        self.assertEqual([t['title'] for t in migrated.all()], ['A'])

    def test_size_cap_discards_oldest_undo_without_changing_data(self):
        self.db.history_limit_bytes = 5000
        self.db.add('任务')
        for index in range(8):
            self.db.set_preference('progress', dict(index=index, text='阶段总结'*40))
            self.assertLessEqual(self.db.history_path.stat().st_size, 5000)
        records = copy.deepcopy(self.history()['undo'])
        self.assertGreater(len(records), 0)
        self.assertLess(len(records), 8)
        index = 7
        for _ in records:
            self.assertTrue(self.db.undo())
            index -= 1
            self.assertEqual(self.db.preference('progress')['index'], index)
            self.assertLessEqual(self.db.history_path.stat().st_size, 5000)
        self.assertFalse(self.db.undo())
        for _ in records:
            self.assertTrue(self.db.redo())
        self.assertEqual(self.db.preference('progress')['index'], 7)

    def test_single_oversized_record_is_discarded_but_project_is_saved(self):
        self.db.history_limit_bytes = 1024
        self.db.add('非常长的正文'*1500)
        self.assertEqual(len(self.db.all()), 1)
        self.assertLessEqual(self.db.history_path.stat().st_size, 1024)
        self.assertFalse(self.db.history_status()['can_undo'])
        self.assertNotIn('history', json.loads(self.db.export_json()))

    def test_reopening_with_lower_limit_trims_existing_history(self):
        for index in range(7):
            self.db.set_preference('data', dict(index=index, text='x'*300))
        with patch.dict(os.environ, WORKY_HISTORY_MAX_BYTES='2048'):
            reopened = Store(self.path)
        self.assertLessEqual(reopened.history_path.stat().st_size, 2048)
        self.assertEqual(reopened.preference('data')['index'], 6)

    def test_failed_project_replacement_restores_both_files(self):
        self.db.add('原任务')
        project = self.path.read_bytes()
        history = self.db.history_path.read_bytes()
        replace = os.replace
        failed = False
        def fail_once(source, destination):
            nonlocal failed
            if Path(destination) == self.path and not failed:
                failed = True
                raise OSError('project replacement failed')
            return replace(source, destination)
        with patch('model.os.replace', side_effect=fail_once):
            with self.assertRaises(OSError):
                self.db.add('不能保存')
        self.assertEqual(self.path.read_bytes(), project)
        self.assertEqual(self.db.history_path.read_bytes(), history)
        self.assertFalse(list(self.path.parent.glob('*.tmp')))
        self.assertTrue(Store(self.path).undo())

    def test_pruning_old_redos_keeps_newer_redos_reversible(self):
        for index in range(6):
            self.db.set_preference('step', dict(index=index, text='x'*100))
        while self.db.undo():
            pass
        original = Store._snapshot(self.db._read())
        self.db.history_limit_bytes = 2500
        state = self.db._load()
        history = self.db._load_history(state)
        payload = self.db._history_payload(state, history)
        self.db._atomic_write(self.db.history_path, payload)
        self.assertGreater(len(history['redo']), 0)
        self.assertLess(len(history['redo']), 6)
        self.assertTrue(self.db.redo())
        self.assertTrue(self.db.undo())
        self.assertEqual(Store._snapshot(self.db._read()), original)
        while self.db.redo():
            pass
        self.assertEqual(self.db.preference('step')['index'], 5)

    def test_external_project_replacement_never_applies_stale_history(self):
        self.db.add('原任务')
        state = json.loads(self.path.read_bytes())
        state['items'][0]['title'] = '外部修改'
        self.path.write_text(json.dumps(state, ensure_ascii=False))
        self.assertFalse(self.db.undo())
        self.assertEqual(self.db.all()[0]['title'], '外部修改')
        self.db.add('新任务')
        self.assertTrue(self.db.undo())
        self.assertEqual([t['title'] for t in self.db.all()], ['外部修改'])

    def test_import_ignores_uploaded_history_and_keeps_local_undo(self):
        self.db.add('本地任务')
        other = Store(self.path.parent/'other.json')
        other.add('导入任务')
        state = json.loads(other.export_json())
        state['history'] = {'undo':[{'bad':'uploaded history'}], 'redo':[]}
        self.db.import_json(json.dumps(state))
        self.assertEqual(self.db.all()[0]['title'], '导入任务')
        self.db.undo()
        self.assertEqual(self.db.all()[0]['title'], '本地任务')


if __name__ == '__main__':
    unittest.main()
