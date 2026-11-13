import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from model import Store


class ReadCacheTests(unittest.TestCase):
    def test_repeated_reads_reuse_parse_and_public_results_are_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Store(Path(tmp) / 'tasks.json')
            task_id = db.add('Original')
            db.set_preference('visible_columns', ['title'])
            with patch.object(db, '_load', wraps=db._load) as load:
                tasks = db.all()
                tasks[0]['title'] = 'Local change'
                prefs = db.preference('visible_columns')
                prefs.append('notes')
                for _ in range(5):
                    self.assertEqual(db.all()[0]['title'], 'Original')
                    self.assertEqual(db.preference('visible_columns'), ['title'])
                    db.history_status()
                    db.columns()
                    db.values()
                self.assertEqual(load.call_count, 1)
            self.assertEqual(db.all()[0]['id'], task_id)

    def test_other_writer_invalidates_cache_and_undo_reads_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'tasks.json'
            first = Store(path)
            task_id = first.add('Original')
            first.all()
            second = Store(path)
            second.set_labels(task_id, ['External'])
            self.assertEqual(first.all()[0]['labels'], ['External'])
            first.set_priority(task_id, 'P1')
            self.assertEqual(second.all()[0]['priority'], 'P1')
            self.assertEqual(second.all()[0]['labels'], ['External'])
            first.undo()
            self.assertEqual(second.all()[0]['priority'], '')
            self.assertEqual(second.all()[0]['labels'], ['External'])

    def test_failed_action_cannot_leak_mutated_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Store(Path(tmp) / 'tasks.json')
            db.add('Original')
            db.all()
            with self.assertRaises(ValueError):
                with db.action() as state:
                    state['items'][0]['title'] = 'Failed edit'
                    raise ValueError('Cancel')
            self.assertEqual(db.all()[0]['title'], 'Original')


if __name__ == '__main__':
    unittest.main()
