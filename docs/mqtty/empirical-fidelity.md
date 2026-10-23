# Empirical BrokerFidelity profiles

Schema 4 (`broker-calibration-empirical-v1`) retains the complete validated v3
calibration plus the exact base64 histogram snapshot. Its provenance hashes the
snapshot and preserves the original observation campaign identity. A profile
set rejects mixing empirical and legacy model methods. Existing schema 2/3
readers and strict full/compact coverage remain supported.

The histogram input uses `schemaVersion: 1`, artifact type
`broker-publish-latency-histograms`, `observationsSha256`, `campaignId`, and
`points`. Only QoS 1/2 points have latency. Each point names broker, messageBytes,
qos, publishers and exactly three `repeats`: repeat number, raw archive SHA-256,
completedPublishes, actualMeasurementSeconds, and sorted `bins` pairs
`[upperBoundMicroseconds, count]`. The exporter must verify raw seals and
recompute counts/quantiles before producing this artifact. The reader verifies
coverage, repeats, archive identities, sample counts, mean RPS and all six mean
quantiles against the original observations (1 microsecond binning tolerance).
Hashes establish integrity of retained inputs, not independent authenticity.

```
calibrate --broker Mosquitto --coverage-protocol compact-v1 \
  --observations broker-observations.v3.json \
  --latency-histograms latency-histograms.json \
  --mqtty-repository <clean-source> --output broker-calibration.v4.json
```

Sampling uses cumulative observed counts and binary search, without spreading
the upper one percent uniformly between p99 and max. Rare maxima retain their
observed frequency; nothing is discarded to improve acceptance. Repeats are
pooled by completion count for the empirical CDF. This differs from averaging
their individual quantiles; both the repeat data and original reference remain
available. Unknown publisher-count points are rejected, including QoS 0.

The effective processing distribution still subtracts `QoS * MQTT echo RTT`
and clamps negative residuals at zero. This is an explicit legacy transport
assumption, not an identification of physical broker service time. Quantized
zero residuals add no mandatory processing tick. The clock origin is now the
publisher's Publish call, before the first network hop. Warmup publications
arriving after reset remain excluded based on that origin.

Set experiment `loadMode: closedLoopPerPublisher` to match the native campaign:
at most one outstanding acknowledged publish per publisher, with `offeredRps`
as an upper bound. `openLoopRate` remains the explicit/default legacy mode.
Completed RPS is measured, never forced to match the reference. A proposed
10-microsecond clock requires converting all physical duration ticks together;
it increases wall-clock cost and alone does not fix transport or profile errors.

Use a new revision/cohort for this model. Never replace old profiles/results in
place or describe reused, already inspected validation as a new blind control.
Acceptance limits remain unchanged. CPU/RSS are not modeled broker properties.

For single-broker BrokerFidelity, `transportTiming: calibratedRttBudget` resolves
the transport budget from the frozen profile's calibration RTT. It measures an
unloaded ideal graph's publisher-to-ACK duration at transfer=1, accounts for
the two/four QoS handshake legs, selects the nearest feasible transfer ticks,
and probes the actual resulting protocol floor. Processing delay then uses
the observed empirical CDF minus that measured model floor. The protocol is
actually delayed; reported latency is never substituted with a target value.
This avoids counting model scheduling overhead on top of a full RTT budget.

`transport-resolution.json` retains the requested/resolved transfer ticks,
ideal floor, calibration RTT and count of empirical observations below the
unavoidable protocol floor. Those observations remain in the source profile;
their effective processing delay is zero, so they cannot be reproduced below
that floor. MQTT echo RTT as a QoS1 ACK budget is an explicit assumption.
Neither validation observations nor acceptance thresholds resolve timing.
The input network object is not mutated, and repeated runs remain deterministic.
Relay keeps explicit transport settings and the documented residual assumption;
this option does not establish native Relay fidelity.
