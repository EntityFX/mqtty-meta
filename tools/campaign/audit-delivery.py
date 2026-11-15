"""Independent read-only recount of sealed native MQTT event logs, without extracting raw."""
import argparse
import collections
import hashlib
import json
import math
import pathlib
import zipfile


def sha(stream):
    return hashlib.file_digest(stream, 'sha256').hexdigest()


def audit_archive(path, expected_sha):
    path = pathlib.Path(path)
    with path.open('rb') as stream:
        observed_sha = sha(stream)
    if observed_sha.lower() != expected_sha.lower():
        raise ValueError('Archive SHA-256 mismatch')
    with zipfile.ZipFile(path) as archive:
        seals = json.loads(archive.read('sha256.json'))
        for name, expected in seals.items():
            with archive.open(name) as stream:
                if sha(stream).lower() != expected.lower():
                    raise ValueError('Raw seal mismatch: ' + name)
        result = json.loads(archive.read('result.json'))
        measurement = result['observation']['measurement']
        identities, completed, failed, delivered = {}, set(), set(), set()
        attempt_duplicates = outcome_duplicates = delivery_duplicates = unexpected_deliveries = 0
        latencies, publish_bins, delivery_bins = [], collections.Counter(), collections.Counter()
        last_delivery = None
        with archive.open('publishes.ndjson') as stream:
            for line in stream:
                event = json.loads(line)
                key = event['id']
                if event['event'] == 'attempt':
                    attempt_duplicates += key in identities
                    identities[key] = (event['publisher'], event['sequence'])
                else:
                    if key not in identities:
                        raise ValueError('Outcome without preceding attempt')
                    outcome_duplicates += key in completed or key in failed
                    if event['event'] == 'completed':
                        completed.add(key)
                        publish_bins[math.floor(event['elapsedSeconds'])] += 1
                        if event.get('latencyMs') is not None:
                            latencies.append(event['latencyMs'])
                    elif event['event'] == 'failed':
                        failed.add(key)
                    else:
                        raise ValueError('Unknown publish event')
        with archive.open('deliveries.ndjson') as stream:
            for line in stream:
                event = json.loads(line)
                key = event['id']
                delivery_duplicates += key in delivered
                unexpected_deliveries += key not in identities
                delivered.add(key)
                last_delivery = event['elapsedSeconds']
                delivery_bins[math.floor(event['elapsedSeconds'])] += 1
        unique = set(identities)
        counts = {'attemptedPublishes': len(unique), 'completedPublishes': len(completed),
            'failedPublishes': len(failed), 'uniqueDeliveries': len(unique & delivered),
            'duplicateDeliveries': delivery_duplicates, 'unexpectedDeliveries': unexpected_deliveries,
            'deliveredAfterFailedPublish': len(failed & delivered), 'completedIdsDelivered': len(completed & delivered)}
        differences = {key: {'recounted': value, 'recorded': measurement[key]}
                       for key, value in counts.items() if value != measurement[key]}
        latencies.sort()
        p99 = latencies[math.ceil(.99 * len(latencies)) - 1] if latencies else None
        recorded_p99 = (measurement.get('publishLatencyMs') or {}).get('p99Ms')
        if p99 != recorded_p99:
            differences['p99Ms'] = {'recounted': p99, 'recorded': recorded_p99}
        per_publisher = collections.defaultdict(collections.Counter)
        for key, (publisher, sequence) in identities.items():
            per_publisher[publisher]['attempted'] += 1
            per_publisher[publisher]['completed'] += key in completed
            per_publisher[publisher]['delivered'] += key in delivered
        return {'archiveSha256': observed_sha, 'sealedFilesVerified': len(seals),
            'identity': result['identity'], 'key': result['key'], 'recounted': counts,
            'p99Ms': p99, 'additionalLatencyQuantilesMs': {
                str(probability): latencies[math.ceil(probability * len(latencies)) - 1] if latencies else None
                for probability in (.999, .9999)},
            'lossRate': 1 - len(completed & delivered) / len(completed) if completed else 0,
            'differences': differences, 'duplicateAttemptIds': attempt_duplicates,
            'duplicateOutcomes': outcome_duplicates, 'unsettled': len(unique - completed - failed),
            'perPublisher': dict(per_publisher), 'completedByElapsedSecond': dict(sorted(publish_bins.items())),
            'deliveredByElapsedSecond': dict(sorted(delivery_bins.items())), 'lastDeliveryElapsedSeconds': last_delivery,
            'drainReason': result['observation']['drainReason'], 'drainSeconds': result['observation']['drainSeconds']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True)
    parser.add_argument('--expected-sha', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = audit_archive(args.archive, args.expected_sha)
    pathlib.Path(args.output).write_text(json.dumps(result, indent=2), encoding='utf-8')
    if result['differences'] or result['duplicateAttemptIds'] or result['duplicateOutcomes'] or result['unsettled']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
