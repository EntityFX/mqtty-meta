"""Local load-balancer workload sweep, with native Refresh timing and sealed science.

prepare -> pilot -> main -> analyze. No SSH or automatic experiment retries.
"""
import argparse
import csv
import json
import os
from pathlib import Path
import platform
import random
import shutil
import statistics
import subprocess
import sys

import benchmark as b
import verify_benchmark_results as audit


PROGRAM = '''using System.Text.Json;
using EntityFX.MqttY.LoadBalancing;
if (args.Length != 4 || args[0] is not ("serial" or "parallel") || args[1] is not ("timed" or "plain")) return 1;
var input = ExperimentInput.Load(args[2], false).Single();
var runner = new LoadBalancingExperimentRunner();
ExperimentSummary summary;
if (args[1] == "timed") {
    var result = runner.RunMeasured(input, args[3], args[0] == "parallel", 0);
    summary = result.Summary;
    File.WriteAllText(Path.Combine(Path.GetDirectoryName(args[3])!, "refresh-timing.json"),
        JsonSerializer.Serialize(result.Timing, ExperimentInput.Json));
} else summary = runner.Run(input, args[3], args[0] == "parallel", 0);
Console.WriteLine(summary.Status);
return summary.Status == "completed" ? 0 : 2;
'''


def prepare(root):
    repo = Path(__file__).resolve().parents[1]
    if b.command(['git', '-C', str(repo), 'branch', '--show-current']) != 'develop':
        raise ValueError('develop required')
    source = repo / 'mqtty'
    if b.command(['git', '-C', str(source), 'status', '--porcelain', '--untracked-files=no']):
        raise ValueError('Commit simulator changes first')
    root.mkdir(parents=True, exist_ok=False)
    commit = b.command(['git', '-C', str(source), 'rev-parse', 'HEAD'])
    package = root / 'package'; package.mkdir()
    subprocess.run(['git', '-c', 'core.autocrlf=false', 'clone', '--no-local', str(source), str(package / 'mqtty')], check=True)
    subprocess.run(['git', '-C', str(package / 'mqtty'), 'checkout', '--detach', commit], check=True)
    probe = package / 'probe'; probe.mkdir()
    (probe / 'Program.cs').write_text(PROGRAM, encoding='utf-8', newline='\n')
    (probe / 'LoadProbe.csproj').write_text('''<Project Sdk="Microsoft.NET.Sdk">
<PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net6.0</TargetFramework><ImplicitUsings>enable</ImplicitUsings></PropertyGroup>
<ItemGroup><ProjectReference Include="../mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.LoadBalancing.App/EntityFX.MqttY.LoadBalancing.App.csproj" /></ItemGroup>
</Project>''', encoding='utf-8', newline='\n')
    with (root / 'build.log').open('wb') as log:
        subprocess.run(['dotnet', 'publish', str(probe / 'LoadProbe.csproj'), '-c', 'Release', '--self-contained', 'false',
                        '-p:UseAppHost=false', '-o', str(package / 'app')], stdout=log, stderr=subprocess.STDOUT, check=True)
    configs = root / 'configs'; configs.mkdir()
    cells = []
    for count in (4, 16, 64):
        for rate in (250, 1000, 4000):
            name = 'b%d-r%d' % (count, rate)
            spec = dict(ideal=True, mode='roundRobin', backendCount=count, totalPublishers=count * 16,
                        payloadBytes=256, publishQos=1, subscribeQos=1, offeredRps=count * rate,
                        seed=42, warmupSeconds=.01, measurementSeconds=.2, drainTimeoutSeconds=1,
                        tickPeriod='00:00:00.0001000', networkLength=2,
                        network=dict(capacityBytesPerSecond=100000000, queueCapacity=100000,
                                     transferTicks=1, throughputWindowTicks=100))
            b.save(configs / (name + '.json'), spec)
            cells.append(dict(name=name, backends=count, rpsPerBackend=rate, offeredRps=count * rate))
    schedule = []; rng = random.Random(42)
    for repeat in range(1, 8):
        order = list(cells); rng.shuffle(order)
        for index, cell in enumerate(order):
            for mode in (('serial', 'parallel') if (repeat + index) % 2 else ('parallel', 'serial')):
                schedule.append(dict(cell=cell['name'], repeat=repeat, execution=mode))
    protocol = dict(schemaVersion=1, mqttyCommit=commit, metaCommit=b.command(['git', '-C', str(repo), 'rev-parse', 'HEAD']),
                    purpose='Identify load-balancer parallel crossover with fixed per-backend traffic; local pilot research for article 2 and future dissertation chapter 5',
                    refreshStrategy=0, countedRepeats=7, timeoutSeconds=180, cells=cells, mainSchedule=schedule,
                    rules=['No automatic retries', 'All failed/mismatching attempts retained', 'No accepted speedup without complete equivalent repetitions',
                           'Primary metric: sum of wall-clock durations strictly inside graph.Refresh during measure phase',
                           'Secondary metrics: full process, full runner, measure phase, export, CPU, peak working set',
                           'Pilot rejects cells with any technical, arithmetic or scientific mismatch; no selecting successful retries',
                           'Fresh process/JIT each attempt; timers remain included in process and runner measurements',
                           'Only local execution; remote deployment deferred until review'],
                    configSha256={p.name: b.digest(p) for p in configs.iterdir()},
                    appSha256={p.name:b.digest(p) for p in (package / 'app').iterdir() if p.is_file()},
                    probeSha256={p.name:b.digest(p) for p in probe.iterdir() if p.is_file()},
                    driverSha256=b.digest(__file__))
    b.save(root / 'protocol.json', protocol)
    b.save(root / 'environment.json', dict(os=platform.platform(), processor=platform.processor(), logicalCpus=os.cpu_count(),
           python=platform.python_version(), dotnetInfo=b.command(['dotnet','--info']),
           hardware=b.command(['powershell','-NoProfile','-Command', 'Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors | ConvertTo-Json -Compress']),
           runtimeEnvironment={k:v for k,v in os.environ.items() if k.startswith(('DOTNET_', 'COMPlus_'))}))
    b.save(root / 'attempts.json', dict(protocolSha256=b.digest(root / 'protocol.json'), rows=[]))


