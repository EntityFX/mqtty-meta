import copy
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from prepare_article_figures import ROOT, build, checked, generate, interval, load_inputs, sha


class ArticleFigureTests(unittest.TestCase):
    def test_student_formula_known_example(self):
        result = interval([1., 2., 3.])
        self.assertEqual(result["mean"], 2.)
        self.assertEqual(result["sd"], 1.)
        self.assertAlmostEqual(result["ci95Low"], -.4841377117, places=9)
        self.assertAlmostEqual(result["ci95High"], 4.4841377117, places=9)

    def test_source_sha_detects_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"source.json"
            path.write_bytes(b"original")
            expected=sha(path)
            path.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError,"SHA-256 mismatch"):
                checked(path,expected)

    def test_frozen_coverage_and_qos0_not_zero(self):
        a,s,n,_=load_inputs(ROOT)
        result=build(a,s,n)
        self.assertEqual(sum(r["holdoutFailed"] for r in result["brokers"]),85)
        self.assertEqual(sum(r["sensitivityFailed"] for r in result["brokers"]),55)
        self.assertEqual({r["broker"] for r in result["brokers"]},{"Aedes","Mosquitto","ActiveMQ","EMQX"})
        self.assertEqual(len(result["nativeUncertainty"]),1344)
        self.assertEqual(len(result["conditionalSeedSpread"]),384)
        for r in result["nativeUncertainty"]:
            if r["qos"]==0 and r["metric"].endswith("Ms"):
                self.assertIsNone(r["mean"])
                self.assertEqual(r["n"],0)

    def test_missing_repeat_is_rejected(self):
        a,s,n,_=load_inputs(ROOT)
        n=copy.deepcopy(n)
        n["runs"][0]["key"]["repeat"]=2
        with self.assertRaisesRegex(ValueError,"repeat identities"):
            build(a,s,n)

    def test_corrupted_criterion_is_rejected(self):
        a,s,n,_=load_inputs(ROOT)
        a=copy.deepcopy(a)
        a["criteria"][0]["error"] = .91
        with self.assertRaisesRegex(ValueError,"error formula"):
            build(a,s,n)

    def test_package_is_byte_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            left,right=Path(tmp)/"one",Path(tmp)/"two"
            generate(ROOT,left);generate(ROOT,right)
            for name in ("criterion-error-map.svg", "native-rps-ci95.svg", "methodology.svg"):
                ET.parse(left/name)
            ns={"s":"http://www.w3.org/2000/svg"}
            self.assertEqual(len(ET.parse(left/"native-rps-ci95.svg").findall(".//s:circle",ns)),96)
            self.assertEqual({p.name:sha(p) for p in left.iterdir()},
                             {p.name:sha(p) for p in right.iterdir()})
            with self.assertRaisesRegex(ValueError,"already exists"):
                generate(ROOT,left)


if __name__=="__main__":unittest.main()
