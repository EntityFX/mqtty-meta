"""Operational continuation of missing pilots; a reviewed drain failure stays failed."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True


def select_tail(plan, rows, review, manifest_sha, failed_receipt_sha,
                failed_summary_sha, failed_summary):
    """Only this complete, fixed pilot cohort may continue; never admit matrix."""
    if (review.get("decision") != "continue-unattempted-pilots-only" or
            review.get("executionManifestSha256") != manifest_sha or
            review.get("failedAttemptSha256") != failed_receipt_sha or
            review.get("failedSummarySha256") != failed_summary_sha or
            not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip() or
            not isinstance(review.get("reason"), str) or not review["reason"].strip()):
        raise ValueError("Review does not pin this failed attempt and program")
    pilots = [j for j in plan["jobs"] if j["kind"] == "pilot"]
    ids = {j["id"] for j in pilots}
    if len(pilots) != 30 or len(ids) != 30:
        raise ValueError("Expected the fixed 30-pilot cohort")
    failures = [r for r in rows.values() if r.get("technicalError")]
    if len(failures) != 1 or failures[0]["id"] != review.get("failedJobId"):
        raise ValueError("Exactly one reviewed existing technical failure is required")
    failure = failures[0]
    if failure["id"] not in ids or failure.get("kind") != "pilot" or failure.get("returnCode") != 2:
        raise ValueError("Reviewed failure is not the expected pilot CLI exit 2")
    phases = failed_summary.get("phases", [])
    if (failed_summary.get("status") != "failed" or
            not str(failed_summary.get("error", "")).startswith("System.TimeoutException: drain timed out") or
            sum(p.get("name") == "measure" and p.get("status") == "completed" for p in phases) != 1 or
            sum(p.get("name") == "drain" and p.get("status") == "failed" for p in phases) != 1 or
            not phases or phases[-1].get("name") != "drain" or
            any(p.get("status") != "completed" for p in phases[:-1])):
        raise ValueError("Only a completed measurement followed by failed drain may be reviewed")
    if any(k not in ids for k in rows):
        raise ValueError("Non-pilot attempts already exist; inspect the cohort")
    tail = [j for j in pilots if j["id"] not in rows]
    if len(rows) != 24 or len(tail) != 6:
        raise ValueError("Expected 23 completed pilots, one failed pilot and six unattempted")
    return tail


def summary(plan, rows, manifest_sha, review_sha, reason=None):
    pilots = [j for j in plan["jobs"] if j["kind"] == "pilot"]
    selected = [rows[j["id"]] for j in pilots if j["id"] in rows]
    return {"status": "partial", "executionManifestSha256": manifest_sha,
            "reviewSha256": review_sha, "coverage": len(selected), "expected": len(pilots),
            "success": sum(not r["technicalError"] for r in selected),
            "failure": sum(bool(r["technicalError"]) for r in selected),
            "pilotAccepted": False, "matrixAdmitted": False, "reason": reason,
            "rows": selected}


def load_frozen_runner(root):
    saved = root / "tools/dissertation/run_network_study.py"
    folder = str(saved.parent)
    for module in ("run_network_study", "network_study_inputs", "network_study_process",
                   "network_study_verification", "network_review", "study", "transit_study"):
        if module in sys.modules:
            raise RuntimeError("Run as a standalone process; preloaded runner modules are forbidden")
    sys.path.insert(0, folder)
    spec = importlib.util.spec_from_file_location("run_network_study", saved)
    runner = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runner
    spec.loader.exec_module(runner)
    return runner


def run(study, review_path, output):
    root, review_path, output = (Path(p).resolve() for p in (study, review_path, output))
    if output.exists():
        raise ValueError("Operation output already exists")
    if output == root or root.is_relative_to(output):
        raise ValueError("Operation output must not contain or equal the study")
    if any(output.is_relative_to(root / part) for part in ("program", "runtime", "tools", "runs", "rows")):
        raise ValueError("Operation output overlaps protected study paths")
    runner = load_frozen_runner(root)
    manifest = runner.verify_snapshot(root)
    manifest_sha = runner.sha(root / "execution-manifest.json")
    if runner.sha(Path(runner.__file__)) != manifest["files"]["tools/dissertation/run_network_study.py"]:
        raise ValueError("Loaded runner differs from frozen code")
    guard = runner.StorageGuard(manifest["mountRoot"], root, manifest["reserveBytes"])
    operation_guard = runner.StorageGuard(manifest["mountRoot"], output, manifest["reserveBytes"])
    if guard.device != manifest["mountDevice"] or operation_guard.device != guard.device:
        raise ValueError("Evidence filesystem differs from the frozen mount")
    plan = runner.program_check(root / "program")
    indexed = {j["id"]: j for j in plan["jobs"]}
    review_bytes = review_path.read_bytes()
    review = json.loads(review_bytes.decode("utf-8-sig"))
    review_sha = hashlib.sha256(review_bytes).hexdigest()
    with runner.lock(root / "run.lock"), runner.lock(guard.mount / ".mqtty-network-study.lock"):
        runner.isolation(manifest["afterUnits"])
        guard.check()
        rows = runner.load_rows(root, manifest_sha, indexed, guard)
        failed = rows.get(review.get("failedJobId"))
        if not failed:
            raise ValueError("Reviewed failed job is absent")
        receipt = runner.contained(root, failed["attempt"] + "/result.json")
        failed_output = runner.contained(root, failed["attempt"] + "/output")
        failed_summary, failed_warnings = runner.verify_output(
            failed_output, runner.contained(root / "program", indexed[failed["id"]]["config"]), root)
        tail = select_tail(plan, rows, review, manifest_sha, runner.sha(receipt),
                           runner.sha(failed_output / "summary.json"), failed_summary)
        if (root / "results-pilot.json").exists():
            raise ValueError("A successful pilot phase result already exists; inspect instead")
        protected = {str(p): runner.sha(p) for p in
                     list((root / "runs").glob("*/attempt-*/result.json")) + [failed_output / "summary.json"]}
        operation_guard.check()
        output.mkdir(parents=True, exist_ok=False)
        (output / "operation.py").write_bytes(Path(__file__).read_bytes())
        (output / "review.json").write_bytes(review_bytes)
        operation_guard.save(output / "operation.json", {
            "operationSha256": runner.sha(output / "operation.py"), "reviewSha256": review_sha,
            "executionManifestSha256": manifest_sha, "loadedRunnerSha256": runner.sha(Path(runner.__file__)),
            "selectedJobIds": [j["id"] for j in tail], "protectedReceiptSha256": protected,
            "retainedFailedJobId": failed["id"], "failedPartialIndependentlyRecounted": True,
            "failedPartialWarnings": failed_warnings, "pilotAccepted": False, "matrixAdmitted": False})
        context = runner.progress_context(root, plan)
        reason = "Reviewed drain failure retained; only previously unattempted pilots may run"
        try:
            for job in tail:
                runner.isolation(manifest["afterUnits"])
                guard.check()
                runner.verify_snapshot(root)
                if runner.sha(root / "execution-manifest.json") != manifest_sha:
                    raise ValueError("Execution manifest changed")
                row = runner.attempt(root, job, manifest, manifest_sha, guard,
                    lambda state: runner.publish(root, "pilot", rows, guard, "partial", manifest_sha,
                                                  context, {"id": job["id"], **state}, reason))
                rows[job["id"]] = row
                context["attempts"]["pilot"]["total"] += 1
                context["attempts"]["pilot"]["failed"] += bool(row["technicalError"])
                operation_guard.save(output / "tail-summary.json", summary(plan, rows, manifest_sha, review_sha))
                if row["technicalError"]:
                    raise RuntimeError("New technical failure; tail stopped: " + row["technicalError"])
                runner.verify_snapshot(root)
        except BaseException as error:
            reason = repr(error)
            raise
        finally:
            changed = [p for p, digest in protected.items() if runner.sha(Path(p)) != digest]
            if changed:
                reason = "Protected original receipts changed: " + repr(changed)
            result = summary(plan, rows, manifest_sha, review_sha, reason)
            operation_guard.save(output / "tail-summary.json", result)
            runner.publish(root, "pilot", rows, guard, "partial", manifest_sha, context, reason=reason)
            if changed:
                raise RuntimeError(reason)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.study, args.review, args.output)
    print(json.dumps({k: result[k] for k in ("status", "coverage", "success", "failure", "pilotAccepted", "matrixAdmitted")}))


if __name__ == "__main__":
    main()
