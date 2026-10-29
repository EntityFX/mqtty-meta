using EntityFX.MqttY.Federation;

if (args.Length != 3 || args[0] is not ("serial" or "parallel"))
    throw new ArgumentException("Usage: GraphExecutionProbe serial|parallel config.json NEW-output");
var input = FederationInput.Load(args[1]);
var result = new GraphExecutionRunner().Run(input, args[0] == "parallel", args[2]);
return result.Status == "completed" ? 0 : 2;
