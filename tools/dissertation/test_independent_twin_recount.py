"""Known-ID fixtures for a second implementation of article-1 arithmetic."""
import unittest
import json
import tempfile
from pathlib import Path
from independent_twin_recount import calculate, connection_and_subscription


class IndependentRecountTests(unittest.TestCase):
    def setUp(self):
        self.phases={'measurementStartSeconds':1,'measurementEndSeconds':3,'drainEndSeconds':5,'qos':1,'timeOrigin':'sharedMonotonic','completed':True}
        self.publications=[{'event':'attempt','id':'warm','elapsedSeconds':.5},
                           {'event':'completed','id':'warm','elapsedSeconds':.6,'latencyMs':100},
                           {'event':'attempt','id':'a','elapsedSeconds':1},
                           {'event':'completed','id':'a','elapsedSeconds':2,'latencyMs':1000},
                           {'event':'attempt','id':'b','elapsedSeconds':2.5},
                           {'event':'completed','id':'b','elapsedSeconds':4,'latencyMs':1500},
                           {'event':'attempt','id':'c','elapsedSeconds':2.6},
                           {'event':'failed','id':'c','elapsedSeconds':2.7},
                           {'event':'attempt','id':'d','elapsedSeconds':2.9},
                           {'event':'completed','id':'d','elapsedSeconds':6,'latencyMs':3100},
                           {'event':'attempt','id':'boundary','elapsedSeconds':3}]
        self.deliveries=[{'id':key,'elapsedSeconds':stamp} for key,stamp in [('warm',1.2),('a',2.1),('a',2.2),('b',4.5),('d',4.4),('b',5.1),('boundary',4.6)]]

    def test_warmup_cutoff_drain_pending_and_loss_denominator(self):
        r=calculate(self.publications,self.deliveries,self.phases)
        expected={'attempted':4,'completed':2,'failed':1,'pending':1,'completedInWindow':1,'completedDuringDrain':1,
                  'uniqueDeliveries':3,'completedIdsDelivered':2,'duplicates':1,'outsideCohortDeliveryEvents':3,
                  'deliveryLossRate':0,'publishFailureRate':.25,'p99PublishMs':1500,'firstDeliveriesDuringDrain':2}
        for key,value in expected.items():self.assertEqual(value,r[key],key)

    def test_delivery_of_pending_publication_cannot_hide_loss_of_completed(self):
        r=calculate(self.publications,[{'id':'d','elapsedSeconds':4}],self.phases)
        self.assertEqual(1,r['deliveryLossRate'])
        self.assertEqual(0,r['completedIdsDelivered'])

    def test_qos_zero_ack_is_not_applicable(self):
        r=calculate(self.publications,self.deliveries,{**self.phases,'qos':0})
        self.assertIsNone(r['p99PublishMs'])
        self.assertEqual('notApplicable',r['latencyStatus'])

    def test_duplicate_attempt_and_outcome_or_time_reversal_are_rejected(self):
        invalid=[self.publications+[self.publications[2]],self.publications+[self.publications[3]],
                 [{'event':'completed','id':'orphan','elapsedSeconds':1,'latencyMs':1}],
                 [{'event':'attempt','id':'x','elapsedSeconds':2},{'event':'failed','id':'x','elapsedSeconds':1}]]
        for rows in invalid:
            with self.assertRaises(ValueError):calculate(rows,[],self.phases)
        with self.assertRaises(ValueError):calculate(self.publications,[{'id':'a','elapsedSeconds':.9}],self.phases)

    def test_receiver_disconnect_or_missing_suback_blocks_connection_proof(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            connections=[{'client':'subscriber','clientId':'sub','state':'connected','elapsedSeconds':.1}]
            subs=[{'clientId':'sub','topic':topic,'requestedQos':1,'grantedQos':1,'elapsedSeconds':.2}
                  for topic in ('load','load/warmup')]
            def save(name,rows):(root/name).write_text('\n'.join(json.dumps(row) for row in rows),encoding='utf-8')
            save('connections.ndjson',connections);save('subscriptions.ndjson',subs)
            self.assertTrue(connection_and_subscription(root,self.phases,'native')['continuous'])
            save('connections.ndjson',connections+[{'client':'subscriber','clientId':'sub','state':'disconnected','elapsedSeconds':2},
                                                  {'client':'subscriber','clientId':'sub','state':'connected','elapsedSeconds':2.1}])
            with self.assertRaises(ValueError):connection_and_subscription(root,self.phases,'native')
            save('connections.ndjson',connections);save('subscriptions.ndjson',subs[:1])
            with self.assertRaises(ValueError):connection_and_subscription(root,self.phases,'native')


if __name__=='__main__':unittest.main()
