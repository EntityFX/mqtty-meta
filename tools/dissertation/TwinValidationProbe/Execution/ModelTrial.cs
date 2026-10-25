using System.Security.Cryptography;
using EntityFX.MqttY.Contracts.Mqtt;
using EntityFX.MqttY.Contracts.Options;
using EntityFX.MqttY.Plugin.Mqtt.BrokerProfile;
using EntityFX.MqttY.Plugin.Mqtt.Experiments;
using TwinValidationProbe.Configuration;

namespace TwinValidationProbe.Execution;

public static class ModelTrial
{
    public static object Run(TwinTrialConfiguration config, string output)
    {
        var sha = Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(config.ProfilePath))).ToLowerInvariant();
        if (sha != config.ProfileSha256) throw new InvalidDataException("Frozen profile hash mismatch.");
        var profiles = new BrokerProfileSet(new[] { config.ProfilePath });
        profiles.Get(config.Broker).For(config.PayloadBytes, (MqttQos)config.Qos, config.Publishers);
        long Ticks(double seconds)
        {
            var value = seconds / config.TickSeconds;
            if (Math.Abs(value - Math.Round(value)) > 1e-6) throw new InvalidDataException("Phase is not an exact tick multiple.");
            return checked((long)Math.Round(value));
        }
        var ticks = new TicksOptions { TickPeriod = TimeSpan.FromSeconds(config.TickSeconds),
            OutgoingWaitTicks = 1, ReceiveWaitPeriod = TimeSpan.FromSeconds(1), CounterHistoryDepth = 1 };
        var network = new NetworkOptions { NetworkType = "eth", TransferTicks = 1,
            CapacityBytesPerSecond = 100_000_000, ThroughputWindowTicks = 1000, QueueCapacity = 100_000 };
        var options = new MqttRelayExperimentOptions { RecordPublicationCohort = true,
            TopologyMode = ExperimentTopologyMode.BrokerFidelity, Brokers = 1, NetworkLength = 1,
            ClientsPerBroker = config.Publishers, ProfileClientCountMode = ProfileClientCountMode.Publishers,
            PublishQos = (MqttQos)config.Qos, SubscribeQos = (MqttQos)config.Qos, PayloadBytes = config.PayloadBytes,
            BrokerAssignmentMode = Enum.Parse<MqttBrokerAssignmentMode>(config.Broker),
            WarmupTicks = Ticks(config.WarmupSeconds), MeasurementTicks = Ticks(config.MeasurementSeconds),
            DrainTimeoutTicks = Ticks(config.DrainSeconds), OfferedRps = config.Publishers / config.TickSeconds,
            RandomSeed = config.Seed, LoadMode = PublishLoadMode.ClosedLoopPerPublisher,
            TransportTiming = TransportTimingMode.EmpiricalServiceBudget, EnableCounters = false,
            Parallel = false, RefreshStrategy = 0 };
        return new MqttRelayExperimentRunner(ticks, network, profiles).Run(options, output,
            new(config.RuntimeIdentity, config.RuntimeIdentity, sha) { BrokerProfiles = profiles.Artifacts });
    }
}
