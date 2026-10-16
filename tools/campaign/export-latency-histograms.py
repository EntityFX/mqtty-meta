"""Export empirical latency counts from sealed successful calibration archives only."""
import argparse
import collections
import hashlib
import io
import json
import math
import pathlib
import zipfile


def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding='utf-8-sig'))


def digest(path):
    with pathlib.Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def archive_path(receipt):
    path = receipt['archivePath']
    if __import__('os').name == 'nt':
        path = path.replace('/run/media/entityfx/hd250/mqtty-evidence/', '//192.168.5.1/mqtty-evidence/')
    return pathlib.Path(path)


def extract(path, expected_sha):
    if digest(path) != expected_sha:
        raise ValueError('Archive SHA-256 mismatch: ' + str(path))
    with zipfile.ZipFile(path) as archive:
        seals = json.loads(archive.read('sha256.json'))
        raw_result = archive.read('result.json')
        if hashlib.sha256(raw_result).hexdigest() != seals['result.json']:
            raise ValueError('Result seal mismatch')
        result = json.loads(raw_result)
        if result['status'] != 'success':
            raise ValueError('Only successful attempts may calibrate a profile')
        measurement = result['observation']['measurement']
        histogram, completed, attempted, failed, latency = collections.Counter(), set(), set(), set(), []
        hasher = hashlib.sha256()
        with io.BufferedReader(archive.open('publishes.ndjson'), buffer_size=1024**2) as stream:
            for line in stream:
                hasher.update(line)
                event = json.loads(line)
                key = event['id']
                if event['event'] == 'attempt':
                    if key in attempted:
                        raise ValueError('Duplicate attempt ID')
                    attempted.add(key)
                elif event['event'] in ('completed', 'failed'):
                    if key not in attempted or key in completed or key in failed:
                        raise ValueError('Invalid publish outcome identity')
                    if event['event'] == 'failed':
                        failed.add(key)
                    else:
                        value = event['latencyMs']
                        if value is None or not math.isfinite(value) or value < 0:
                            raise ValueError('Invalid acknowledged publish latency')
                        completed.add(key)
                        latency.append(value)
                        histogram[math.ceil(value * 1000)] += 1
                else:
                    raise ValueError('Unexpected publish event')
        if hasher.hexdigest() != seals['publishes.ndjson']:
            raise ValueError('Publish-event seal mismatch')
        if len(completed) != measurement['completedPublishes'] or len(attempted) != measurement['attemptedPublishes'] or len(failed) != measurement['failedPublishes'] or attempted != completed | failed:
            raise ValueError('Raw completion counts differ from the sealed summary')
        latency.sort()
        knots = [('minMs', 0), ('p50Ms', .5), ('p75Ms', .75), ('p95Ms', .95), ('p99Ms', .99), ('maxMs', 1)]
        if not latency:
            raise ValueError('No completed acknowledged publications')
        for name, probability in knots:
            value = latency[max(0, math.ceil(probability * len(latency)) - 1)]
            if value != measurement['publishLatencyMs'][name]:
                raise ValueError('Raw quantiles differ from summary: ' + name)
        return result, {'repeat': result['key']['repeat'], 'archiveSha256': expected_sha,
            'completedPublishes': len(completed), 'actualMeasurementSeconds': measurement['actualMeasurementSeconds'],
            'bins': sorted([micros, count] for micros, count in histogram.items())}


def export(observations_path, raw_root, output):
    observations = read(observations_path)
    points = []
    for point in observations['points']:
        if point['qos'] == 0:
            continue
        broker, size, qos, publishers = (point[name] for name in ('broker', 'messageBytes', 'qos', 'publishers'))
        repeats = []
        for repeat in (1, 2, 3):
            key = f'{broker}.m{size}.q{qos}.p{publishers}.r{repeat}'
            paths = [path for path in (pathlib.Path(raw_root) / 'attempts' / key).glob('attempt-*/result.json') if read(path)['status'] == 'success']
            if len(paths) != 1:
                raise ValueError('Expected exactly one successful attempt: ' + key)
            receipt = read(paths[0].parent / 'archive.json')
            result, distribution = extract(archive_path(receipt), receipt['sha256'])
            if result['identity'] != observations['provenance']['identity'] or result['key']['key'] != key:
                raise ValueError('Histogram raw belongs to another observation identity')
            repeats.append(distribution)
            print('Verified', key, 'samples', distribution['completedPublishes'], flush=True)
        points.append({'broker': broker, 'messageBytes': size, 'qos': qos, 'publishers': publishers, 'repeats': repeats})
    value = {'schemaVersion': 1, 'artifactType': 'broker-publish-latency-histograms',
        'observationsSha256': digest(observations_path), 'campaignId': observations['provenance']['identity']['campaignId'],
        'points': points}
    output = pathlib.Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # An existing snapshot may not be silently regenerated/overwritten.
    with output.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, separators=(',', ':'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('observations', 'raw', 'output'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    export(args.observations, args.raw, args.output)


if __name__ == '__main__':
    main()
