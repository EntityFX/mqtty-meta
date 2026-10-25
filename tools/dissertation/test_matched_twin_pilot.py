"""Synthetic gate fixtures, never reported as experimental measurements."""
import unittest
from run_matched_twin_pilot import review_observations, validate_pilot_selection


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

    def model_inputs(self):
        phases, connections, _ = self.inputs()
        phases['connectionObservation'] = 'everyModelTick'
        subscriptions = [dict(client='receiver', clientId='actual-id', role='subscriber',
            topic=topic, state='subscribed', requestedQos=1, grantedQos=1, elapsedSeconds=.2)
            for topic in ('telemetry/+', 'local/telemetry/+')]
        return phases, connections, subscriptions

    def test_both_configured_model_filters_are_admitted(self):
        self.assertTrue(review_observations(*self.model_inputs(), scope='model', window=2, drain=5)['admitted'])

    def test_one_missing_configured_model_filter_is_rejected(self):
        phases, connections, subscriptions = self.model_inputs()
        self.assertFalse(review_observations(phases, connections, subscriptions[:1], scope='model', window=2, drain=5)['admitted'])

    def test_wrong_model_filter_cannot_replace_required_filter(self):
        phases, connections, subscriptions = self.model_inputs()
        subscriptions[1]['topic'] = 'unrelated/+'
        self.assertFalse(review_observations(phases, connections, subscriptions, scope='model', window=2, drain=5)['admitted'])

    def test_native_warmup_topic_must_belong_to_measured_topic(self):
        phases, connections, subscriptions = self.inputs()
        subscriptions[1]['topic'] = 'other/warmup'
        self.assertFalse(review_observations(phases, connections, subscriptions, scope='native', window=2, drain=5)['admitted'])

    def test_continuation_can_select_only_the_missing_window(self):
        self.assertEqual('pilot-attempt-002', validate_pilot_selection((4,), 2, 548))

    def test_duplicate_or_unplanned_windows_cannot_add_unplanned_repetitions(self):
        for windows in [(2, 2), (4, 2), (8,), ()]:
            with self.assertRaises(ValueError):
                validate_pilot_selection(windows, 2, 548)

    def test_continuation_path_and_budget_are_bounded(self):
        for attempt, budget in [('../old', 548), (0, 548), (2, 576), (2, 0)]:
            with self.assertRaises(ValueError):
                validate_pilot_selection((4,), attempt, budget)


if __name__ == '__main__':
    unittest.main()
