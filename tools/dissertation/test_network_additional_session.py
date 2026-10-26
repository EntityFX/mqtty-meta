import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import network_additional_session

from network_additional_session import admit, check_pending


class Guard:
    def save(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')


class AdditionalSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'wall-budget.json').write_bytes(b'{"deadlineEpoch":1}')
        (self.root/'old-attempt.json').write_bytes(b'old failure')
        digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
        self.policy=dict(sessionId='remaining-v1',executionManifestSha256='manifest',
                         sessionHelperSha256=digest(Path(network_additional_session.__file__)),
                         controllerSha256='controller',workers=2,wallLimitSeconds=21600,
                         originalBudgetSha256=digest(self.root/'wall-budget.json'),
                         originalReceipts={'old-attempt.json':digest(self.root/'old-attempt.json')},
                         allowedJobIds=['matrix.a','matrix.b','replay.a'],
                         retryReasons={'matrix.a':'Inspected executionBudgetExhausted',
                                       'matrix.b':'Inspected executionBudgetExhausted'},
                         reason='User authorized separate bounded continuation')
        self.path=self.root/'decision.json'

    def call(self, now=100):
        self.path.write_text(json.dumps(self.policy),encoding='utf-8')
        return admit(self.root,Guard(),self.path,'manifest','controller',2,now=now)

    def test_separate_deadline_preserves_original_and_is_not_reset(self):
        old=(self.root/'wall-budget.json').read_bytes()
        first,_=self.call();second,_=self.call(now=200)
        self.assertEqual(first,21700)
        self.assertEqual(second,first)
        self.assertEqual(old,(self.root/'wall-budget.json').read_bytes())
        with self.assertRaisesRegex(RuntimeError,'exhausted'):self.call(now=21701)

    def test_changed_policy_and_oversize_budget_rejected(self):
        self.call();self.policy['wallLimitSeconds']=20000
        with self.assertRaisesRegex(ValueError,'changed'):self.call()
        self.policy['wallLimitSeconds']=21601
        with self.assertRaisesRegex(ValueError,'six hours'):self.call()

    def test_controller_or_original_evidence_change_rejected(self):
        self.policy['controllerSha256']='other'
        with self.assertRaises(ValueError):self.call()
        self.policy['controllerSha256']='controller'
        (self.root/'old-attempt.json').write_bytes(b'changed')
        with self.assertRaises(ValueError):self.call()

    def test_multiple_inspected_retries_allowed_but_other_failure_rejected(self):
        jobs=[dict(id='matrix.a'),dict(id='matrix.b')]
        rows={x['id']:dict(id=x['id'],technicalError='executionBudgetExhausted') for x in jobs}
        check_pending(self.policy,jobs,rows,True)
        self.policy['retryReasons'].pop('matrix.b')
        with self.assertRaises(ValueError):check_pending(self.policy,jobs,rows,True)

    def test_unlisted_pending_work_or_implicit_resume_rejected(self):
        with self.assertRaises(ValueError):check_pending(self.policy,[dict(id='other')],{},True)
        with self.assertRaises(ValueError):check_pending(self.policy,[dict(id='matrix.a')],{},False)


if __name__=='__main__':unittest.main()
