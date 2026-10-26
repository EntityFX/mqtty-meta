using EntityFX.MqttBenchmark.Campaign;
using System.Text.Json;
using TwinValidationProbe.Configuration;

namespace TwinValidationProbe.Execution;

public static class NativeTrial
{
    public static async Task<object> RunAsync(TwinTrialConfiguration config, string output)
    {
        var campaign = new CampaignDefinition { RecordPublicationCohort = true,
            WriteMessageEventLogs = config.WriteMessageEventLogs,
            ObserveFullDrainWindow = config.ObserveFullDrainWindow,
            WarmupSeconds = config.WarmupSeconds, MeasurementSeconds = config.MeasurementSeconds,
            DrainTimeoutSeconds = config.DrainSeconds, CooldownSeconds = 0, QuietPeriodSeconds = .01 };
        using var cancellation = new CancellationTokenSource(TimeSpan.FromSeconds(120));
        var runner = new MqttCampaignRunner(TimeSpan.FromSeconds(2), TimeSpan.FromMilliseconds(30));
        var rtt = 0d;
        if (config.MeasureRttBaseline)
        {
            var preflight = await runner.PreflightAsync(config.Broker, new Uri(config.Endpoint), 20, cancellation.Token);
            using (var file = new FileStream(Path.Combine(output, "protocol-preflight.json"), FileMode.CreateNew))
                JsonSerializer.Serialize(file, preflight, new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.CamelCase, WriteIndented = true });
            if (!preflight.Success || preflight.RttBaselineMs is not double measured || !double.IsFinite(measured) || measured <= 0)
                throw new InvalidDataException("The new calibration requires successful protocol probes and a measured positive RTT baseline.");
            rtt = measured;
        }
        return await runner.RunAsync(campaign,
            new CampaignKey(config.Broker, config.PayloadBytes, config.Qos, config.Publishers, config.Repeat),
            new Uri(config.Endpoint), config.CampaignId, config.Id, output, rtt,
            cancellation.Token);
    }
}
