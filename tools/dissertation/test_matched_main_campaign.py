import json
import tempfile
import unittest
from pathlib import Path
from run_matched_main_campaign import planned_jobs, statistics3, latency_bins, open_budget, completed_jobs, profile_inputs, seal, compare_results

class MainCampaignTests(unittest.TestCase):
    def test_matrix_has_40_unique_jobs_and_frozen_stage_order(self):
        jobs=planned_jobs()
        self.assertEqual(40,len(jobs));self.assertEqual(40,len({j['key'] for j in jobs}))
        self.assertEqual(['calibration']*12+['control']*12+['model']*12+['replay']*4,[j['stage'] for j in jobs])
        self.assertEqual([42,43,44], [j['seed'] for j in jobs if j['stage']=='model' and j['broker']=='Aedes'])
    def test_statistics_are_sample_deviation_and_three_repeat_t_interval(self):
        s=statistics3([1,2,3]);self.assertEqual(2,s['mean']);self.assertEqual(1,s['standardDeviation'])
        self.assertAlmostEqual(4.30265272975/3**.5,s['ci95HalfWidth'])
        with self.assertRaises(ValueError):statistics3([1,2])
    def test_histogram_uses_only_cohort_completed_before_horizon(self):
        rows=[dict(event='attempt',id='warm',elapsedSeconds=.5),dict(event='completed',id='warm',elapsedSeconds=.6,latencyMs=99),dict(event='attempt',id='x',elapsedSeconds=1),dict(event='completed',id='x',elapsedSeconds=2.1,latencyMs=.0016),dict(event='attempt',id='late',elapsedSeconds=2),dict(event='completed',id='late',elapsedSeconds=2.2,latencyMs=99)]
        self.assertEqual([[2,1]],latency_bins(rows,dict(measurementStartSeconds=1,measurementEndSeconds=2,drainEndSeconds=3)))
    def test_budget_keeps_original_deadline_and_rejects_changed_program(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);a=open_budget(root,'a'*64,100)
            self.assertEqual(a,open_budget(root,'a'*64,200))
            with self.assertRaises(ValueError):open_budget(root,'b'*64,200)
            with self.assertRaises(TimeoutError):open_budget(root,'a'*64,21700)
    def test_aggregation_reads_real_bins_and_actual_rtt_from_three_new_trials(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'program.json').write_text('{}');profiles=root/'profiles';profiles.mkdir();done={}
            for repeat in (1,2,3):
                case=root/str(repeat);raw=case/'raw';raw.mkdir(parents=True)
                phases=dict(measurementStartSeconds=1,measurementEndSeconds=5,drainEndSeconds=10)
                events=[dict(event='attempt',id='x',elapsedSeconds=1),dict(event='completed',id='x',elapsedSeconds=2,latencyMs=repeat)]
                (raw/'publishes.ndjson').write_text('\n'.join(json.dumps(row) for row in events)+'\n')
                (raw/'trial.json').write_text(json.dumps(dict(rttBaselineMs=.1*repeat)))
                metrics=dict(attemptedRps=repeat,completedCohortPerSubmissionSecond=repeat,
                             publishFailureRate=0,deliveryLossRate=0,completed=1,measurementSeconds=4)
                done[f'calibration.Aedes.r{repeat}']=dict(directory=str(case),files=seal(case),
                                                       review=dict(metrics=metrics,phases=phases))
            obs,hist=profile_inputs(dict(standSha256='a'*64,mqttBenchmarkCommit='b'*40),root,'Aedes',done,profiles)
            point=json.loads(obs.read_text())['points'][0]
            self.assertEqual(2,point['completedRps']['mean']);self.assertAlmostEqual(.2,point['rttBaselineMs'])
            self.assertEqual(2,point['observedLatencyQuantiles']['p99Ms'])
            hist=json.loads(hist.read_text())
            self.assertEqual([[[1000,1]],[[2000,1]],[[3000,1]]],[r['bins'] for r in hist['points'][0]['repeats']])
            self.assertEqual(3,len(list(profiles.glob('*.tar.gz'))))
    def test_comparison_uses_three_control_mean_not_arbitrary_seed_pairing(self):
        done={}
        for broker in ('Aedes','Mosquitto','ActiveMQ','EMQX'):
            for repeat in (1,2,3):
                for stage in ('calibration','control','model'):
                    metrics=dict(completedCohortPerSubmissionSecond=repeat if stage=='control' else 2,
                                 p99PublishMs=2,publishFailureRate=0,deliveryLossRate=0)
                    done[f'{stage}.{broker}.r{repeat}']=dict(review=dict(metrics=metrics))
        rows=compare_results(Path('.'),done)
        self.assertTrue(all(r['rpsNative']==2 and r['rpsError']==0 for r in rows))
        self.assertEqual(12,len(rows))
    def test_resume_never_accepts_unsealed_success_or_failed_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.assertEqual({},completed_jobs(root,'a'*64))
            case=root/'attempt-001/jobs/calibration.Aedes.r1';case.mkdir(parents=True)
            (case/'raw.txt').write_text('proof')
            receipt=dict(programSha256='a'*64,job=dict(key='calibration.Aedes.r1'),files=seal(case))
            (case/'success.json').write_text(json.dumps(receipt))
            self.assertEqual(1,len(completed_jobs(root,'a'*64)))
            (case/'raw.txt').write_text('changed')
            with self.assertRaises(ValueError):completed_jobs(root,'a'*64)

if __name__=='__main__':unittest.main()
