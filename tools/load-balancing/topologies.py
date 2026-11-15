"""Draw the exported model networks; validate physical edges before rendering."""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import study


def verify_graph(path, options):
    ns = {"g": "http://graphml.graphdrawing.org/xmlns"}
    root = ET.parse(path).getroot()
    nodes = {node.attrib["id"]: {value.attrib["key"]: value.text for value in node.findall("g:data", ns)}
             for node in root.findall(".//g:node", ns)}
    networks = {key: value["label"] for key, value in nodes.items() if value["type"] == "Network"}
    expected_names = {"clients"} | {f"backend{b}-hop{hop}" for b in range(options["backendCount"]) for hop in range(1, options["networkLength"] + 1)}
    if set(networks.values()) != expected_names:
        raise ValueError("GraphML networks differ from the resolved experiment.")
    expected_edges = set()
    for backend in range(options["backendCount"]):
        previous = "clients"
        for hop in range(1, options["networkLength"] + 1):
            current = f"backend{backend}-hop{hop}"
            expected_edges.add(frozenset((previous, current)))
            previous = current
    physical_edges = set()
    attachments = {}
    for edge in root.findall(".//g:edge", ns):
        source, target = edge.attrib["source"], edge.attrib["target"]
        if source in networks and target in networks:
            physical_edges.add(frozenset((networks[source], networks[target])))
        elif source in networks or target in networks:
            leaf, network = (target, source) if source in networks else (source, target)
            attachments.setdefault(leaf, set()).add(networks[network])
        else:
            raise ValueError("Unexpected direct leaf-to-leaf physical edge.")
    if physical_edges != expected_edges:
        raise ValueError("GraphML physical paths differ from the intended topology.")
    publishers = [key for key, value in nodes.items() if value["type"] == "Client" and value["label"].startswith("publisher")]
    receivers = [key for key, value in nodes.items() if value["type"] == "Client" and value["label"].startswith("receiver")]
    brokers = [key for key, value in nodes.items() if value["type"] == "Server"]
    if len(nodes) != len(networks) + len(publishers) + len(receivers) + len(brokers):
        raise ValueError("Unexpected node roles in the physical graph.")
    if len(publishers) != options["totalPublishers"] or len(receivers) != options["backendCount"] or len(brokers) != options["backendCount"]:
        raise ValueError("GraphML client/server population mismatch.")
    for client in publishers + receivers:
        if attachments.get(client) != {"clients"}:
            raise ValueError("All publishers and receivers must belong to the common client segment.")
    for broker in brokers:
        index = int(nodes[broker]["label"].removeprefix("broker"))
        if attachments.get(broker) != {f"backend{index}-hop{options['networkLength']}"}:
            raise ValueError("Broker belongs to an unexpected backend segment.")
    return {"networks": sorted(networks.values()), "networkLinks": [sorted(edge) for edge in sorted(physical_edges, key=lambda e: sorted(e))],
            "publishers": len(publishers), "receivers": len(receivers), "brokers": len(brokers), "allClientAttachmentsVerified": True}


def mermaid(options, counts):
    backend_count = options["backendCount"]
    model = "Идеальная модель" if options["ideal"] else options["brokerType"]
    mapping = "Все publishers → B0" if backend_count == 1 else "D3 / RR / LC: publisher[i] → B[i mod 3]"
    lines = ["flowchart LR", f'  CTRL["Назначение перед CONNECT: {mapping}"]',
             '  subgraph C["Общий сетевой сегмент clients"]', f'    P["Publishers: {options["totalPublishers"]}"]',
             f'    R["Receivers: {backend_count}; Rj подключён к Bj"]', "  end", "  CTRL -.-> P"]
    for backend in range(backend_count):
        lines += [f'  subgraph H{backend}1["backend{backend}-hop1"]', f'    N{backend}1["Сетевой сегмент 1"]', "  end",
                  f'  subgraph H{backend}2["backend{backend}-hop2"]', f'    B{backend}["B{backend}: {model}; N={counts[backend]}"]', "  end",
                  f"  C <--> H{backend}1", f"  H{backend}1 <--> H{backend}2"]
    return "\n".join(lines) + "\n"


