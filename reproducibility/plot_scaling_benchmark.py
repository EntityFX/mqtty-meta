"""Render all paired speedups from independently verified local scaling runs."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence', type=Path, help='Contains nodes-N-summary.json and nodes-N-verification.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    sources = []
    for path in args.evidence.glob('nodes-*-summary.json'):
        count = int(path.name.split('-')[1])
        audit = json.loads(path.with_name('nodes-%d-verification.json' % count).read_text(encoding='utf-8-sig'))
        if audit['attemptsChecked'] != 36:
            raise ValueError('Incomplete independent verification')
        sources.append((count, json.loads(path.read_text(encoding='utf-8-sig'))))
    sources.sort()
    if not sources:
        raise ValueError('No verified summaries')
    fig, axes = plt.subplots(1, 3, figsize=(11, 4.5), sharey=True, dpi=180)
    colors = ['#24539D', '#BF5E1E', '#22745B']
    for index, (ax, color, engine) in enumerate(zip(axes, colors, ('relay', 'federation', 'load-balancer'))):
        medians = []
        for x, (count, summary) in enumerate(sources):
            row = next(r for r in summary['rows'] if r['engine'] == engine)
            if not row['parallelComparisonValid']:
                medians.append(float('nan'))
                ax.text(x, .25, '≠ результаты', ha='center', color='#A12222', fontsize=9)
                continue
            values = row['pairedSpeedups']
            ax.scatter([x + (n - 2) * .035 for n in range(len(values))], values,
                       color=color, alpha=.65, s=25)
            medians.append(row['validatedSpeedup'])
            ax.annotate('%.2f×' % medians[-1], (x, medians[-1]),
                        xytext=(0, 12), textcoords='offset points', ha='center', fontsize=10)
        ax.plot(range(len(sources)), medians, color=color, marker='D', lw=1.5, markersize=6)
        ax.axhline(1, color='#555', ls='--', lw=1)
        ax.set_xticks(range(len(sources)), [str(n) for n, _ in sources])
        ax.set_xlim(-.4, len(sources) - .6)
        ax.set_title(engine, fontsize=12)
        ax.set_xlabel('Брокеры / backend')
        ax.grid(axis='y', alpha=.25)
        ax.spines[['top', 'right']].set_visible(False)
    axes[0].set_ylabel('Время serial / время parallel, ×')
    axes[0].set_ylim(bottom=0)
    fig.suptitle('Локальная i7: эффект увеличения графа при 1000 публикаций/с', fontsize=14)
    fig.legend(handles=[Line2D([0], [0], marker='o', color='#555', linestyle='', label='Пять пар повторов'),
                        Line2D([0], [0], marker='D', color='#555', label='Отношение медиан')],
               loc='lower center', ncol=2, frameon=False, bbox_to_anchor=(.5, .07))
    fig.text(.5, .035, 'Выше 1 — parallel быстрее. Свежий процесс, включая JIT и экспорт; Refresh strategy=0.',
             ha='center', fontsize=9)
    fig.subplots_adjust(left=.075, right=.985, top=.80, bottom=.26, wspace=.13)
    fig.savefig(args.output, facecolor='white')
    plt.close(fig)


if __name__ == '__main__':
    main()
