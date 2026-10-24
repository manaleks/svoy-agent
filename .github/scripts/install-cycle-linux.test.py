import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('native_fixture', Path(__file__).with_name('install-cycle-linux.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)

class ProofDirectoryTest(unittest.TestCase):
    def test_real_source_and_missing_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self.assertEqual(fixture.checked_directory(root), root)
            target = root / 'new/proof'
            self.assertEqual(fixture.checked_directory(target, create=True), target)
            self.assertTrue(target.is_dir())

    def test_parent_or_final_symlink_refused_before_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / 'real').mkdir()
            (root / 'link').symlink_to(root / 'real', target_is_directory=True)
            for path in [root / 'link', root / 'link/proof']:
                for create in [False, True]:
                    with self.assertRaises(RuntimeError):
                        fixture.checked_directory(path, create=create)
            self.assertFalse((root / 'real/proof').exists())

    def test_relative_path_refused(self):
        with self.assertRaises(RuntimeError):
            fixture.checked_directory(Path('relative/proof'), create=True)

if __name__ == '__main__':
    unittest.main()
