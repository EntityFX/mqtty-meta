"""Synthetic gate fixtures, never reported as experimental measurements."""
import unittest
from run_matched_twin_pilot import review_observations


class PilotGateTests(unittest.TestCase):
    def inputs(self):
        return (dict(timeOrigin='sharedMonotonic', measurementStartSeconds=1., measurementEndSeconds=3.,
                     drainEndSeconds=8., completed=True),
                [dict(client='subscriber', clientId='actual-id', state='connected', elapsedSeconds=.1)],
                [dict(client='subscriber', clientId='actual-id', topic='load', requestedQos=1, grantedQos=1, elapsedSeconds=.2),
                 dict(client='subscriber', clientId='actual-id', topic='load/warmup', requestedQos=1, grantedQos=1, elapsedSeconds=.3)])

    def test_correct_native_observation_is_admitted(self):
        self.assertTrue(review_observations(*self.inputs(), scope='native', window=2, drain=5)['admitted'])

    def test_disconnect_is_retained_as_system_outcome_and_blocks_scaling(self):
        phases, connections, subscriptions = self.inputs()
        connections.append(dict(connections[0], state='disconnected', elapsedSeconds=2.))
        result = review_observations(phases, connections, subscriptions, scope='native', window=2, drain=5)
        self.assertFalse(result['admitted'])
        self.assertEqual('interrupted', result['connection']['status'])
        self.assertEqual('systemObservation', result['classification'])

    def test_short_observation_is_rejected(self):
        phases, connections, subscriptions = self.inputs()
        phases['drainEndSeconds'] = 3.01
        self.assertFalse(review_observations(phases, connections, subscriptions, scope='native', window=2, drain=5)['admitted'])

    def test_subscription_cannot_belong_to_a_different_client(self):
        phases, connections, subscriptions = self.inputs()
        subscriptions[0]['clientId'] = 'someone-else'
        self.assertFalse(review_observations(phases, connections, subscriptions, scope='native', window=2, drain=5)['admitted'])

    def test_missing_model_tick_observation_is_rejected(self):
        phases, connections, subscriptions = self.inputs()
        self.assertFalse(review_observations(phases, connections, subscriptions, scope='model', window=2, drain=5)['admitted'])


if __name__ == '__main__':
    unittest.main()
