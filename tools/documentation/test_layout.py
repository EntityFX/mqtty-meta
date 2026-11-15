import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from layout import organize_reports, refresh_report_registries, retarget


class LayoutTests(unittest.TestCase):
    def test_links_images_and_html_preserve_resolved_targets(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old = root / "docs/old.md"
            new = root / "docs/mqtty/new.md"
            target = root / "reports/one.md"
            moved = root / "reports/mqtty/one.md"
            text = '[result](../reports/one.md#part) ![plot](assets/a.svg) <a href="../reports/one.md">x</a>'
            result = retarget(text, old, new, {target.resolve(): moved.resolve()})
            self.assertIn('(../../reports/mqtty/one.md#part)', result)
            self.assertIn('(../assets/a.svg)', result)
            self.assertIn('href="../../reports/mqtty/one.md"', result)

    def test_anchors_external_links_and_embedded_images_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            text = '[inside](#local) [web](https://example.org/x) <img src="data:image/png;base64,abc">'
            self.assertEqual(text, retarget(text, root / 'a.md', root / 'sub/a.md', {}))

    def test_generated_report_goes_to_canonical_folder_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            reports = Path(folder) / 'reports'
            reports.mkdir()
            (reports / 'Aedes.md').write_text('[data](data/native-evidence.json)', encoding='utf-8')
            self.assertEqual(['mqtt-benchmark/brokers/Aedes.md'], organize_reports(reports))
            target = reports / 'mqtt-benchmark/brokers/Aedes.md'
            self.assertIn('(../../data/native-evidence.json)', target.read_text())
            self.assertEqual([], organize_reports(reports))

    def test_resealing_document_move_does_not_allow_scientific_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            reports = Path(folder)
            data = reports / 'data/study'
            data.mkdir(parents=True)
            scientific = data / 'runs.json'
            scientific.write_text('{}')
            expected = hashlib.sha256(scientific.read_bytes()).hexdigest()
            (data / 'publication-sha256.json').write_text(json.dumps({'data/study/runs.json': expected}))
            scientific.write_text('{"changed": true}')
            with self.assertRaisesRegex(ValueError, 'Scientific file changed'):
                refresh_report_registries(reports)


if __name__ == '__main__':
    unittest.main()
