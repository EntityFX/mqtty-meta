using System.Text.Json;
using TwinValidationProbe.Configuration;
using TwinValidationProbe.Execution;

var json = new JsonSerializerOptions { PropertyNameCaseInsensitive = true, PropertyNamingPolicy = JsonNamingPolicy.CamelCase, WriteIndented = true };
if (args.Length != 3 || args[0] is not ("native" or "model"))
    throw new ArgumentException("Usage: TwinValidationProbe native|model <config.json> <NEW-output-directory>");
var config = JsonSerializer.Deserialize<TwinTrialConfiguration>(File.ReadAllBytes(args[1]), json)!;
config.Validate();
var output = Path.GetFullPath(args[2]);
if (Directory.Exists(output)) throw new IOException("Trial directory already exists.");
Directory.CreateDirectory(output);
try
{
    var result = args[0] == "native" ? await NativeTrial.RunAsync(config, output) : ModelTrial.Run(config, output);
    using var stream = new FileStream(Path.Combine(output, "trial.json"), FileMode.CreateNew);
    JsonSerializer.Serialize(stream, result, json);
    Console.WriteLine(output);
    if (result is EntityFX.MqttY.Plugin.Mqtt.Experiments.MqttRelayExperimentResult model &&
        model.Summary.Status != EntityFX.MqttY.Plugin.Mqtt.Experiments.ExperimentRunStatus.Completed) return 2;
    return 0;
}
catch (Exception error)
{
    using var stream = new FileStream(Path.Combine(output, "trial-failure.json"), FileMode.CreateNew);
    JsonSerializer.Serialize(stream, new { error = error.ToString(), config }, json);
    Console.Error.WriteLine(error);
    return 1;
}
