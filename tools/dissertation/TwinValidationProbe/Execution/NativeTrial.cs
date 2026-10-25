using EntityFX.MqttBenchmark.Campaign;
using TwinValidationProbe.Configuration;

namespace TwinValidationProbe.Execution;

public static class NativeTrial
{
    public static async Task<object> RunAsync(TwinTrialConfiguration config, string output)
    {
        var campaign = new CampaignDefinition { RecordPublicationCohort = true,
            WarmupSeconds = config.WarmupSeconds, MeasurementSeconds = config.MeasurementSeconds,
            DrainTimeoutSeconds = config.DrainSeconds, CooldownSeconds = 0, QuietPeriodSeconds = .01 };
        using var cancellation = new CancellationTokenSource(TimeSpan.FromSeconds(120));
        return await new MqttCampaignRunner(TimeSpan.FromSeconds(2)).RunAsync(campaign,
            new CampaignKey(config.Broker, config.PayloadBytes, config.Qos, config.Publishers, config.Repeat),
            new Uri(config.Endpoint), "article1-fresh-validation-v1", config.Id, output, 0,
            cancellation.Token);
    }
}
