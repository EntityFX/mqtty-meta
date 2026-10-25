"""Bounded Web queue acceptance: nine ideal experiments and three exact replays.
This verifies execution and evidence handling, not accuracy against a real broker.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.request


def run(url, output, data_root=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    receipt = {"passed": False, "scientificAccuracyClaim": False, "jobs": [], "replays": [], "startedEpoch": time.time()}
    own = []
    deadline = time.monotonic() + 180

    def request(path, body=None, raw=False):
        req = urllib.request.Request(url.rstrip('/') + path, data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as response:
            value = response.read()
            return value if raw else json.loads(value)

    def save():
        (output / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def submit(body):
        job = request('/api/simulations', body)
        own.append(job['id'])
        return job['id']

    def finish(id):
        while True:
            job = request('/api/simulations/' + id)
            if job['state'] not in ('Queued', 'Running', 'Paused', 'Cancelling'):
                break
            if time.monotonic() > deadline:
                raise TimeoutError('Acceptance deadline reached; only own jobs will be stopped')
            time.sleep(.2)
        if job['state'] != 'Completed':
            raise RuntimeError('Own acceptance job failed: ' + json.dumps(job))
        raw = request('/api/simulations/' + id + '/results/summary.json', raw=True)
        summary = json.loads(raw)
        if summary['status'] != 'completed' or summary['scheduledPublishes'] != 20 or not 0 < summary['apiAttempts'] <= 20 or summary['apiAttempts'] + summary['backpressureUnsent'] != 20:
            raise AssertionError(summary)
        if 'uniqueDeliveries' in summary:
            assert summary['uniqueDeliveries'] == summary['expectedDeliveries']
        else:
            assert summary['delivery']['uniqueDelivered'] == summary['delivery']['measurementCreated'] == summary['apiAttempts']
        sealed = 0
        if data_root:
            root = Path(data_root).resolve(strict=True)
            result = Path(job['outputDir']).resolve(strict=True)
            if root not in result.parents:
                raise RuntimeError('Output escapes Web data root')
            for relative, expected in json.loads((result / 'sha256.json').read_text()).items():
                item = (result / relative).resolve(strict=True)
                if result not in item.parents:
                    raise RuntimeError('Invalid seal member')
                if hashlib.sha256(item.read_bytes()).hexdigest() != expected:
                    raise RuntimeError('Seal mismatch: ' + relative)
                sealed += 1
        return {"id": id, "state": job['state'], "outputDir": job['outputDir'], "summarySha256": hashlib.sha256(raw).hexdigest(), "sealedFilesVerified": sealed, "apiAttempts": summary['apiAttempts']}

    try:
        plan = []
        for engine in ['relay', 'federation', 'loadBalancing']:
            for qos in [0, 1, 2]:
                body = {"engine": engine, "spec": {"kind": "line", "chainLength": 2, "brokerPresetId": "ideal-demo", "clientsPerBroker": 1,
                    "warmupTicks": 10, "measurementTicks": 200, "drainTimeoutTicks": 10000, "offeredRps": 1000, "payloadBytes": 16, "qos": qos, "seed": 42},
                    "runSettings": {"executionMode": "OutOfProcess", "timeLimit": "00:01:00"}}
                plan.append(body)
        # A complete admission pass precedes the first execution.
        for body in plan:
            assert request('/api/simulations', dict(body, validateOnly=True))['valid']
        receipt['allPointsValidatedBeforeQueueing'] = True
        (output / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
        ids = [submit(body) for body in plan]
        for body, id in zip(plan, ids):
            result = finish(id)
            result.update(engine=body['engine'], qos=body['spec']['qos'])
            receipt['jobs'].append(result)
            save()
        for engine in ['relay', 'federation', 'loadBalancing']:
            body = next(b for b in plan if b['engine'] == engine and b['spec']['qos'] == 1)
            result = finish(submit(body))
            original = next(j for j in receipt['jobs'] if j['engine'] == engine and j['qos'] == 1)
            result['summaryMatchesOriginalBytes'] = result['summarySha256'] == original['summarySha256']
            if not result['summaryMatchesOriginalBytes']:
                raise AssertionError('Replay summary differs for ' + engine)
            receipt['replays'].append(result)
            save()
        assert len({j['outputDir'] for j in receipt['jobs'] + receipt['replays']}) == 12
        receipt['distinctOutputDirectories'] = 12
        receipt['passed'] = True
    except Exception as error:
        receipt['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        if not receipt['passed']:
            for id in own:
                try: request('/api/simulations/' + id + '/stop', {})
                except Exception: pass
        receipt['finishedEpoch'] = time.time()
        save()
    print(json.dumps({"passed": True, "experiments": len(receipt['jobs']), "exactReplays": len(receipt['replays']), "distinctOutputs": 12}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:5088')
    parser.add_argument('--output', required=True)
    parser.add_argument('--data-root')
    args = parser.parse_args()
    run(args.url, args.output, args.data_root)
