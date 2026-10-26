"""Finite abstract checks of one-ID flooding; not an MQTT simulation."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path


def connected_graphs(n):
    candidates=list(itertools.combinations(range(n),2))
    for mask in range(1<<len(candidates)):
        edges=[e for i,e in enumerate(candidates) if mask & (1<<i)]
        reached={0}
        while True:
            more={b for a,b in edges if a in reached}|{a for a,b in edges if b in reached}
            if more<=reached:break
            reached|=more
        if len(reached)==n:yield edges


def initial(origin,hops):return (0,((-1,origin,hops),),0,0,0)


def advance(state,position,edges):
    seen,pending,forwards,suppressed,drops=state
    sender,target,remaining=pending[position]
    pending=list(pending[:position]+pending[position+1:])
    if seen & (1<<target):return seen,tuple(pending),forwards,suppressed+1,drops
    seen|=1<<target
    neighbors=sorted({b for a,b in edges if a==target}|{a for a,b in edges if b==target})
    for next_node in neighbors:
        if next_node==sender:continue
        if remaining==0:drops+=1
        else:
            pending.append((target,next_node,remaining-1));forwards+=1
    return seen,tuple(sorted(pending)),forwards,suppressed,drops


def execute_order(n,edges,origin,hops,prefix):
    state=initial(origin,hops)
    for sender,target in prefix:
        sender=-1 if sender is None else sender
        position=next(i for i,event in enumerate(state[1]) if event[:2]==(sender,target))
        state=advance(state,position,edges)
    while state[1]:state=advance(state,0,edges)
    return state[0],state[2],state[3],state[4]


def explore(n,edges,origin,hops):
    todo=[initial(origin,hops)];visited=set();outcomes=set()
    while todo:
        state=todo.pop()
        if state in visited:continue
        visited.add(state)
        if not state[1]:outcomes.add((state[0],state[2],state[3],state[4]));continue
        todo.extend(advance(state,i,edges) for i in range(len(state[1])))
    return outcomes,len(visited)


def verify(output,stats_path):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    graphs=origins=states=terminal_checks=0
    for n in range(1,5):
        for edges in connected_graphs(n):
            graphs+=1
            for origin in range(n):
                origins+=1
                for hops in sorted({n-1,n}):
                    outcomes,count=explore(n,edges,origin,hops);states+=count
                    for seen,forwards,suppressed,drops in outcomes:
                        assert seen==(1<<n)-1
                        assert forwards<=2*len(edges)
                        assert suppressed==forwards-(n-1)
                        if hops>=n:
                            assert forwards==2*len(edges)-(n-1) and drops==0
                        terminal_checks+=1
    counterexample=execute_order(4,[(0,1),(0,2),(1,2),(2,3)],0,2,
                                [(None,0),(0,1),(1,2),(0,2)])
    assert counterexample==(7,3,1,2)
    stats_path=Path(stats_path);stats=json.loads(stats_path.read_text(encoding='utf-8'))
    matched=[]
    for r in stats:
        if r['mode']!='federation':continue
        m=r['metrics']
        if any(m[k]['max'] for k in ['deliveryLoss','queueDrops','hopLimitDrops','forwardRejected']):continue
        edges={'line':3,'ring':4}[r['topology']]
        assert m['forwardAttempts']['mean']==m['apiAttempts']['mean']*(2*edges-3)
        assert m['suppressedDuplicates']['mean']==m['forwardAttempts']['mean']-3*m['apiAttempts']['mean']
        matched.append({k:r[k] for k in ['topology','qos','offeredRps','matchedSeeds']})
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    source=Path('mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.Federation.App/Federation')
    result=dict(status='abstractPropertiesChecked',connectedGraphs=graphs,graphOrigins=origins,
                exploredStates=states,terminalChecks=terminal_checks,maximumNodes=4,
                counterexample=dict(nodes=4,edges=[[0,1],[0,2],[1,2],[2,3]],diameter=2,
                                    hopLimit=2,origin=0,deliveredNodes=[0,1,2],missingNodes=[3],
                                    result=list(counterexample)),
                verifiedSavedConditions=matched,
                sourceSha256={str(p):sha(p) for p in [Path(__file__),stats_path,source/'StaticFederator.cs',source/'FloodingPolicy.cs']},
                scope='All abstract event orders for connected labeled simple undirected graphs up to four nodes; no queues, faults or MQTT packets; not proof by testing for arbitrary graph size; analytic proof separate',
                newMqttSimulations=0,linuxContacted=False)
    (output/'verification.json').write_bytes((json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
    print(json.dumps({k:result[k] for k in ['connectedGraphs','graphOrigins','exploredStates','terminalChecks']}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True);p.add_argument('--stats',required=True)
    a=p.parse_args();verify(a.output,a.stats)