def check_package(root, protocol):
    for name, expected in protocol['appSha256'].items():
        audit.require(b.digest(root / 'package/app' / name) == expected, 'Changed app: ' + name)
    for name, expected in protocol['configSha256'].items():
        audit.require(b.digest(root / 'configs' / name) == expected, 'Changed config: ' + name)
    audit.require(b.command(['git', '-C', str(root / 'package/mqtty'), 'rev-parse','HEAD']) == protocol['mqttyCommit'], 'Wrong source')


def attempt(root, protocol, state, phase, cell, repeat, mode, timed=True):
    name = '%s-%s-r%d-%s-%s' % (phase, cell, repeat, mode, 'timed' if timed else 'plain')
    destination = root / 'attempts' / name
    destination.parent.mkdir(exist_ok=True)
    spec = b.read(root / 'configs' / (cell + '.json'))
    argv = ['dotnet', str(root / 'package/app/LoadProbe.dll'), mode, 'timed' if timed else 'plain',
            str(root / 'configs' / (cell + '.json')), str(destination / 'raw')]
    row = dict(b.measure(argv, root / 'package/mqtty', destination, protocol['timeoutSeconds']),
               name=name, phase=phase, cell=cell, repeat=repeat, execution=mode, timed=timed)
    try:
        audit.require(row['returnCode'] == 0 and not row['timedOut'], 'Process failed')
        count, fingerprint = audit.seal(destination / 'raw')
        audit.require(b.read(destination / 'raw/inputs/config.json') == spec, 'Executed config changed')
        runtime = b.read(destination / 'raw/manifest.json')['runtime']
        expected = {n:h for n,h in protocol['appSha256'].items() if n.endswith('.dll')}
        audit.require(runtime['binarySha256'] == expected, 'Executed DLL changed')
        audit.recount(destination / 'raw', 'load-balancer', spec)
        row.update(verified=True, sealedFiles=count, fingerprint=fingerprint)
        if timed:
            timing = b.read(destination / 'refresh-timing.json')
            ticks = round(spec['measurementSeconds'] / .0001)
            audit.require(timing['refreshCallsByPhase']['measure'] == ticks, 'Refresh count changed')
            refresh = timing['refreshWallSecondsByPhase']['measure']
            phase_time = timing['phaseWallSeconds']['measure']
            audit.require(0 < refresh <= phase_time <= timing['runnerWallSeconds'] <= row['wallSeconds'], 'Invalid nested timers')
            row.update(measureRefreshSeconds=refresh, measurePhaseSeconds=phase_time,
                       runnerSeconds=timing['runnerWallSeconds'], exportSeconds=timing['exportWallSeconds'],
                       fullRefreshSeconds=timing['refreshWallSeconds'])
    except (ValueError, OSError, KeyError) as error:
        row.update(verified=False, diagnostic=str(error))
    state['rows'].append(row)
    b.save(root / 'attempts.json', state)
    print('CHECKED' if row['verified'] else 'DIAGNOSTIC', name, round(row['wallSeconds'],3), row.get('diagnostic',''), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['prepare','pilot','main','analyze'], required=True)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args(); root = args.root.resolve()
    if args.mode == 'prepare':
        prepare(root); return
    protocol = b.read(root / 'protocol.json'); check_package(root,protocol)
    state = b.read(root / 'attempts.json')
    audit.require(state['protocolSha256'] == b.digest(root / 'protocol.json'), 'Protocol changed')
    if args.mode == 'pilot':
        audit.require(not state['rows'], 'Pilot attempts already exist')
        for cell in protocol['cells']:
            for mode in ('serial','parallel'):
                attempt(root,protocol,state,'pilot',cell['name'],1,mode)
        admission = {}
        for cell in protocol['cells']:
            rows = [r for r in state['rows'] if r['cell']==cell['name']]
            admission[cell['name']] = len(rows)==2 and all(r['verified'] for r in rows) and rows[0]['fingerprint']==rows[1]['fingerprint']
        b.save(root / 'admission.json', dict(protocolSha256=b.digest(root / 'protocol.json'), cells=admission))
    elif args.mode == 'main':
        gate = b.read(root / 'admission.json')
        audit.require(gate['protocolSha256']==b.digest(root / 'protocol.json'), 'Admission changed')
        audit.require(all(r['phase']=='pilot' for r in state['rows']), 'Main attempts already exist')
        for entry in protocol['mainSchedule']:
            if gate['cells'][entry['cell']]:
                attempt(root,protocol,state,'main',entry['cell'],entry['repeat'],entry['execution'])
        # Paired controls of timer enabled/disabled, on the middle load at 4 backends.
        for repeat in range(1,4):
            for mode in ('serial','parallel'):
                for timed in ((False,True) if repeat % 2 else (True,False)):
                    attempt(root,protocol,state,'control','b4-r1000',repeat,mode,timed)
    else:
        analyze(root, protocol, state)
    check_package(root,protocol)


