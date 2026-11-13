import errno
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from model import Store


class PlatformTests(unittest.TestCase):
    def test_native_processes_share_lock_and_preserve_unicode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'项目 文件.json'
            Store(path)
            code="from model import Store; import sys; s=Store(sys.argv[1]); [s.add(sys.argv[2]+str(i)) for i in range(3)]"
            workers=[subprocess.Popen([sys.executable,'-c',code,str(path),prefix],cwd=Path(__file__).parent,
                                      stdout=subprocess.PIPE,stderr=subprocess.PIPE) for prefix in ('中文','English')]
            for worker in workers:
                out,error=worker.communicate(timeout=30)
                self.assertEqual(worker.returncode,0,error.decode('utf-8',errors='replace'))
            reopened=Store(path)
            self.assertEqual(len(reopened.all()),6)
            self.assertEqual(len({t['id'] for t in reopened.all()}),6)
            self.assertTrue(reopened.undo())
            self.assertTrue(reopened.redo())
            self.assertIn('中文',path.read_text(encoding='utf-8'))

    @unittest.skipIf(os.name=='nt','Windows does not attempt directory fsync')
    def test_filesystem_without_directory_fsync_still_saves(self):
        with tempfile.TemporaryDirectory() as tmp:
            original=os.fsync
            def reject_directory(fd):
                if stat.S_ISDIR(os.fstat(fd).st_mode):
                    raise OSError(errno.EINVAL,'Directory sync is unsupported')
                original(fd)
            with patch('model.os.fsync',side_effect=reject_directory):
                db=Store(Path(tmp)/'tasks.json')
                db.add('Saved')
            self.assertEqual(Store(db.path).all()[0]['title'],'Saved')

    def test_portable_launcher_validates_port_before_start(self):
        result=subprocess.run([sys.executable,'run.py','--port','70000'],cwd=Path(__file__).parent,
                              capture_output=True,text=True)
        self.assertEqual(result.returncode,2)
        self.assertIn('between 1 and 65535',result.stderr)


if __name__=='__main__':
    unittest.main()
