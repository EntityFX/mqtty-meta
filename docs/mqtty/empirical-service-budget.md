# Empirical service budget for single-broker fidelity

`transportTiming: empiricalServiceBudget` is an opt-in revision of the empirical
single-broker experiment. It requires a schema-4 observed profile and
`loadMode: closedLoopPerPublisher`. Fixed transport and the previous
`calibratedRttBudget` remain available. Relay cannot use this mode.

An MQTT echo RTT contains application/receiver overhead and need not equal a
PUBACK round trip. Consequently it is an upper-bound proxy, not an identified
physical transport delay. For QoS1/2 the new budget is bounded by the smallest
observed calibration publish latency minus one refresh tick. An unloaded ideal
protocol probe identifies the representable minimum and transfer ticks are
rounded down to avoid exceeding the empirical bound when feasible. If the
tick/protocol minimum still exceeds observed latencies, samples remain in the
profile and `clippedEmpiricalSamples` reports this limitation. No validation
observations or target acceptance values select the transport.

The capacity constraint uses FIFO fractional-time reservations rather than
rejecting a short burst. Closed-loop publishers retain at most one pending
QoS1/2 publication, bounding the outstanding work in this experiment. The
capacity eligibility delay and empirical processing residual are combined as
lower bounds using their maximum: the observed end-to-end CDF already includes
native queueing. This is an explicit aggregate service-model assumption, not an
identified reconstruction of a broker's internal queues. Calibrated publication
failures, conditional delivery losses, network queue limits, and finite drain
remain active and visible. FIFO capacity pacing must not be interpreted as a
general unbounded-queue policy for Relay or an open-loop overload model.

QoS0 publishers do not await an ACK. They use the fractional offered-load token
accumulator without the one-publisher token cap or scan limit used for pending
QoS1/2 publications. The previous cap rounded a 30674-RPS stream down to 25000
RPS at a 10-microsecond tick with one publisher.

The QoS1/2 offered-load cap retains one arrival tick's fractional credit in
addition to the N-publisher backlog. Capping at N alone rounded even an
uncongested 9051-RPS stream down to 8333 RPS at a 10-microsecond tick. Pending-ACK
backpressure still bounds each publisher to one outstanding QoS1/2 publication.

The transport-resolution artifact identifies the new method, empirical lower
bound, RTT proxy, resolved protocol floor, clipping, and capacity pacing. Profile
hashes and acceptance thresholds do not change. Regression against calibration
points or already inspected control data is not new independent validation.
Deploy as a separate cohort; never relabel results from a frozen running series.
