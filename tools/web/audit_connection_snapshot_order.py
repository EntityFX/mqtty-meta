"""Check ordering differences without losing time, duplicates or per-client event order."""
from itertools import groupby
import json


def snapshot_groups(events):
    result=[]
    for (time,phase),group in groupby(events,key=lambda row:(row['elapsedSeconds'],row['phase'])):
        clients={}
        for event in group:
            clients.setdefault(event['client'],[]).append(event)
        result.append((time,phase,clients))
    return result


def audit(first,second):
    return {'byteOrderNotRequired':True,'eventsFirst':len(first),'eventsSecond':len(second),
            'sameOrderedSnapshotsAndPerClientEvents':snapshot_groups(first)==snapshot_groups(second),
            'scope':'Separate diagnostic; does not replace the strict byte comparison or science criteria'}


def read(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
