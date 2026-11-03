"""Build an isolated portable benchmark package from meta-repository gitlinks."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

import run as suite


def execute(argv):
    subprocess.run(argv, check=True)


def generate_probe(package):
    """Add a switch to copies of the two runners, never mutate model sources."""
    base = package / 'mqtty/src/EntityFX.MqttSimulator'
    probe = package / 'probe'; probe.mkdir()
    records = []
    for engine, name in [('Federation', 'FederationRunner'), ('LoadBalancing', 'LoadBalancingExperimentRunner')]:
        source = base / ('EntityFX.MqttY.%s.App/Execution/%s.cs' % (engine, name))
        original = source.read_text(encoding='utf-8-sig')
        declaration = 'public sealed class ' + name
        assert original.count(declaration) == 1 and original.count('Refresh(false, 0)') == 1
        generated = original.replace(declaration, 'public sealed class Benchmark' + name)
        generated = generated.replace('Refresh(false, 0)', 'Refresh(ParallelExecution, 0)')
        marker = 'public sealed class Benchmark' + name + '\n{'
        assert generated.count(marker) == 1
        generated = generated.replace(marker, marker + '\n    public bool ParallelExecution { get; init; }')
        (probe / (name + '.cs')).write_text(generated, encoding='utf-8', newline='\n')
        records.append({'source': source.relative_to(package).as_posix(),
            'sourceSha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'generated': 'probe/' + name + '.cs',
            'generatedSha256': hashlib.sha256((probe / (name + '.cs')).read_bytes()).hexdigest(),
            'changes': ['class name', 'ParallelExecution bool property', 'Refresh bool only']})
    (probe / 'Program.cs').write_text('''using EntityFX.MqttY.Federation;
using EntityFX.MqttY.LoadBalancing;
if (args.Length != 4 || args[1] is not ("serial" or "parallel")) return 1;
var parallel = args[1] == "parallel";
if (args[0] == "load-balancer") {
    var input = ExperimentInput.Load(args[2], false).Single();
    var result = new BenchmarkLoadBalancingExperimentRunner { ParallelExecution = parallel }.Run(input, args[3]);
    Console.WriteLine(result.Status); return result.Status == "completed" ? 0 : 2;
}
if (args[0] is not ("relay" or "federation")) return 1;
var federation = FederationInput.Load(args[2]);
if (federation.Options.Mode.ToString().ToLowerInvariant() != args[0]) return 1;
var summary = new BenchmarkFederationRunner { ParallelExecution = parallel }.Run(federation, args[3]);
Console.WriteLine(summary.Status); return summary.Status == "completed" ? 0 : 2;
''', encoding='utf-8')
    (probe / 'BenchmarkProbe.csproj').write_text('''<Project Sdk="Microsoft.NET.Sdk">
<PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net6.0</TargetFramework><ImplicitUsings>enable</ImplicitUsings><Nullable>enable</Nullable></PropertyGroup>
<ItemGroup>
<ProjectReference Include="../mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.Federation.App/EntityFX.MqttY.Federation.App.csproj" />
<ProjectReference Include="../mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.LoadBalancing.App/EntityFX.MqttY.LoadBalancing.App.csproj" />
</ItemGroup></Project>''', encoding='utf-8')
    suite.write_json(package / 'adapter.json', records)
    return probe / 'BenchmarkProbe.csproj'


def prepare(output):
    root = Path(__file__).resolve().parents[1]
    if suite.command(['git', 'branch', '--show-current']) != 'develop':
        raise ValueError('Prepare from develop')
    output.mkdir(parents=True, exist_ok=False)
    package = output / 'package'; package.mkdir()
    commits = {}
    for name in ('mqtty', 'MqttBenchmark'):
        commits[name] = suite.command(['git', 'rev-parse', 'HEAD:' + name])
        execute(['git', '-c', 'core.autocrlf=false', 'clone', '--no-local', '--no-checkout', str(root / name), str(package / name)])
        execute(['git', '-C', str(package / name), 'config', 'core.autocrlf', 'false'])
        if subprocess.run(['git', '-C', str(package / name), 'cat-file', '-e', commits[name]], capture_output=True).returncode:
            remote = suite.command(['git', '-C', str(root / name), 'remote', 'get-url', 'origin'])
            execute(['git', '-C', str(package / name), 'fetch', remote, commits[name]])
        execute(['git', '-C', str(package / name), 'checkout', '--detach', commits[name]])
    project = generate_probe(package)
    with (output / 'build.log').open('wb') as log:
        subprocess.run(['dotnet', 'publish', str(project), '-c', 'Release', '--self-contained', 'false',
                        '-p:UseAppHost=false', '-o', str(package / 'app')], stdout=log, stderr=subprocess.STDOUT, check=True)
    shutil.copyfile(root / 'reproducibility/benchmark.py', package / 'benchmark.py')
    common = dict(ideal=True, payloadBytes=256, offeredRps=1000, seed=42, warmupSeconds=0.01,
                  measurementSeconds=0.2, drainTimeoutSeconds=1, tickPeriod='00:00:00.0001000',
                  network=dict(capacityBytesPerSecond=100000000, queueCapacity=100000,
                               transferTicks=1, throughputWindowTicks=100))
    relay = dict(common, mode='relay', brokers=4, publishers=16, qos=1, topology='line')
    federation = dict(relay, mode='federation')
    balance = dict(common, mode='roundRobin', backendCount=4, totalPublishers=16,
                   publishQos=1, subscribeQos=1, networkLength=2)
    suite.write_json(package / 'cases.json', {'relay': relay, 'federation': federation, 'load-balancer': balance})
    files = [package / 'benchmark.py', package / 'adapter.json', package / 'cases.json']
    files += [p for p in (package / 'probe').iterdir() if p.is_file()]
    files += [p for p in (package / 'app').rglob('*') if p.is_file()]
    for name in commits:
        tracked = subprocess.check_output(['git', '-C', str(package / name), 'ls-files', '-z']).decode().split('\0')
        files += [package / name / p for p in tracked if p]
    manifest = {'schemaVersion': 1, 'metaCommit': suite.command(['git', 'rev-parse', 'HEAD']),
                'builderSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'sourceCommits': commits, 'buildMode': 'Release managed framework-dependent without RID/apphost',
                'casesStatus': 'Candidate; freeze after pilots on every target',
                'files': {p.relative_to(package).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}}
    suite.write_json(package / 'manifest.json', manifest)
    archive = output / 'mqtty-benchmark.tar.gz'
    def exclude_build(info):
        parts = Path(info.name).parts
        return None if ('bin' in parts or 'obj' in parts) else info
    with tarfile.open(archive, 'w:gz') as stream:
        stream.add(package, arcname='package', filter=exclude_build)
    suite.write_json(output / 'package-receipt.json', {'archive': archive.name,
        'archiveSha256': hashlib.sha256(archive.read_bytes()).hexdigest(), 'archiveBytes': archive.stat().st_size,
        'manifestSha256': hashlib.sha256((package / 'manifest.json').read_bytes()).hexdigest(),
        'sourceCommits': commits, 'simulationsStarted': 0})
    print(package)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    prepare(parser.parse_args().output.resolve())