def analyze(root, protocol, state):
    result = dict(protocolSha256=b.digest(root / 'protocol.json'), rows=[], rawAttempts=len(state['rows']),
                  scientificScope='Ideal simulated load-balancer; wall-clock execution cost, not validation against real brokers',
                  intendedUse='Technical evidence for article 2; proposed future chapter 5, no manuscript text edited')
    for cell in protocol['cells']:
        item = dict(cell)
        pilots = [r for r in state['rows'] if r['phase']=='pilot' and r['cell']==cell['name']]
        baseline = next((r['fingerprint'] for r in pilots if r['execution']=='serial' and r['verified']),None)
        rows = [r for r in state['rows'] if r['phase']=='main' and r['cell']==cell['name']]
        valid = len(rows)==14 and all(r['verified'] and r['fingerprint']==baseline for r in rows+pilots)
        item.update(validated=valid, attempts=len(rows), scientificMismatches=sum(r['verified'] and r['fingerprint']!=baseline for r in rows+pilots))
        for mode in ('serial','parallel'):
            selected = [r for r in rows if r['execution']==mode and r['verified']]
            item[mode] = {key: [r[key] for r in selected] for key in ('wallSeconds','measureRefreshSeconds','measurePhaseSeconds','runnerSeconds','exportSeconds')}
            item[mode]['cpuSeconds']=[r['userSeconds']+r['systemSeconds'] for r in selected]
            item[mode]['peakMemoryMiB']=[r['peakMemoryBytes']/1024**2 for r in selected]
        for label,key in [('refreshSpeedup','measureRefreshSeconds'),('processSpeedup','wallSeconds')]:
            item[label] = statistics.median(item['serial'][key])/statistics.median(item['parallel'][key]) if valid else None
        item['pairedRefreshSpeedups'] = [next(r['measureRefreshSeconds'] for r in rows if r['repeat']==n and r['execution']=='serial') /
                                         next(r['measureRefreshSeconds'] for r in rows if r['repeat']==n and r['execution']=='parallel') for n in range(1,8)] if valid else []
        result['rows'].append(item)
    result['controls']=[r for r in state['rows'] if r['phase']=='control']
    b.save(root / 'analysis.json', result)
    for r in result['rows']:
        print(r['name'], 'valid=',r['validated'], 'Refresh speedup=',r['refreshSpeedup'],'process=',r['processSpeedup'])


if __name__ == '__main__':
    main()
