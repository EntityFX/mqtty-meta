from pathlib import Path
import csv,collections,statistics,hashlib,json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
# Registered academic-figure palette, mirrored from scripts/cs_palette.py.
CS_PEER_PALETTE=['#0072B2','#D55E00','#009E73','#CC79A7','#E69F00','#56B4E9']
CS_SUPPORT_COLORS={'text':'#333333','grid':'#E0E0E0'}
base=Path(__file__).resolve().parent;source=base/'source-metadata/summary-matrix.csv';output=base/'figures';output.mkdir(exist_ok=False)
rows=list(csv.DictReader(source.open(encoding='utf-8'))); assert len(rows)==162
groups=collections.defaultdict(list)
for r in rows:
 parts=r['id'].split('.');groups[(parts[1],parts[2],int(parts[3][1:]),int(parts[4][1:]))].append(r)
assert len(groups)==54 and all(len(v)==3 for v in groups.values())
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'svg.fonttype':'none','pdf.fonttype':42,'text.color':CS_SUPPORT_COLORS['text'],'axes.labelcolor':CS_SUPPORT_COLORS['text']})
plotted=[]
for rate in (1000,5000):
 fig,axes=plt.subplots(2,3,figsize=(11.8,6),sharey='row')
 for col,(top,title) in enumerate([('line','Линия'),('ring','Кольцо'),('star','Звезда с транзитом')]):
  axes[0,col].set_title(title)
  for mi,(mode,label,marker) in enumerate([('single','Один брокер','o'),('relay','MqttRelay','s'),('federation','Федерация','^')]):
   x=[q+(mi-1)*.14 for q in range(3)]
   for row,metric in enumerate(('deliveryLoss','receiverP99Ms')):
    values=[];lower=[];upper=[]
    for q in range(3):
     samples=[float(r[metric]) for r in groups[top,mode,q,rate]]
     if metric=='deliveryLoss':samples=[100*(1-v) for v in samples]
     mean=statistics.mean(samples);values.append(mean);lower.append(mean-min(samples));upper.append(max(samples)-mean);plotted.append({'topology':top,'mode':mode,'qos':q,'rate':rate,'metric':metric,'n':3,'mean':mean,'min':min(samples),'max':max(samples)})
    axes[row,col].errorbar(x,values,yerr=[lower,upper],fmt=marker,color=CS_PEER_PALETTE[mi],capsize=2,ms=5,label=label if row==col==0 else None)
  for row in range(2):
   ax=axes[row,col];ax.set_xticks(range(3),['QoS 0','QoS 1','QoS 2']);ax.set_xlim(-.4,2.4);ax.grid(axis='y',color=CS_SUPPORT_COLORS['grid'],linewidth=.6);ax.set_axisbelow(True)
  axes[0,col].set_ylim(0,108);axes[1,col].set_yscale('log');axes[1,col].set_ylim(1,20000)
 axes[0,0].set_ylabel('Полнота доставки, %');axes[1,0].set_ylabel('p99 доставки, мс (лог. шкала)')
 fig.suptitle(f'Идеальная модель: {rate} исходных сообщений/с',fontsize=12)
 fig.legend(*axes[0,0].get_legend_handles_labels(),loc='lower center',ncol=3,bbox_to_anchor=(.5,.04),frameon=False)
 fig.text(.5,.018,'Среднее и min–max трёх seed. p99 только для полученных сообщений; не натурные измерения.',ha='center',fontsize=8)
 fig.tight_layout(rect=(0,.12,1,.95))
 for ext in ('svg','pdf','png'):fig.savefig(output/f'comparison-{rate}.{ext}',dpi=160)
 plt.close(fig)
manifest={'source':str(source),'sourceSha256':hashlib.file_digest(source.open('rb'),'sha256').hexdigest(),'rendererSha256':hashlib.file_digest(Path(__file__).open('rb'),'sha256').hexdigest(),'palette':'CS_PEER_PALETTE + CS_SUPPORT_COLORS','contract':{'claim':'Delivery completeness and latency differ under finite model queues','backend':'Matplotlib','statistic':'Equally weighted three seeds; min-max, not confidence intervals','exports':['SVG','PDF','PNG'],'rawAcceptanceRequired':True},'plotted':plotted,'files':{p.name:hashlib.file_digest(p.open('rb'),'sha256').hexdigest() for p in output.iterdir()}}
(output/'manifest.json').write_bytes((json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode());print('Rendered 2 figures; plotted entries',len(plotted),flush=True)
