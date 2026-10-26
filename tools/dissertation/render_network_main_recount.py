"""Render the audited, incomplete network comparison; never execute simulations."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Mirrored from academic-figure/scripts/cs_palette.py: registered palette names.
CS_PEER_PALETTE = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9"]
CS_SUPPORT_COLORS = {"text": "#333333", "grid": "#E0E0E0"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    source = args.audit / "condition-stats.json"
    stats = json.loads(source.read_text(encoding="utf-8"))
    index = {(r["topology"], r["qos"], r["offeredRps"], r["mode"]): r for r in stats}
    modes = ["single", "relay", "federation"]
    names = ["Центральный", "MqttRelay", "Федерация"]
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "svg.fonttype": "none", "pdf.fonttype": 42,
                         "text.color": CS_SUPPORT_COLORS["text"],
                         "axes.labelcolor": CS_SUPPORT_COLORS["text"]})
    fig, axes = plt.subplots(2, 2, figsize=(9, 6.2))
    for col, topology in enumerate(["line", "ring"]):
        axes[0, col].set_title("Линия" if topology == "line" else "Кольцо")
        for mi, mode in enumerate(modes):
            x = [q + (mi - 1) * 0.13 for q in range(3)]
            for row, metric in enumerate(["deliveryLoss", "receiverP99Ms"]):
                values, lower, upper = [], [], []
                rows = [index[topology, q, 5000, mode] for q in range(3)]
                for r in rows:
                    m = r["metrics"][metric]
                    assert m["n"] == len(r["matchedSeeds"]) and m["mean"] is not None
                    if metric == "deliveryLoss":
                        value, lo, hi = 100 * (1-m["mean"]), 100*(1-m["max"]), 100*(1-m["min"])
                    else:
                        value, lo, hi = m["mean"], m["min"], m["max"]
                    values.append(value)
                    lower.append(value-lo)
                    upper.append(hi-value)
                ax = axes[row, col]
                ax.errorbar(x, values, yerr=[lower, upper], fmt=["o", "s", "^"][mi],
                            color=CS_PEER_PALETTE[mi], capsize=2, ms=5,
                            label=names[mi] if row == col == 0 else None)
                if topology == "ring" and mode == "federation":
                    ax.annotate("n=1", (x[-1], values[-1]), xytext=(-6, 12),
                                textcoords="offset points", ha="right", fontsize=9)
        for row in range(2):
            ax = axes[row, col]
            ax.set_xticks([0, 1, 2], ["QoS 0", "QoS 1", "QoS 2"])
            ax.set_xlim(-0.4, 2.4)
            ax.grid(axis="y", color=CS_SUPPORT_COLORS["grid"], linewidth=0.6)
            ax.set_axisbelow(True)
        axes[0, col].set_ylim(0, 113)
        axes[1, col].set_yscale("log")
        axes[1, col].set_ylim(1, 20000)
    axes[0, 0].set_ylabel("Полнота доставки, %")
    axes[1, 0].set_ylabel("p99 доставки, мс (лог. шкала)")
    fig.suptitle("Идеальная модель: 5000 исходных сообщений/с", fontsize=12)
    fig.legend(*axes[0, 0].get_legend_handles_labels(), loc="lower center", ncol=3,
               bbox_to_anchor=(0.5, 0.035), frameon=False)
    fig.text(0.5, 0.015, "Средние и min–max по общим seed; n=3, кроме кольца/QoS 2: n=1 для всех режимов.",
             ha="center", fontsize=8)
    fig.tight_layout(rect=(0, 0.10, 1, 0.96))
    for ext in ["svg", "pdf", "png"]:
        fig.savefig(args.output / f"comparison-5000.{ext}", dpi=180)
    plt.close(fig)

    table = ["| Топология | QoS | Сообщ./с | Общих seed | Полнота федерации, % | p99 single, мс | p99 Relay, мс | p99 федерации, мс |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for topology in ["line", "ring"]:
        for qos in range(3):
            for rate in [1000, 5000]:
                rows = [index[topology, qos, rate, mode] for mode in modes]
                fed = rows[-1]
                values = ["Линия" if topology == "line" else "Кольцо", str(qos), str(rate),
                          str(len(fed["matchedSeeds"])),
                          f'{100*(1-fed["metrics"]["deliveryLoss"]["mean"]):.3f}']
                values += [f'{r["metrics"]["receiverP99Ms"]["mean"]:.3f}' for r in rows]
                table.append("| " + " | ".join(values).replace(".", ",") + " |")
    (args.output / "Comparison.md").write_text("\n".join(table)+"\n", encoding="utf-8")
    original = args.raw_root / "runs/matrix.line.relay.q0.r1000.w10.s42/attempt-001/output"
    originals = {}
    for kind in ["physical", "logical"]:
        for ext in ["svg", "mmd"]:
            name = f"topology-{kind}.{ext}"
            shutil.copyfile(original / name, args.output / name)
            assert digest(original / name) == digest(args.output / name)
            originals[name] = {"source": str(original / name), "sha256": digest(original / name)}
    manifest = {"sourceStatsSha256": digest(source), "rendererSha256": digest(Path(__file__)),
                "palette": "CS_PEER_PALETTE + CS_SUPPORT_COLORS", "originalTopologies": originals,
                "statistics": "Equally weighted common seeds; min-max, not confidence intervals",
                "files": {p.name: digest(p) for p in sorted(args.output.iterdir()) if p.is_file()}}
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"conditionRows": len(table)-2, "output": str(args.output)}))


if __name__ == "__main__":
    main()
