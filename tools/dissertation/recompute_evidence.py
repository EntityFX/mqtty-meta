"""Recalculate published arithmetic into a separate audit file, without editing reports."""
import argparse
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    target = args.output.resolve()
    if target.is_relative_to((ROOT / "reports").resolve()):
        parser.error("Audit output must be outside published reports")
    spec = importlib.util.spec_from_file_location("evidence_assessor", ROOT / "tools/campaign/assess-evidence.py")
    assessor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(assessor)
    result = assessor.assess(ROOT / "reports", None)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"nativeRuns": result["native"]["runs"],
                      "primaryRawQuantilesRecomputed": result["native"]["primaryRawQuantilesRecomputed"]}))
