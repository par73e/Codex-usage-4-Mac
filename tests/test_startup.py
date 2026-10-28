import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend.app import find_dashboard_builder, build_dashboard

class StartupTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.cache=Path(self.temp.name)
    def builder(self,version):
        path=self.cache/version/'scripts/data-app.mjs';path.parent.mkdir(parents=True);path.write_text('// installed builder fixture');return path
    def test_plugin_update_replaces_removed_version(self):
        old=self.builder('1.0.11');new=self.builder('1.1.0-exp-baseline');old.unlink()
        self.assertEqual(find_dashboard_builder(self.cache),new)
    def test_version_selection_is_numeric_and_requires_a_builder(self):
        self.builder('1.9.0');new=self.builder('1.10.0');(self.cache/'2.0.0/scripts').mkdir(parents=True)
        self.assertEqual(find_dashboard_builder(self.cache),new)
    def test_missing_builder_does_not_overwrite_snapshot(self):
        with patch('backend.app.find_dashboard_builder',side_effect=RuntimeError('missing builder')),patch('backend.app.export_snapshot') as export:
            with self.assertRaisesRegex(RuntimeError,'missing builder'):build_dashboard(None)
            export.assert_not_called()
        with self.assertRaisesRegex(RuntimeError,'--skip-build'):find_dashboard_builder(self.cache/'missing')
