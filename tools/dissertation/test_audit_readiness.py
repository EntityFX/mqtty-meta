import tempfile
import unittest
from pathlib import Path

from audit_readiness import inspect_tex, uncomment


class SourceAuditTests(unittest.TestCase):
    def test_comments_preserve_line_numbers_and_escaped_percent(self):
        text = "first% hidden\nvalue \\% visible\nend\\\\% hidden\n"
        self.assertEqual(uncomment(text), "first\nvalue \\% visible\nend\\\\\n")

    def test_reachable_sources_find_actual_citations_and_ambiguous_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "biblio").mkdir()
            (root / "biblio/refs.bib").write_text("@article{known, title={Title}}", encoding="utf-8")
            (root / "dissertation.tex").write_text(
                "\\input{chapter}\n\\label{same}\n\\ref{absent}\n"
                "\\def\\oldcite{}\n% \\cite{commented}\n", encoding="utf-8")
            (root / "chapter.tex").write_text(
                "\\label{same}\n\\cite{}\n\\parencite[see][2]{known,unknown}\n",
                encoding="utf-8")
            (root / "unused.tex").write_text("\\cite{unused}", encoding="utf-8")
            result = inspect_tex(root)
            self.assertEqual(result["reachableFiles"], ["chapter.tex", "dissertation.tex"])
            self.assertEqual(set(result["unresolvedCitationKeys"]), {"", "unknown"})
            self.assertEqual(result["unresolvedCitationKeys"][""], [{"file": "chapter.tex", "line": 2}])
            self.assertEqual(len(result["duplicateLabels"]["same"]), 2)
            self.assertEqual(set(result["unresolvedReferences"]), {"absent"})

    def test_missing_input_and_optional_logo_are_separate_findings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dissertation.tex").write_text(
                "\\input{missing}\n\\IfFileExists{logo.pdf}{\\includegraphics{logo.pdf}}{}\n",
                encoding="utf-8")
            result = inspect_tex(root)
            self.assertEqual(result["missingInputs"][0]["target"], "missing")
            self.assertFalse(result["images"][0]["resolved"])
            self.assertTrue(result["images"][0]["conditionalGuardNearby"])
            self.assertTrue(result["staticOnly"])


if __name__ == "__main__":
    unittest.main()
