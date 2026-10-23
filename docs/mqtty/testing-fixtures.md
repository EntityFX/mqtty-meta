# CampaignAggregator roundoff fixture

`campaign-aggregator-roundoff-point.json` is the unmodified JSON serialization of
the Aedes/16-byte/QoS1/one-publisher ObservationPoint returned by the actual
MqttBenchmark `CampaignAggregator.Aggregate`, commit
`bdfd859f3ff8c2cfc4cfcc620c4e073e148f904f`, using `CampaignJson.Options` on .NET 6.
It is synthetic test input, not a real measurement or campaign result.

Generation expanded `new CampaignDefinition()` to all 288 keys. Each successful
attempt supplied `MeasurementSummary(100, 90, 10, 81, 0, 0, 0, 81, 1000, latency,
latencyStatus, errorReasons)`; all six latency knots were 0.1 ms for QoS1/2 and null
for QoS0; the sole error reason counted 10 failures. Start/end were 1000 seconds
apart and RTT was 0.01 ms. Thus each identical repeat contributes attempted RPS
0.1, completed RPS 0.09 and publish failure 0.1. Their computed means exceed the
repeat maxima by one representable step. Standard deviation and CI fields are
also retained exactly; the fixture is not rebuilt using MqttY's validation code.

The regression inserts this complete point into the otherwise synthetic full
matrix and invokes MqttY calibration and runtime profile loading. This keeps the
ordinary MqttY test build independent of an adjacent MqttBenchmark checkout while
testing genuine cross-project JSON, including its floating-point rounding.
