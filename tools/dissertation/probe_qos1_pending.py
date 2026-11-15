"""Bounded, isolated diagnostic; does not modify scientific campaigns or MqttY source."""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import tempfile
import time

SOURCE = r'''
using System.Collections;
using System.Reflection;
using System.Text.Json;
using EntityFX.MqttY.Contracts.Mqtt;
using EntityFX.MqttY.Contracts.Network;
using EntityFX.MqttY.Contracts.Options;
using EntityFX.MqttY.Federation;

class Probe {
static object Field(object instance,string name) {
 for(Type? t=instance.GetType();t!=null;t=t.BaseType) {
 var f=t.GetField(name,BindingFlags.Instance|BindingFlags.NonPublic);
 if(f!=null)return f.GetValue(instance)!;
 } throw new Exception(name);
}
static object Property(object o,string n)=>o.GetType().GetProperty(n)!.GetValue(o)!;
static int Count(object o)=>Convert.ToInt32(Property(o,"Count"));
static object[] Sessions(object broker)=>((IEnumerable)Field(broker,"_sessionRepository").GetType().GetMethod("ReadAll")!.Invoke(Field(broker,"_sessionRepository"),null)!).Cast<object>().OrderBy(s=>Property(s,"Id").ToString(),StringComparer.Ordinal).ToArray();
static int Pending(object broker)=>Sessions(broker).Sum(s=>Count(Property(s,"PendingMessages")));
static object Snapshot(FederationTopology t,FederationDeliveryLedger ledger)=>new {
 tick=t.Graph.TotalTicks,idle=t.IsIdle,networkQueued=t.Graph.Networks.Values.Sum(n=>n.QueueSize),
 nodes=t.Graph.Clients.Values.Cast<object>().Concat(t.Brokers.Cast<object>()).OrderBy(n=>Property(n,"Name").ToString(),StringComparer.Ordinal).Select(n=>new {
 name=Property(n,"Name"),quiescent=((IQuiescenceParticipant)n).IsQuiescent,
 outgoing=Count(Field(n,"_outgoingMessages")),incoming=Count(Field(n,"_incommingMessages")),
 sessions=Sessions(n).Select(s=>new {id=Property(s,"Id"),pending=((IEnumerable)Property(s,"PendingMessages")).Cast<object>().Select(p=>new {
 status=Property(p,"Status").ToString(),packetId=Property(p,"PacketId"),topic=Property(p,"Topic")}).ToArray()}).ToArray()
 }).ToArray(),unique=ledger.Unique
};
static object Run(bool refuse) {
 var o=new FederationOptions {Brokers=1,Publishers=1,Topology="line",Mode=FederationMode.Single,WarmupSeconds=0,MeasurementSeconds=.001,Qos=1};
 var ledger=new FederationDeliveryLedger(o);
 using var t=FederationTopologyBuilder.Build(FederationInput.Prepare(o),ledger,new TransmissionLedger(ledger));
 void Tick() {t.Pump();if(!t.Graph.Refresh(false,0))throw new Exception("refresh");}
 void Until(Func<bool> done) {for(int i=0;i<500&&!done();i++)Tick();if(!done())throw new Exception("setup");}
 foreach(var c in t.Connections)c.Client.BeginConnect(t.Brokers[c.Target].Name,true);
 Until(()=>t.Graph.Clients.Values.All(c=>c.IsConnected));
 foreach(var s in t.Subscriptions)s.Client.BeginSubscribe(s.Filter,MqttQos.AtLeastOnce);
 Until(()=>t.Subscriptions.All(s=>s.Client.IsSubscribed(s.Filter))&&t.IsIdle);
 var net=t.Graph.Networks.Values.Single();var opts=(NetworkOptions)Field(net,"_networkTypeOption");
 var publication=ledger.Create(0,t.Graph.TotalTicks,true);
 t.Publishers[0].Publish(FederationTopics.Delivered(0),publication.Payload,MqttQos.AtLeastOnce);
 Until(()=>Pending(t.Brokers[0])>0);
 var before=Snapshot(t,ledger);var capacity=opts.CapacityBytesPerSecond;
 // Deliberately refuse the already-queued broker Publish and ACK for two ticks.
 // This diagnostic fault is not a campaign input or a protocol implementation change.
 if(refuse)opts.CapacityBytesPerSecond=1;
 Tick();Tick();opts.CapacityBytesPerSecond=capacity;
 for(int i=0;i<1000;i++)Tick();
 var after=Snapshot(t,ledger);
 var refused=net.Counters.Counters.OfType<EntityFX.MqttY.Contracts.Counters.CounterGroup>().SelectMany(g=>g.Counters).Where(c=>c.Name=="Refused").Sum(c=>Convert.ToInt64(c.Value));
 for(int i=0;i<10000;i++)Tick();
 var final=Snapshot(t,ledger);
 if(refuse&&!(refused>0&&Pending(t.Brokers[0])>0&&net.QueueSize==0&&!t.IsIdle))throw new Exception("refusal did not reproduce stranding");
 if(!refuse&&!(t.IsIdle&&ledger.Unique==1&&Pending(t.Brokers[0])==0&&refused==0))throw new Exception("control failure");
 return new {fault=refuse,refused,before,after,final};
}
static void Main()=>Console.WriteLine(JsonSerializer.Serialize(new {control=Run(false),refusal=Run(true)}));
}
'''

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    root=args.root.resolve(); output=args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    binary=root/"mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.Federation.App/bin/Release/net6.0"
    artifacts=root/"MqttBenchmark/artifacts"
    artifacts.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix="qos1-pending-probe-",dir=artifacts))
    (work/"Program.cs").write_text(SOURCE,encoding="utf-8")
    refs="".join(f'<Reference Include="{x.stem}"><HintPath>{x}</HintPath><Private>true</Private></Reference>' for x in binary.glob("*.dll"))
    (work/"Probe.csproj").write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net6.0</TargetFramework><ImplicitUsings>enable</ImplicitUsings><Nullable>enable</Nullable></PropertyGroup><ItemGroup>'+refs+'</ItemGroup></Project>',encoding="utf-8")
    started=time.monotonic()
    build=subprocess.run(["dotnet","build",str(work/"Probe.csproj"),"-c","Release","--nologo"],capture_output=True,text=True,timeout=60)
    (output/"build.log").write_text(build.stdout+build.stderr,encoding="utf-8")
    if build.returncode: raise SystemExit("Build failed; see build.log")
    run=subprocess.run(["dotnet",str(work/"bin/Release/net6.0/Probe.dll")],capture_output=True,text=True,timeout=60)
    (output/"stderr.log").write_text(run.stderr,encoding="utf-8")
    (output/"stdout.log").write_text(run.stdout,encoding="utf-8")
    if run.returncode: raise SystemExit("Probe failed; see logs")
    result=json.loads(run.stdout)
    # Independent structural checks, separate from the C# assertions.
    for key in ("after", "final"):
        fault=result["refusal"][key]
        assert fault["networkQueued"] == 0 and not fault["idle"]
        assert all(n["outgoing"] == n["incoming"] == 0 for n in fault["nodes"])
        assert sum(len(s["pending"]) for n in fault["nodes"] for s in n["sessions"]) == 2
        assert fault["unique"] == 0
        assert result["control"][key]["idle"] and result["control"][key]["unique"] == 1
    assert result["refusal"]["after"]["nodes"] == result["refusal"]["final"]["nodes"]
    result["elapsedSeconds"]=time.monotonic()-started
    result["binarySha256"]={x.name:hashlib.sha256(x.read_bytes()).hexdigest() for x in binary.glob("EntityFX*.dll")}
    result["sourceSha256"]=hashlib.sha256(SOURCE.encode()).hexdigest()
    model=root/"mqtty/src/EntityFX.MqttSimulator"
    sources=["EntityFX.MqttSimulator/Network/Node.cs","EntityFX.MqttSimulator/Network/Network.cs",
             "EntityFX.MqttY.Plugin.Mqtt/MqttBroker.cs","EntityFX.MqttY.Plugin.Mqtt/MqttClient.cs",
             "EntityFX.MqttY.Federation.App/Federation/StaticFederator.cs"]
    result["modelSourceSha256"]={name:hashlib.sha256((model/name).read_bytes()).hexdigest() for name in sources}
    result["modelCommit"]=subprocess.check_output(["git","-C",str(root/"mqtty"),"rev-parse","HEAD"],text=True,timeout=10).strip()
    (output/"verification.json").write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    (output/"Probe.cs").write_text(SOURCE,encoding="utf-8")
    print(json.dumps({"status":"verified","output":str(output),"elapsedSeconds":result["elapsedSeconds"]}))

if __name__ == "__main__": main()
