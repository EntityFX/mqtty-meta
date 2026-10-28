from pathlib import Path
import csv,json,hashlib,statistics,collections
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
CS_PEER_PALETTE=['#0072B2','#D55E00','#009E73','#CC79A7','#E69F00','#56B4E9']
CS_SUPPORT_COLORS={'text':'#333333','grid':'#E0E0E0'}
base=Path(__file__).resolve().parent;out=base/'cost-figures';out.mkdir(exist_ok=True)
summary=base/'source-metadata/summary-matrix.csv';transit=base/'transit.csv'
rows=list(csv.DictReader(summary.open(encoding='utf-8')));trans=list(csv.DictReader(transit.open(encoding='utf-8')))
assert len(rows)==162 and len(trans)==54
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'svg.fonttype':'none','pdf.fonttype':42,'text.color':CS_SUPPORT_COLORS['text'],'axes.labelcolor':CS_SUPPORT_COLORS['text']})
modes=[('single','Один брокер','o'),('relay','MqttRelay','s'),('federation','Федерация','^')];plotted=[]
def points(ax,samples,mode,label,marker,mi,kind):
 means=[statistics.mean(v) for v in samples];lo=[m-min(v) for m,v in zip(means,samples)];hi=[max(v)-m for m,v in zip(means,samples)];ax.errorbar([q+(mi-1)*.14 for q in range(3)],means,yerr=[lo,hi],fmt=marker,color=CS_PEER_PALETTE[mi],capsize=2,ms=5,label=label)
 for q,v in enumerate(samples):assert len(v)==3;plotted.append({'panel':kind,'mode':mode,'qos':q,'n':3,'mean':statistics.mean(v),'min':min(v),'max':max(v)})
def decorate(axes):
 for ax in axes.flat:ax.set_xticks(range(3),['QoS 0','QoS 1','QoS 2']);ax.set_xlim(-.4,2.4);ax.grid(axis='y',color=CS_SUPPORT_COLORS['grid'],linewidth=.6);ax.set_axisbelow(True);ax.set_ylim(bottom=0)
def save(fig,name,handles):
 fig.legend(*handles,loc='lower center',ncol=3,bbox_to_anchor=(.5,.04),frameon=False);fig.text(.5,.015,'Среднее и min–max трёх seed. Меньше обмена при недоставке не означает лучшую эффективность.',ha='center',fontsize=8);fig.tight_layout(rect=(0,.14,1,.94))
 for ext in ('svg','pdf','png'):fig.savefig(out/f'{name}.{ext}',dpi=160)
 plt.close(fig)
fig,axes=plt.subplots(2,3,figsize=(11.8,6),sharey='row')
for col,(top,title) in enumerate([('line','Линия'),('ring','Кольцо'),('star','Звезда с транзитом')]):
 axes[0,col].set_title(title)
 for mi,(mode,label,marker) in enumerate(modes):
  selected=[[r for r in rows if r['id'].startswith(f'matrix.{top}.{mode}.q{q}.r5000.')] for q in range(3)]
  points(axes[0,col],[[float(r['forwardAttempts'])/float(r['apiAttempts']) for r in rs] for rs in selected],mode,label,marker,mi,top+' exports/attempt')
  points(axes[1,col],[[float(r['forwardTopicPayloadBytes'])/1024**2 for r in rs] for rs in selected],mode,label,marker,mi,top+' forwarded MiB')
decorate(axes);axes[0,0].set_ylabel('Экспорты / попытки');axes[1,0].set_ylabel('Пересылка, МиБ');
for ax in axes[0,:]:ax.set_ylim(bottom=-.4)
for ax in axes[1,:]:ax.set_ylim(bottom=-.8)
fig.suptitle('Логическая пересылка: 5000 исходных сообщений/с');save(fig,'forwarding-5000',axes[0,0].get_legend_handles_labels())
fig,axes=plt.subplots(1,2,figsize=(9,4.4))
for mi,(mode,label,marker) in enumerate(modes):
 selected=[[r for r in trans if r['id'].startswith(f'matrix.star.{mode}.q{q}.r5000.')] for q in range(3)]
 points(axes[0],[[int(r['inboundBytes'])/1024**2 for r in rs] for rs in selected],mode,label,marker,mi,'transit inbound MiB')
 points(axes[1],[[int(r['transferredPackets']) for r in rs] for rs in selected],mode,label,marker,mi,'transit packets')
decorate(axes);axes[0].set_ylabel('Входящие байты, МиБ');axes[1].set_ylabel('Переданные модельные пакеты');fig.suptitle('Транзит физической звезды: подача + ожидание, 5000 сообщений/с');save(fig,'transit-5000',axes[0].get_legend_handles_labels())
manifest={'sourceSha256':{p.name:hashlib.file_digest(p.open('rb'),'sha256').hexdigest() for p in (summary,transit)},'rendererSha256':hashlib.file_digest(Path(__file__).open('rb'),'sha256').hexdigest(),'palette':'CS_PEER_PALETTE + CS_SUPPORT_COLORS','contract':{'layout':'Report multi-panel figures, SVG/PDF/PNG','statistic':'Equal seed weights, min-max; 3 per group','bytes':'Forwarded topic+payload separate from physical-model transit counters','claim':'Observed exchange cost; losses preclude interpreting lower traffic as efficiency'},'plotted':plotted,'files':{p.name:hashlib.file_digest(p.open('rb'),'sha256').hexdigest() for p in out.iterdir() if p.name!='manifest.json'}}
(out/'manifest.json').write_bytes((json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode('utf-8'));print('Cost figures rendered',len(plotted))
