import json
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys

from audit_bibliography import audit, lexical_text, parse_bib


class BibliographyAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def run_audit(self, text, bib="@article{good,title={T},author={A},year=2020}"):
        self.write("main.tex", text + "\n\\addbibresource{refs.bib}")
        self.write("refs.bib", bib)
        return audit(self.root, "main.tex")

    def test_comments_escape_multiline_optional_and_verbatim(self):
        result = self.run_audit(r"""% \cite{ignored}
\% actual \cite[see][p.~2]{
good, absent}
\verb|\cite{ignored}| \begin{lstlisting}\ref{ignored}\end{lstlisting}
\label{known} \cref{known,missing}
""")
        self.assertEqual([r["key"] for r in result["citations"]], ["good", "absent"])
        self.assertEqual(result["missingCitations"][0]["line"], 2)
        self.assertEqual([r["key"] for r in result["missingReferences"]], ["missing"])
        self.assertTrue(result["protectedSources"]["unchanged"])

    def test_definitions_not_actual_citations_and_dynamic_keys_reported(self):
        result = self.run_audit(r"""\newcommand{\foo}[1]{\cite{#1}\ref{missing}}
\def\bar#1{\cite{good}}
\providecommand\baz[1][x]{\label{template}}
\cite{\variable} \cite{good}
""")
        self.assertEqual(result["counts"]["citationOccurrences"], 1)
        self.assertEqual(result["counts"]["labelDefinitions"], 0)
        self.assertEqual(result["missingReferences"], [])
        self.assertEqual(result["unresolvedLexicalConstructs"][0]["key"], r"\variable")

    def test_graph_root_parent_resolution_missing_and_cycle(self):
        self.write("main.tex", r"\input{sub/one}\input{absent}\input{../outside}")
        self.write("sub/one.tex", r"\input{two}\input{main}\cite{ghost}")
        self.write("sub/two.tex", r"\label{present}\ref{present}")
        result = audit(self.root, "main.tex")
        self.assertEqual(result["counts"]["texFiles"], 3)
        self.assertEqual(len(result["missingInputs"]), 2)
        self.assertEqual(len(result["includeCycles"]), 1)
        self.assertEqual(result["missingCitations"][0]["path"], "sub/one.tex")

    def test_conservative_branches_and_resources_deduplicated(self):
        self.write("main.tex", r"\iftrue\input{a}\else\input{b}\fi\input{a}")
        self.write("a.tex", r"\addbibresource{refs.bib}\label{a}")
        self.write("b.tex", r"\bibliography{refs}\label{b}")
        self.write("refs.bib", "@misc{x,title={X}}")
        result = audit(self.root, "main.tex")
        self.assertEqual(result["counts"]["bibFiles"], 1)
        self.assertEqual(result["counts"]["bibliographyEntries"], 1)
        self.assertEqual(len(result["duplicateLabels"]), 0)
        self.assertEqual(result["scope"]["conditionalBranches"], "both")

    def test_duplicates_missing_nocite_and_ranges(self):
        result = self.run_audit(r"\cite{good}\nocite{extra}\label{x}\label{x}\crefrange{x}{y}\hyperref[z]{caption}",
                                "@article{good,title={T}}\n@book{good,title={T}}\n@misc{extra,title={Extra}}\n@misc{unused,title={U}}")
        self.assertEqual(result["counts"]["duplicateKeys"], 1)
        self.assertEqual(result["counts"]["duplicateLabels"], 1)
        self.assertEqual(result["unusedKeys"], ["unused"])
        self.assertEqual([r["key"] for r in result["missingReferences"]], ["y", "z"])
        self.assertEqual(len(result["duplicateTitleCandidates"]), 1)

    def test_bib_nested_quoted_concatenated_and_inheritance(self):
        result = self.run_audit(r"\cite{good}", r'''@string{prefix="not an entry"}
@comment{This has {nested} braces}
@article{good,
 title = {Nested {MQTT} title}, author = "A and {B, C}",
 year = 2020, doi = "https://doi.org/" # {10.1000/example},
 crossref = {missing}}
''')
        entry = result["entries"][0]
        self.assertEqual(entry["fields"]["title"], "Nested {MQTT} title")
        self.assertEqual(entry["fields"]["doi"], "https://doi.org/10.1000/example")
        self.assertEqual(result["bibParseErrors"], [])
        self.assertEqual(result["missingInheritance"][0]["target"], "missing")

    def test_malformed_bib_and_duplicate_field(self):
        path = self.write("refs.bib", "@misc{x,title={X} title={Y}}\n@misc{y,title={A},title={B}}\n@misc{z,title={unterminated}")
        entries, errors = parse_bib(path, self.root)
        self.assertEqual(len(entries), 2)
        self.assertTrue(any(e["error"] == "Missing comma after field" for e in errors))
        self.assertTrue(any(e["error"] == "Duplicate field" for e in errors))
        self.assertTrue(any("Unclosed" in e["error"] for e in errors))

    def test_external_tex_inputs_reported_and_includeonly_not_executed(self):
        result = self.run_audit("\\input glyphtounicode.tex\n\\includeonly{chapter}\n\\cite{good}")
        self.assertEqual(result["missingInputs"][0]["argument"], "glyphtounicode.tex")
        self.assertEqual(result["unresolvedLexicalConstructs"][0]["command"], "includeonly")

    def test_deterministic_json_and_source_seals(self):
        self.run_audit(r"\cite{good}\label{known}\ref{known}")
        a = audit(self.root, "main.tex")
        b = audit(self.root, "main.tex")
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        self.assertEqual(len(a["protectedSources"]["sha256Before"]), 2)

    def test_unreachable_drafts_are_protected_but_not_audited_as_main(self):
        self.write("unused.tex", r"\cite{not_in_main}\label{duplicate}\label{duplicate}")
        result = self.run_audit(r"\cite{good}")
        self.assertEqual(result["counts"]["texFiles"], 1)
        self.assertEqual(result["missingCitations"], [])
        self.assertEqual(result["duplicateLabels"], {})
        self.assertIn("unused.tex", result["protectedSources"]["sha256Before"])
        self.assertNotIn("unused.tex", result["reachableSources"]["sha256Before"])

    def test_cli_refuses_to_overwrite_previous_evidence(self):
        self.run_audit(r"\cite{good}")
        output = self.root / "saved.json"
        args = [sys.executable, str(Path(__file__).with_name("audit_bibliography.py")),
                "--root", str(self.root), "--entrypoint", "main.tex", "--output", str(output)]
        one = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(one.returncode, 0, one.stderr)
        saved = output.read_bytes()
        two = subprocess.run(args, capture_output=True, text=True)
        self.assertNotEqual(two.returncode, 0)
        self.assertEqual(output.read_bytes(), saved)


if __name__ == "__main__":
    unittest.main()
