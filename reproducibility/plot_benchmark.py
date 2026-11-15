"""Plot paired process-time ratios from verified benchmark summaries (matplotlib)."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--windows', type=Path, required=True)
    parser.add_argument('--elbrus', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    fig, ax = plt.subplots(figsize=(10.5, 5.5), dpi=180)
    fig.patch.set_facecolor('white'); ax.set_facecolor('white')
    sources = [('Windows / Xeon', args.windows, '#24539D', -.14, 'o'),
               ('Linux / Эльбрус', args.elbrus, '#BF5E1E', .14, 's')]
    for label, path, color, offset, marker in sources:
        summary = json.loads(path.read_text(encoding='utf-8-sig'))
        for index, row in enumerate(summary['rows']):
            if not row['parallelComparisonValid']:
                raise ValueError('Unverified parallel comparison')
            values = row['pairedSpeedups']
            ax.plot([min(values), max(values)], [index + offset] * 2, color=color, lw=1, alpha=.5)
            ax.scatter(values, [index + offset + (n - 2) * .022 for n in range(5)],
                       color=color, marker=marker, s=34, alpha=.8, edgecolors='white', linewidths=.4, zorder=3)
            median_ratio = row['validatedSpeedup']
            ax.scatter([median_ratio], [index + offset], color=color, marker='D', s=90,
                       edgecolors='black', linewidths=.6, zorder=4)
            ax.annotate(f'{median_ratio:.3f}×', (median_ratio, index + offset),
                        xytext=(5, -20 if offset > 0 else 13), textcoords='offset points',
                        fontsize=10, color=color, fontweight='bold')
    ax.axvline(1, color='#555555', ls='--', lw=1)
    ax.set_xlim(.65, 1.33); ax.set_ylim(-.55, 2.55); ax.invert_yaxis()
    ax.set_yticks([0, 1, 2], ['Relay', 'Federation', 'Load-balancer'])
    ax.set_xlabel('Время serial / время parallel, ×', fontsize=11)
    ax.set_title('Параллельное исполнение: эффект зависит от среды и сети',
                 loc='left', fontsize=14, fontweight='bold', pad=22)
    ax.grid(axis='x', color='#dddddd', lw=.6); ax.set_axisbelow(True)
    ax.spines[['top', 'right', 'left']].set_visible(False)
    ax.legend(handles=[Line2D([0], [0], marker=marker, color=color, linestyle='', label=label, markersize=7)
                       for label, _, color, _, marker in sources], loc='lower right', frameon=False)
    fig.text(.11, .065, 'Точки — пять пар учётных повторов; ромб — отношение медиан времени.\n'
             'Правее 1 — parallel быстрее; левее 1 — медленнее. MqttY 40640a3, Refresh strategy=0.',
             fontsize=9, color='#444444')
    fig.subplots_adjust(left=.15, right=.97, top=.83, bottom=.20)
    fig.savefig(args.output, facecolor='white')
    plt.close(fig)


if __name__ == '__main__':
    main()
