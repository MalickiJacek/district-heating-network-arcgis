from pathlib import Path

import networkx as nx

from dhnet.pipeline import run, summary

DATA = Path(__file__).resolve().parents[1] / "data" / "sample"


def test_sample_town_end_to_end():
    res = run(DATA / "Streets.shp", DATA / "Buildings.shp", DATA / "HeatSource.shp")
    assert not res.network.unreachable
    assert nx.is_tree(nx.Graph([tuple(e) for e in res.network.edges]))
    # iterative method is shorter than routing every building independently
    assert res.main_length < res.naive_main_length
    # every pipe has a DN and the largest DN sits at the plant
    assert res.pipes.dn.notna().all()
    assert res.pipes.loc[res.pipes.design_kw.idxmax(), "pipe_type"] == "plant"
    t = summary(res)
    assert abs(t["total"].sum() - res.pipes.length_m.sum()) < 5


def test_from_network_variant_is_valid():
    res = run(DATA / "Streets.shp", DATA / "Buildings.shp", DATA / "HeatSource.shp", from_network=True)
    assert nx.is_tree(nx.Graph([tuple(e) for e in res.network.edges]))
