import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from i18n import translator,english_catalog
from model import Store
from test_ui import grid_payload


class LanguageTests(unittest.TestCase):
    def test_templates_preserve_user_content_and_format_values(self):
        en=translator('en')
        self.assertEqual(en('开始日期'),'Start date')
        self.assertEqual(translator('zh-CN')('开始日期'),'开始日期')
        self.assertEqual(en('结束日期 {0} 早于开始日期 {1}。','2026-01-01','2026-01-02'),
                         'End date 2026-01-01 is before start date 2026-01-02.')
        self.assertEqual(en('数据文件无法读取，请检查 C:/项目/{test}.json；原文件未被覆盖。'),
                         'Could not read C:/项目/{test}.json. The original file has not been overwritten.')
        self.assertEqual(en('My own **备注**'),'My own **备注**')

    def test_language_switch_preserves_data_filters_units_and_custom_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Store(Path(tmp)/'项目.json')
            tid=db.add('备注','User text 中文',priority='P1')
            db.set_labels(tid,['进行中'])
            db.add_update(tid,'**更新**')
            col=db.add_column('备注','text')
            db.set_value(tid,col,'Label 中文')
            original=db.all()
            with patch('model.Store',return_value=db):
                app=AppTest.from_file('app.py',default_timeout=30).run()
                app.selectbox[0].select('按月').run()
                app.button(key='language_action').click().run()
                self.assertFalse(app.exception)
                payload=grid_payload(app)
                self.assertEqual(payload['language'],'en')
                self.assertEqual(payload['timeUnit'],'按月')
                self.assertEqual(payload['stateFilter'],'全部状态')
                self.assertEqual(next(c['name'] for c in payload['allColumns'] if c['key']=='title'),'Item')
                self.assertEqual(next(c['rawName'] for c in payload['allColumns'] if c['key']==f'custom_{col}'),'备注')
                self.assertEqual(db.all(),original)
                self.assertEqual(db.preference('ui_language'),'en')
                self.assertEqual(Store(db.path).preference('ui_language'),'en')
                self.assertEqual(payload['translations']['更新看板'],'Updates board')
                app.button(key='language_action').click().run()
                self.assertEqual(grid_payload(app)['language'],'zh-CN')
                self.assertEqual(grid_payload(app)['timeUnit'],'按月')
                self.assertEqual(db.all(),original)

    def test_all_frontend_translation_keys_have_english_values(self):
        # Extract literal tr(...) arguments with a small scanner rather than
        # translating arbitrary DOM text (which might be user content).
        import re
        catalog=english_catalog()
        files=[Path('grid/index.html'),Path('grid/updates-board.js'),Path('grid/calendar.js'),
               Path('datepicker/index.html'),Path('datepicker/calendar.js'),Path('import_button/index.html')]
        for path in files:
            source=path.read_text(encoding='utf-8')
            for match in re.finditer(r'\btr\(("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')\)',source):
                import ast
                key=ast.literal_eval(match.group(1))
                if any('\u3400'<=char<='\u9fff' for char in key):
                    self.assertIn(key,catalog,path.name+': '+key)


if __name__=='__main__':
    unittest.main()
