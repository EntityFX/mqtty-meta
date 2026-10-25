namespace TwinValidationProbe.Configuration;

public sealed class TwinTrialConfiguration
{
    public string Id { get; set; } = "";
    public string Broker { get; set; } = "Mosquitto";
    public int Qos { get; set; } = 1;
    public int Publishers { get; set; } = 16;
    public int PayloadBytes { get; set; } = 16;
    public int Seed { get; set; } = 42;
    public int Repeat { get; set; } = 1;
    public double WarmupSeconds { get; set; } = .01;
    public double MeasurementSeconds { get; set; } = .2;
    public double DrainSeconds { get; set; } = 1;
    public double TickSeconds { get; set; } = .00001;
    public string Endpoint { get; set; } = "mqtt://127.0.0.1:1883";
    public string ProfilePath { get; set; } = "";
    public string ProfileSha256 { get; set; } = "";
    public string RuntimeIdentity { get; set; } = "";

    public void Validate()
    {
        if (string.IsNullOrWhiteSpace(Id) || string.IsNullOrWhiteSpace(RuntimeIdentity) ||
            Qos is not (1 or 2) || Publishers <= 0 || PayloadBytes != 16 ||
            new[] { MeasurementSeconds, DrainSeconds, TickSeconds }.Any(x => !double.IsFinite(x) || x <= 0) ||
            !double.IsFinite(WarmupSeconds) || WarmupSeconds < 0)
            throw new InvalidDataException("Invalid bounded twin trial configuration.");
    }
}
