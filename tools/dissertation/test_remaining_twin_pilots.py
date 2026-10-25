"""Synthetic tests of pilot selection and fail-fast guards, not broker measurements."""
import copy
import tempfile
from pathlib import Path
import unittest
from run_remaining_twin_pilots import cases, web_request, check_web_validation, require_idle, verify_files, require_case_admission, open_budget, recover_owned_job


class RemainingPilotTests(unittest.TestCase):
    def test_only_six_predeclared_points_without_mosquitto_or_duplicates(self):
        selected=cases()
        self.assertEqual([(r['broker'],r['window']) for r in selected],
                         [(b,w) for b in ('Aedes','ActiveMQ','EMQX') for w in (2,4)])
        self.assertEqual(6,len({r['key'] for r in selected}))

    def test_web_model_parameters_do_not_use_native_rps(self):
        request=web_request('EMQX',4,180)
        self.assertEqual('emqx-default',request['brokerPresetId'])
        self.assertEqual((1,4,5,.00001),(request['warmupSeconds'],request['measurementSeconds'],request['drainSeconds'],request['tickSeconds']))
        self.assertEqual((16,1,42),(request['publishers'],request['qos'],request['seed']))
        self.assertNotIn('offeredRps',request)
        self.assertEqual(180,request['timeLimitSeconds'])

    def test_bad_profile_or_phase_or_validation_status_is_rejected(self):
        expected='a'*64
        valid={'valid':True,'profileSha256':expected,'warmupTicks':100000,'measurementTicks':400000,'drainTicks':500000}
        check_web_validation(valid,expected,4)
        for key,value in [('valid',False),('profileSha256','b'*64),('measurementTicks',200000),('drainTicks',100)]:
            row={**valid,key:value}
            with self.subTest(key=key),self.assertRaises(ValueError):check_web_validation(row,expected,4)

    def test_any_live_or_waiting_job_blocks_native_measurement(self):
        require_idle([{'state':'Completed'}])
        for state in ('Queued','Running','Paused','Cancelling'):
            with self.subTest(state=state),self.assertRaises(RuntimeError):require_idle([{'state':state,'id':'other'}])

    def test_hash_change_or_path_escape_blocks_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'input').write_bytes(b'abc')
            import hashlib
            expected=hashlib.sha256(b'abc').hexdigest()
            verify_files(root,{'input':expected})
            for files in ({'input':'0'*64},{'../outside':expected}):
                with self.assertRaises(ValueError):verify_files(root,files)

    def test_unsettled_or_disconnected_result_stops_expansion(self):
        result={'gate':{'admitted':True,'issues':[]},'metrics':{'attempted':10,'completed':8,'outcomesSettled':True}}
        require_case_admission(result)
        for path,value in [('admitted',False),('outcomesSettled',False),('attempted',0),('completed',0)]:
            row=copy.deepcopy(result)
            row['gate' if path=='admitted' else 'metrics'][path]=value
            with self.subTest(path=path),self.assertRaises(RuntimeError):require_case_admission(row)

    def test_restart_cannot_reset_budget_or_change_program(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            first=open_budget(root,'a'*64,1000)
            self.assertEqual(1900,first['deadlineEpoch'])
            self.assertEqual(first,open_budget(root,'a'*64,1050))
            with self.assertRaises(TimeoutError):open_budget(root,'a'*64,1900)
            with self.assertRaises(ValueError):open_budget(root,'b'*64,1050)

    def test_lost_post_response_recovers_only_new_exact_owned_name(self):
        jobs=[{'id':'old','name':'own','engine':'twinValidation'},
              {'id':'new','name':'own','engine':'twinValidation'},
              {'id':'other','name':'someone else','engine':'twinValidation'}]
        self.assertEqual('new',recover_owned_job(jobs,'own',{'old'}))
        self.assertIsNone(recover_owned_job(jobs,'absent',{'old'}))
        with self.assertRaises(RuntimeError):recover_owned_job(jobs,'own',set())


if __name__=='__main__':unittest.main()
