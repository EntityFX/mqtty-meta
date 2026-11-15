"""Existing evidence only; CS_PEER_PALETTE line chart, no invented completion."""
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

HERE=Path(__file__).resolve().parent/'remaining-result'
# Mirrored verbatim from academic-figure/scripts/cs_palette.py for portability.
CS_PEER_PALETTE=('#0072B2','#D55E00','#009E73','#CC79A7','#E69F00','#56B4E9')
CS_SUPPORT_COLORS={'grid':'#E0E0E0','text_muted':'#666666'}


def main():
    with (HERE/'delivery-curve.csv').open(encoding='utf-8') as stream:
        rows=list(csv.DictReader(stream))
    receipt=json.loads((HERE/'acceptance.json').read_text())
    assert receipt['status']=='partialEvidenceAccepted' and not receipt['completedPairAccepted']
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'svg.fonttype':'none','pdf.fonttype':42})
    fig,ax=plt.subplots(figsize=(7.2,4.4))
    for i,capacity in enumerate((1024,4096)):
        series=[r for r in rows if int(r['queueCapacity'])==capacity]
        x=[float(r['secondsFromMeasurementStart']) for r in series]
        y=[100*float(r['fractionDelivered']) for r in series]
        label='1024 — обмен завершён' if capacity==1024 else '4096 — результат к сроку остановки'
        ax.step(x,y,where='post',label=label,color=CS_PEER_PALETTE[i],linestyle='-' if i==0 else '--',linewidth=2)
        ax.plot(x[-1],y[-1],marker='o' if i==0 else 's',color=CS_PEER_PALETTE[i],clip_on=False)
    ax.axvline(10,color=CS_SUPPORT_COLORS['text_muted'],linestyle=':',linewidth=1)
    ax.text(10.5,43,'Подача закончена',color=CS_SUPPORT_COLORS['text_muted'],fontsize=9)
    final=next(w for w in receipt['windows'] if w['queueCapacity']==4096)
    level=100*final['metrics']['uniqueDeliveries']/final['metrics']['expectedDeliveries']
    ax.annotate('Обмен ещё не завершён',xy=(40,level),xytext=(24,42),fontsize=9,
                arrowprops={'arrowstyle':'->','color':CS_SUPPORT_COLORS['text_muted']})
    ax.set(xlim=(0,40),ylim=(0,46),xlabel='Модельные секунды от начала подачи',
           ylabel='Получено ожидаемых пар, %',title='Доставка при очередях 1024 и 4096')
    ax.grid(axis='y',color=CS_SUPPORT_COLORS['grid'],linewidth=.7)
    ax.legend(loc='upper left',bbox_to_anchor=(.01,.88),frameon=False,fontsize=9)
    ax.spines[['top','right']].set_visible(False)
    fig.text(.5,.02,'50 000 публикаций × 4 получателя; 10 с подачи + 30 с ожидания.\n'
             '4096: частичные данные. Разброс между повторами не оценивался.',ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.10,1,1))
    for extension in ('svg','pdf','png'):
        fig.savefig(HERE/f'delivery-window.{extension}',dpi=180,bbox_inches='tight',metadata={'Creator':'MqttY evidence analysis'})
    # Matplotlib leaves separators at SVG path line ends; normalise only this generated asset.
    svg=HERE/'delivery-window.svg'
    svg.write_text('\n'.join(line.rstrip() for line in svg.read_text(encoding='utf-8').splitlines())+'\n',
                   encoding='utf-8',newline='\n')
    plt.close(fig)


if __name__=='__main__':main()
