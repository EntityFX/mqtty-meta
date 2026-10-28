"""Strict registry gate for the frozen final network package; raw checks are separate."""
SCIENTIFIC_FILES={'summary.json','histograms.json','publications.json','delivery-receipts.json','transmissions.json','network-evidence.json'}
def validate_registry(plan,matrix,replay):
 def index(document,kind):
  rows=document['rows'];indexed={r['id']:r for r in rows};expected={j['id'] for j in plan['jobs'] if j['kind']==kind}
  if set(indexed)!=expected or len(indexed)!=len(rows):raise ValueError('Incomplete, unexpected or duplicate '+kind+' keys')
  for row in rows:
   if row['technicalError'] is not None or row['returnCode']!=0:raise ValueError('Technical failure: '+row['id'])
   names=set(row['scientificSha256']);required=SCIENTIFIC_FILES-{'network-evidence.json'}
   if not required<=names or not names<=SCIENTIFIC_FILES:raise ValueError('Scientific file set differs: '+row['id'])
   if '.star.' in row['id'] and 'network-evidence.json' not in names:raise ValueError('Star transit evidence absent: '+row['id'])
  return indexed
 originals=index(matrix,'matrix');repeated=index(replay,'replay');jobs={j['id']:j for j in plan['jobs']}
 for key,row in repeated.items():
  original=originals.get(jobs[key]['replayOf'])
  if original is None:raise ValueError('Replay original absent: '+key)
  if row['scientificSha256']!=original['scientificSha256']:raise ValueError('Scientific replay SHA differs: '+key)
  if row['summary']!=original['summary']:raise ValueError('Replay summary differs: '+key)
 return len(repeated)