def draw(options, counts, target, preview):
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams["svg.hashsalt"] = "mqtt-lb-topology-v1"
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
    backend_count = options["backendCount"]
    if options["networkLength"] != 2 or backend_count not in (1, 3):
        raise ValueError("This study diagram layout requires two hops and one or three backends.")
    fig, ax = plt.subplots(figsize=(12, 6.5), constrained_layout=True)
    ax.set(xlim=(0, 1), ylim=(0, 1))
    ax.axis("off")
    def box(x, y, width, height, text, color, size=10, linestyle="solid"):
        patch = FancyBboxPatch((x, y), width, height, boxstyle="round,pad=0.009", linewidth=1.2,
                               edgecolor="#43566a", facecolor=color, linestyle=linestyle, zorder=2)
        ax.add_patch(patch)
        if text:
            ax.text(x + width / 2, y + height / 2, text, ha="center", va="center", fontsize=size, zorder=3)
    def arrow(start, end, dashed=False):
        ax.add_patch(FancyArrowPatch(start, end, arrowstyle="->" if dashed else "<->", mutation_scale=13,
                                    linestyle="--" if dashed else "-", color="#317f68" if dashed else "#385d85", linewidth=1.5, zorder=4 if dashed else 1))
    model = "Идеальная модель" if options["ideal"] else options["brokerType"]
    ax.set_title(f"MqttY: {model}; брокеров — {backend_count}, publishers — {options['totalPublishers']}", fontsize=14, pad=16)
    box(.03, .23, .28, .49, "", "#eaf1f8")
    ax.text(.17, .69, "Общий сегмент clients", ha="center", va="center", fontsize=11)
    box(.055, .49, .23, .13, f"Publishers: {options['totalPublishers']}\nMQTT-соединение с выбранным Bj", "#ffffff", 9)
    receiver_labels = "R0 ↔ B0" if backend_count == 1 else "R0 ↔ B0; R1 ↔ B1; R2 ↔ B2"
    box(.055, .28, .23, .13, f"Receivers: {backend_count}\n{receiver_labels}", "#ffffff", 9)
    mapping = "Все publishers → B0" if backend_count == 1 else "D3 / RR / LC: i → i mod 3"
    box(.07, .83, .57, .11, f"Политика назначения перед CONNECT\n{mapping}", "#e9f5ed", 10, "dashed")
    arrow((.10, .83), (.046, .56), dashed=True)
    centers = [.52] if backend_count == 1 else [.73, .48, .23]
    for backend, y in enumerate(centers):
        box(.44, y - .065, .17, .13, f"backend{backend}-hop1\nСетевой сегмент 1", "#f1f2f7", 9)
        box(.71, y - .095, .26, .19, "", "#f5eee8")
        ax.text(.84, y + .065, f"backend{backend}-hop2", ha="center", fontsize=9)
        box(.75, y - .055, .18, .09, f"B{backend}: {model}\nN профиля = {counts[backend]}" if not options["ideal"] else f"B{backend}: идеальный\nPublishers = {counts[backend]}", "#ffe3c7", 9)
        arrow((.32, .48), (.43, y))
        arrow((.62, y), (.70, y))
    ax.text(.03, .12, "Сплошные стрелки — двусторонние связи сетевых сегментов. Пунктир — карта до CONNECT.", fontsize=9)
    ax.text(.03, .075, "Receivers находятся в общем клиентском сегменте и не входят в N publishers профиля.", fontsize=9)
    ax.text(.03, .03, "Отдельного TCP proxy и связей подписок между брокерами в этой модели нет.", fontsize=9)
    fig.savefig(target, metadata={"Date": None})
    preview.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(preview, dpi=130)
    plt.close(fig)


def publish(work, reports):
    data = reports / "data/load-balancing-study-v1"
    records = []
    figures = {"demo": [], "mosquitto": []}
    for family, variants in (("demo", ("single48", "roundRobin")), ("mosquitto", ("single16", "single64", "roundRobin"))):
        for variant in variants:
            case_id = f"{family}-r60000-q1-{variant}-s42"
            directory = work / "runs" / case_id
            study.verify_seal(directory)
            manifest = study.read(directory / "manifest.json")
            options = manifest["resolvedOptions"]
            checked = verify_graph(directory / "topology.graphml", options)
            counts = [point["publishers"] for point in manifest["backendProfileLookup"]]
            name = f"topology-{family}-{'pool3' if variant == 'roundRobin' else variant}"
            source = mermaid(options, counts)
            (data / (name + ".mmd")).write_text(source, encoding="utf-8")
            draw(options, counts, data / (name + ".svg"), work / "previews" / (name + ".png"))
            figures[family].append((name, source))
            records.append({"caseId": case_id, "diagram": name + ".svg", "checked": checked,
                            "manifestSha256": study.digest(directory / "manifest.json"), "graphmlSha256": study.digest(directory / "topology.graphml"),
                            "publishersByBackend": counts, "physicalSource": "exported GraphML", "assignmentSource": "resolved manifest"})
    note = "Receivers показаны в общем клиентском сегменте, как в экспортированном графе. Они подключены к своим брокерам через те же два участка. "
    note += "Политика назначения работает перед CONNECT; отдельного TCP proxy в пути пакетов нет. Подписки независимых backend не объединяются. "
    note += "Физические связи, состав узлов и принадлежность сегментам сверены с GraphML; карта publisher → broker взята из manifest.\n\n"
    for family, filename in (("demo", "Load-balancing-demo.md"), ("mosquitto", "Load-balancing-Mosquitto.md")):
        file = reports / filename
        text = file.read_text(encoding="utf-8")
        section = "## Топология брокеров\n\n" + note
        for name, source in figures[family]:
            section += f"![Топология {name}](data/load-balancing-study-v1/{name}.svg)\n\n"
        section += "Исходник диаграммы пула для редактирования:\n\n```mermaid\n" + figures[family][-1][1] + "```\n\n"
        text = text.replace("## Условия и границы вывода", section + "## Условия и границы вывода", 1)
        file.write_text(text, encoding="utf-8")
    study.write(data / "topology-verification.json", records)
    return figures, note
