import argparse
import importlib.util
import json
import pathlib
import tempfile
import types
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('portable', pathlib.Path(__file__).with_name('portable-linux.py'))
portable = importlib.util.module_from_spec(spec)
spec.loader.exec_module(portable)


class PortableTests(unittest.TestCase):
    def test_cpu_ranges_and_invalid_inputs(self):
        self.assertEqual({1, 3, 7, 8, 9}, portable.cpu_set('1,3,7-9'))
        for text in ('', '3-1', '-1', '1-2-3'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                portable.cpu_set(text)

    def test_non_ext4_and_not_a_mount_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = str(pathlib.Path(directory).resolve())
            for response in (f'{target} /dev/test ntfs', '/ /dev/test ext4'):
                with mock.patch.object(portable.subprocess, 'check_output', return_value=response):
                    with self.assertRaisesRegex(RuntimeError, 'ext4'):
                        portable.mount_identity(target)

    def test_dedicated_mount_is_accepted_and_system_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = str(pathlib.Path(directory).resolve())
            with mock.patch.object(portable.subprocess, 'check_output', side_effect=[f'{target} /dev/evidence ext4', '/dev/system']), \
                 mock.patch.object(portable.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=100 * 1024**3)):
                self.assertEqual([target, '/dev/evidence', 'ext4'], portable.mount_identity(target))
            with mock.patch.object(portable.subprocess, 'check_output', side_effect=[f'{target} /dev/system ext4', '/dev/system']):
                with self.assertRaisesRegex(RuntimeError, 'system filesystem'):
                    portable.mount_identity(target)

    def test_prepare_and_launch_use_target_machine_paths_and_cpu_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            mount = base / 'evidence'
            repo = base / 'sources'
            binaries = base / 'bin'
            binaries.mkdir()
            for name in ('benchmark.dll', 'simulator.dll', 'pwsh', 'broker'):
                (binaries / name).write_text(name)
            (repo / 'tools/campaign').mkdir(parents=True)
            (repo / 'tools/campaign/accelerated-resume.py').write_text('adapter')
            native_spec = base / 'brokers.json'
            portable.write(native_spec, {'brokers': [{'name': name, 'mqttUri': f'mqtt://127.0.0.1:{port}',
                'executable': str(binaries / 'broker'), 'args': [], 'brokerVersion': 'fixed-test'}
                for name, port in zip(portable.BROKERS, (1883, 2883, 3883, 4883))]})
            args = argparse.Namespace(repository=str(repo), archive_mount=str(mount), series=str(mount / 'runs/new'),
                spec=str(native_spec), benchmark_cli=str(binaries / 'benchmark.dll'), simulator_cli=str(binaries / 'simulator.dll'),
                pwsh=str(binaries / 'pwsh'), broker_cpu=8, controller_cpus='1,3,7,9',
                fidelity_workers=4, relay_workers=4, simulation_seconds=10, cpu_guard=99)
            assets = mount / 'runs/new/brokers-native'
            (assets / 'aedes/node_modules/package').mkdir(parents=True)
            (assets / 'aedes/node_modules/package/index.js').write_text('immutable-dependency')
            (assets / 'emqx/data').mkdir(parents=True)
            (assets / 'emqx/data/runtime-state').write_text('mutable')
            args.broker_assets_root = str(assets)
            original_read = pathlib.Path.read_text

            def read_text(path, *a, **kw):
                if str(path).replace('\\', '/').endswith('/cpu8/topology/thread_siblings_list'):
                    return '8,12'
                return original_read(path, *a, **kw)

            def create_stand(argv, **kwargs):
                portable.write(argv[argv.index('-StandPath') + 1], {'brokers': [], 'cpuMode': 'singleCorePinned'})
                portable.write(argv[argv.index('-CampaignPath') + 1], {'schemaVersion': 3})
                return types.SimpleNamespace(returncode=0)

            with mock.patch.object(portable.sys, 'platform', 'linux'), mock.patch.object(portable, 'mount_identity', return_value=[str(mount.resolve()), '/dev/target', 'ext4']), \
                 mock.patch.object(portable.os, 'sched_getaffinity', return_value={1, 3, 7, 8, 9, 12}, create=True), \
                 mock.patch.object(portable.os, 'uname', return_value=types.SimpleNamespace(nodename='target-machine'), create=True), \
                 mock.patch.object(pathlib.Path, 'read_text', read_text), mock.patch.object(portable, 'repository_commit', return_value='frozen-commit'), \
                 mock.patch.object(portable, 'capture', side_effect=lambda argv: {'argv': argv}), \
                 mock.patch.object(portable.subprocess, 'check_output', return_value='pinned-packages'), \
                 mock.patch.object(portable.subprocess, 'run', side_effect=create_stand):
                portable.prepare(args)
            root = mount / 'runs/new/controller'
            plan = portable.read(root / 'series.json')
            self.assertEqual('1,3,7,9', plan['controllerCpuSet'])
            self.assertEqual('8', plan['brokerCpuSet'])
            self.assertEqual('12', plan['excludedControllerSmtSibling'])
            self.assertEqual('/dev/target', plan['archiveDevice'])
            self.assertIn('target-machine', plan['reportMachineDescription'])
            self.assertEqual(plan['simulatorCli'], plan['calibratorCli'])
            self.assertEqual(10, plan['simulationMeasurementSeconds'])
            environment = portable.read(root / 'environment.json')
            self.assertIn('aedes/node_modules/package/index.js', environment['brokerDistributionSha256'])
            self.assertNotIn('emqx/data/runtime-state', environment['brokerDistributionSha256'])
            for name in portable.BROKERS:
                calibration = portable.read(root / f'inputs/{name}-calibration.json')
                validation = portable.read(root / f'inputs/{name}-validation.json')
                pilot = portable.read(root / f'inputs/{name}-pilot.json')
                self.assertEqual(20260909, calibration['seed'])
                self.assertEqual(20260910, validation['seed'])
                self.assertEqual(30, calibration['measurementSeconds'])
                self.assertEqual([1, 16, 64, 128], calibration['publishers'])
                self.assertEqual([128], pilot['publishers'])
                self.assertEqual(1, pilot['repeats'])
                self.assertEqual(30, pilot['measurementSeconds'])
            calls = []
            with mock.patch.object(portable, 'mount_identity', return_value=[str(mount.resolve()), '/dev/target', 'ext4']), \
                 mock.patch.object(portable.subprocess, 'run', side_effect=lambda argv, **kw: calls.append((argv, kw))):
                with mock.patch.dict(portable.os.environ, {}):
                    portable.launch(argparse.Namespace(series=str(root), mode='run'))
            payload = json.loads(calls[0][1]['input'])
            self.assertEqual([1, 3, 7, 9], payload['cpus'])
            self.assertEqual(8 * 1024**3, payload['memoryLimit'])
            self.assertEqual(plan['orchestrationAdapter'], payload['args'][0])
            self.assertEqual('--resume', payload['args'][-1])
            self.assertEqual('run', portable.read(root / 'controller-service.json')['mode'])


if __name__ == '__main__':
    unittest.main()
