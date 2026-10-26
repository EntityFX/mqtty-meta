"""Render the recorded matched campaign; never generate or rerun measurements."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics

# Mirrored registered CS_PEER_PALETTE / CS_SUPPORT_COLORS from academic-figure.
# Keep names explicit so figures do not depend on a user's local skill installation.
CS_PEER_PALETTE = ("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9")
CS_SUPPORT_COLORS = dict(text="#333333", grid="#E0E0E0", axis="#4D4D4D")


def render(root, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    root, output = Path(root), Path(output)
    program_path = root / "program.json"
    summary_path = root / "attempt-001/summary.json"
    program = json.loads(program_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["completed"] == 40 and summary["status"] == "completed"
    output.mkdir(parents=True, exist_ok=False)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "svg.fonttype": "none",
        "pdf.fonttype": 42, "axes.spines.top": False, "axes.spines.right": False,
        "axes.labelcolor": CS_SUPPORT_COLORS["text"], "text.color": CS_SUPPORT_COLORS["text"]})

    def save(fig, name):
        for extension in ("svg", "pdf", "png"):
            fig.savefig(output / (name + "." + extension), dpi=220, bbox_inches="tight")
        plt.close(fig)

    phase = program["phaseSeconds"]
    names = ("Прогрев", "Подача сообщений", "Ожидание завершения")
    seconds = [phase[k] for k in ("warmup", "measurement", "drain")]
    ticks = [round(value / phase["tick"]) for value in seconds]
    fig, ax = plt.subplots(figsize=(7.0, 2.75))
    for row in (0, 1):
        start = 0
        for i, (name, duration, count) in enumerate(zip(names, seconds, ticks)):
            ax.barh(row, duration, left=start, height=.56, color=CS_PEER_PALETTE[i], alpha=.86)
            label = f"{name}\n{duration:g} с"
            ax.text(start + duration / 2, row, label, va="center", ha="center", color="white", fontsize=8)
            if row == 0:
                ax.text(start + duration / 2, -.46, f"{count:,}\nтиков".replace(",", " "),
                    va="center", ha="center", color=CS_SUPPORT_COLORS["text"], fontsize=8)
            start += duration
    ax.set_yticks([0, 1], ["MqttY", "Реальный брокер"])
    ax.set_xticks([0, seconds[0], seconds[0] + seconds[1], sum(seconds)])
    ax.set_xlim(0, sum(seconds))
    ax.set_xlabel("Заданные фазы, с; начало — старт прогрева")
    ax.set_ylim(-.92, 1.65)
    ax.set_title("Соединения, подписки и натурный RTT проверяются до нагрузки", fontsize=9)
    fig.subplots_adjust(bottom=.23, left=.18, top=.82)
    save(fig, "timeline")

    comparisons = summary["modelComparisons"]
    brokers = list(dict.fromkeys(row["broker"] for row in comparisons))
    fig, axes = plt.subplots(1, 3, figsize=(8.1, 3.1))
    for ax, metric, label, multiplier in zip(axes, ("rps", "p99", "loss"),
            ("Завершения публикаций / с подачи", "p99 публикации, мс", "Недоставка, %"), (1, 1, 100)):
        for i, broker in enumerate(brokers):
            rows = [row for row in comparisons if row["broker"] == broker]
            assert len(rows) == 3
            for field in ("Native", "NativeMin", "NativeMax"):
                assert len({row[metric + field] for row in rows}) == 1
            native = rows[0][metric + "Native"] * multiplier
            model_values = [row[metric + "Model"] * multiplier for row in rows]
            model = statistics.mean(model_values)
            native_min = rows[0][metric + "NativeMin"] * multiplier
            native_max = rows[0][metric + "NativeMax"] * multiplier
            for x, mean, low, high, marker, color in (
                    (i - .12, native, native_min, native_max, "o", CS_PEER_PALETTE[0]),
                    (i + .12, model, min(model_values), max(model_values), "s", CS_PEER_PALETTE[1])):
                ax.errorbar(x, mean, yerr=[[max(0, mean - low)], [max(0, high - mean)]],
                    fmt=marker, color=color, capsize=3, markersize=4, linewidth=1.1)
        ax.set_xticks(range(len(brokers)), brokers, rotation=24, ha="right", fontsize=8)
        ax.set_title(label, fontsize=9)
        ax.set_ylim(bottom=0)
        ax.grid(axis="y", color=CS_SUPPORT_COLORS["grid"], linewidth=.6)
        ax.set_axisbelow(True)
    fig.legend(handles=[Patch(color=CS_PEER_PALETTE[0], label="Натурный контроль, n=3"),
        Patch(color=CS_PEER_PALETTE[1], label="MqttY, три seed")], loc="upper center", ncol=2, frameon=False)
    fig.text(.5, .015, "Точки — средние, отрезки — минимум–максимум. Это не доверительные интервалы.",
        ha="center", fontsize=8)
    fig.subplots_adjust(left=.08, right=.98, bottom=.25, top=.76, wspace=.42)
    save(fig, "comparison")
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    receipt = {"status": "renderedAwaitingVisualInspection", "inputs": {
        "program.json": digest(program_path), "attempt-001/summary.json": digest(summary_path)},
        "phaseTicks": dict(zip(("warmup", "measurement", "drain"), ticks)),
        "nativeReplicates": 3, "modelSeeds": 3, "errorBars": "min-max; not CI",
        "palette": "CS_PEER_PALETTE / CS_SUPPORT_COLORS",
        "files": {p.name: digest(p) for p in sorted(output.iterdir())}}
    (output / "manifest.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "phaseTicks": receipt["phaseTicks"], "figures": 2}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    render(args.root, args.output)
