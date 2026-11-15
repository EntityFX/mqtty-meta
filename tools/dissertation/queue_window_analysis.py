"""Independent finite-horizon ID/receiver recount, including censored runs."""
from bisect import bisect_right
import math


def delivery_window(publications,receipts,receiver_sites,measurement_start,cutoff,tick_seconds):
    if cutoff<measurement_start or tick_seconds<=0 or len(set(receiver_sites))!=len(receiver_sites):
        raise ValueError('Invalid observation window or receivers')
    all_pubs={p['id']:p for p in publications}
    if len(all_pubs)!=len(publications):raise ValueError('Duplicate source ID')
    cohort={i:p for i,p in all_pubs.items() if p['measurement']}
    first={};duplicates=0
    for r in receipts:
        p=all_pubs[r['publicationId']]
        if r['tick']<p['tick']:raise ValueError('Delivery precedes creation')
        if not p['measurement'] or r['tick']>cutoff:continue
        if r['receiver'] not in receiver_sites:raise ValueError('Unexpected receiver')
        pair=(p['id'],r['receiver'])
        if pair in first:duplicates+=1
        first[pair]=min(first.get(pair,r['tick']),r['tick'])
    expected=len(cohort)*len(receiver_sites)
    stamps=sorted(first.values())
    latencies=sorted(stamp-cohort[pair[0]]['tick'] for pair,stamp in first.items())
    duration=(cutoff-measurement_start)*tick_seconds
    sample_ticks=max(1,round(.1/tick_seconds))
    samples=list(range(measurement_start,cutoff+1,sample_ticks))
    if not samples or samples[-1]!=cutoff:samples.append(cutoff)
    curve=[dict(seconds=(tick-measurement_start)*tick_seconds,
                uniquePairs=bisect_right(stamps,tick),expectedPairs=expected) for tick in samples]
    return dict(sourcePublications=len(cohort),expectedPairs=expected,uniquePairs=len(first),duplicateReceipts=duplicates,
        notObservedFraction=1-len(first)/expected if expected else None,
        p99ReceivedMs=latencies[math.ceil(.99*len(latencies))-1]*tick_seconds*1000 if latencies else None,
        observationSeconds=duration,uniqueInLastModelSecond=len(stamps)-bisect_right(stamps,cutoff-round(1/tick_seconds)),
        latestUniqueTick=stamps[-1] if stamps else None,curve=curve)
