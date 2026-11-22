"""Render README figures from the synthetic sample town.  Usage: python scripts/make_figures.py"""

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import geopandas as gpd  # noqa: E402

from dhnet.pipeline import run  # noqa: E402

DATA = ROOT / "data" / "sample"
IMG = ROOT / "docs" / "img"
IMG.mkdir(parents=True, exist_ok=True)


def map_figure():
    res = run(DATA / "Streets.shp", DATA / "Buildings.shp", DATA / "HeatSource.shp")
    streets = gpd.read_file(DATA / "Streets.shp")
    b = gpd.read_file(DATA / "Buildings.shp")
    src = gpd.read_file(DATA / "HeatSource.shp")

    fig, ax = plt.subplots(figsize=(11, 8.5), dpi=130)
    streets.plot(ax=ax, color="#d9d9d9", linewidth=5, zorder=1)
    b[b.connect == 0].plot(ax=ax, color="#ececec", edgecolor="#c8c8c8", linewidth=0.3, zorder=2)
    b[b.connect == 1].plot(ax=ax, color="#f4b183", edgecolor="#c55a11", linewidth=0.3, zorder=2)

    pipes = res.pipes.copy()
    dns = sorted(pipes.dn.dropna().unique())
    cmap = plt.get_cmap("viridis_r")
    norm = {dn: i / max(len(dns) - 1, 1) for i, dn in enumerate(dns)}
    for dn in dns:
        part = pipes[pipes.dn == dn]
        w = 0.6 + 4.4 * norm[dn]
        part.plot(ax=ax, color=cmap(norm[dn]), linewidth=w, zorder=3)
    src.plot(ax=ax, color="#c00000", markersize=160, marker="s", zorder=5)

    handles = [Line2D([], [], color=cmap(norm[dn]), linewidth=0.6 + 4.4 * norm[dn], label=f"DN {int(dn)}")
               for dn in dns]
    handles += [Line2D([], [], marker="s", color="#c00000", ls="", markersize=10, label="heat plant"),
                Line2D([], [], marker="s", color="#f4b183", ls="", markersize=9, label="building to connect"),
                Line2D([], [], marker="s", color="#e0e0e0", ls="", markersize=9, label="other building")]
    ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(1.0, 1.0), frameon=False, fontsize=9)
    ax.set_title(f"Generated and sized district heating network – synthetic town\n"
                 f"{len(res.network.order)} buildings · {res.main_length / 1000:.1f} km main pipes · "
                 f"{res.service_length / 1000:.1f} km service connections · ΔT = 30 K", fontsize=11)
    ax.set_axis_off()
    ax.set_aspect("equal")
    fig.tight_layout()
    fig.savefig(IMG / "network_dn_map.png")
    plt.close(fig)
    return res


def comparison_figure():
    import tempfile

    b = gpd.read_file(DATA / "Buildings.shp")
    rng = np.random.default_rng(1)
    rates = [1.0, 0.5, 0.3, 0.15]
    naive, iterative, n_b = [], [], []
    with tempfile.TemporaryDirectory() as tmp:
        for rate in rates:
            sel = b[(b.kind != "single-family") | (rng.random(len(b)) < rate)]
            path = Path(tmp) / f"b_{rate}.shp"
            sel.to_file(path)
            r = run(DATA / "Streets.shp", path, DATA / "HeatSource.shp", where=None)
            naive.append(r.naive_main_length / 1000)
            iterative.append(r.main_length / 1000)
            n_b.append(len(sel))
    fig, ax = plt.subplots(figsize=(7.5, 4.2), dpi=130)
    x = np.arange(len(rates))
    ax.bar(x - 0.2, naive, 0.4, color="#bdc1c6", label="Each building → plant (shortest path tree)")
    ax.bar(x + 0.2, iterative, 0.4, color="#2e75b6", label="Iterative Closest Facility (paper method)")
    for i, (a, c) in enumerate(zip(naive, iterative)):
        ax.text(i + 0.2, c + 0.15, f"−{1 - c / a:.0%}", ha="center", fontsize=8)
    ax.set_xticks(x, [f"{int(r * 100)} %\n({n} bldg)" for r, n in zip(rates, n_b)])
    ax.set_xlabel("Share of single-family houses connected")
    ax.set_ylabel("Main pipe length [km]")
    ax.set_ylim(0, max(naive) * 1.35)
    ax.legend(frameon=False, fontsize=8, loc="upper right")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(IMG / "iterative_vs_naive.png")
    plt.close(fig)
    return list(zip(rates, n_b, naive, iterative))


def capacity_figure():
    from dhnet.sizing import DesignParameters, capacity_table
    fig, ax = plt.subplots(figsize=(7.5, 4.2), dpi=130)
    for dt, c in zip((20, 30, 40, 60), ("#9ecae1", "#4292c6", "#2171b5", "#08306b")):
        t = capacity_table(DesignParameters(delta_t_k=dt))
        ax.plot([dn for dn, _ in t], [kw for _, kw in t], marker="o", color=c, label=f"ΔT = {dt} K")
    ax.set_yscale("log")
    ax.set_xlabel("Nominal diameter DN")
    ax.set_ylabel("Max thermal capacity [kW]")
    ax.set_title("Pipe capacity (R ≤ 150 Pa/m, v ≤ 3 m/s)", fontsize=10)
    ax.grid(alpha=0.3, which="both")
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(IMG / "pipe_capacity.png")
    plt.close(fig)


if __name__ == "__main__":
    res = map_figure()
    rows = comparison_figure()
    capacity_figure()
    print(f"main {res.main_length:.0f} m, naive {res.naive_main_length:.0f} m")
    for r in rows:
        print("rate %.2f bldg %d naive %.2f km iterative %.2f km" % r)
