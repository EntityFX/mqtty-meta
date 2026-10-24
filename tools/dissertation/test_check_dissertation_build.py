import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import subprocess

import check_dissertation_build as build


class DissertationBuildChecks(unittest.TestCase):
    def test_log_fails_on_unresolved_citation_reference_and_missing_glyph(self):
        issues = build.log_issues("LaTeX Warning: Citation 'key' on page 1 undefined.\n"
                                  "LaTeX Warning: Reference 'fig:x' on page 2 undefined.\n"
                                  "Missing character: There is no я in font xyz!\n")
        self.assertTrue(issues['undefinedCitation'])
        self.assertTrue(issues['undefinedReference'])
        self.assertTrue(issues['missingGlyph'])

    def test_commented_includeonly_not_active(self):
        text = '%\\includeonly{chapter1}\n\\include{chapter2} % note\n'
        self.assertNotIn('includeonly', build.uncomment(text))
        self.assertIn('chapter2', build.uncomment(text))

    def test_source_or_existing_output_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            for output in (source, source / 'build'):
                with self.assertRaises(ValueError):
                    build.run(source, output)

    def test_missing_tools_does_not_claim_compiled(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source'
            source.mkdir()
            main = source / 'dissertation.tex'
            main.write_text('\\include{Dissertation/chapter1}', encoding='utf-8')
            output = Path(directory) / 'evidence'
            audit = {'missingTopLevelFiles': [], 'includeOnlyActive': False}
            with patch.object(build, 'tracked_inputs', return_value=[main]), \
                    patch.object(build, 'source_audit', return_value=audit), \
                    patch.object(build.shutil, 'which', return_value=None), \
                    patch.object(build.subprocess, 'check_output', return_value=b'abc123'):
                result = build.run(source, output, build=True)
            self.assertEqual(result['status'], 'blockedMissingTools')
            self.assertFalse(result['step37Accepted'])
            self.assertTrue(result['sourceUnchanged'])
            self.assertFalse((output / 'snapshot').exists())
            self.assertTrue((output / 'verification.json').is_file())

    def test_old_pdf_never_becomes_new_build_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'Fresh main PDF missing'):
                build.validate_fresh_pdf(Path(directory), [], {})

    def test_timeout_terminates_process_tree_before_returning_error(self):
        process = Mock(pid=12345)
        process.wait.side_effect = [subprocess.TimeoutExpired(['fake-latexmk'], 1), -9]
        with patch.object(build.subprocess, 'Popen', return_value=process), \
                patch.object(build.subprocess, 'run') as stop_windows, \
                patch.object(build.os, 'killpg', create=True) as stop_posix:
            with self.assertRaises(subprocess.TimeoutExpired):
                build.bounded_build(['fake-latexmk'], Path('.'), {}, None, 1)
        if build.os.name == 'nt':
            self.assertEqual(stop_windows.call_args.args[0], ['taskkill', '/PID', '12345', '/T', '/F'])
        else:
            stop_posix.assert_called_once_with(12345, build.signal.SIGKILL)
        self.assertEqual(process.wait.call_count, 2)


if __name__ == '__main__':
    unittest.main()
