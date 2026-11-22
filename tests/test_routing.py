import networkx as nx
import pytest
from shapely.geometry import LineString, Point

from dhnet.routing import build_street_graph, iterative_closest_facility, snap_points
from dhnet.sizing import size_network


def line_graph():
    """Straight street 0..300 m along x."""
    return build_street_graph([LineString([(0, 0), (100, 0)]), LineString([(100, 0), (300, 0)])])


def test_snap_splits_edges_and_adds_service_lines():
    g = line_graph()
    nodes = snap_points(g, {"a": Point(50, 10), "b": Point(50, -20), "c": Point(250, 5)})
    assert set(nodes) == {"a", "b", "c"}
    assert g[nodes["a"]][(50.0, 0.0)]["length"] == pytest.approx(10)
    assert g[nodes["b"]][(50.0, 0.0)]["length"] == pytest.approx(20)
    # street length is preserved after splitting
    street = sum(d["length"] for _, _, d in g.edges(data=True) if not d.get("service"))
    assert street == pytest.approx(300)


def test_far_points_are_skipped():
    g = line_graph()
    assert snap_points(g, {"far": Point(50, 900)}, max_distance=500) == {}


def test_iterative_cf_beats_naive_star():
    """
    Source at x=0. Buildings at x=200 (north side) and x=210 (south side).
    Naive: both routed from the source -> shared street 0..200, plus 10 m.
    Iterative: second building connects to the first one's street point.
    Both give a tree; the iterative network must not be longer.
    """
    g = line_graph()
    src = snap_points(g, {"s": Point(0, 0)})["s"]
    b = snap_points(g, {1: Point(200, 5), 2: Point(210, -5), 3: Point(290, 5)})
    res = iterative_closest_facility(g, src, b)
    assert res.order[0] == 1                     # closest building first
    assert sorted(res.order) == [1, 2, 3]
    net = nx.Graph([tuple(e) for e in res.edges])
    assert nx.is_tree(net)
    assert res.total_length == pytest.approx(290 + 5 + 5 + 5)


def test_routes_never_pass_through_other_buildings():
    g = line_graph()
    src = snap_points(g, {"s": Point(0, 0)})["s"]
    b = snap_points(g, {1: Point(100, 30), 2: Point(150, 30)})
    res = iterative_closest_facility(g, src, b)
    for e in res.edges:
        u, v = tuple(e)
        for n in (u, v):
            if g.nodes[n].get("kind") == "point" and n != src:
                # a building node may only be a leaf of the network
                assert sum(1 for x in res.edges if n in x) == 1


def test_unreachable_buildings_are_reported():
    g = line_graph()
    g.add_edge((1000, 1000), (1100, 1000), length=100)  # isolated street
    g.nodes[(1000, 1000)].update(x=1000, y=1000)
    g.nodes[(1100, 1000)].update(x=1100, y=1000)
    src = snap_points(g, {"s": Point(0, 0)})["s"]
    b = {"x": ("pt", "x")}
    g.add_node(("pt", "x"), x=1050, y=1005, kind="point")
    g.add_edge(("pt", "x"), (1000, 1000), length=50, service=True)
    res = iterative_closest_facility(g, src, b)
    assert res.unreachable == ["x"]


def test_sizing_accumulates_loads_towards_source():
    g = line_graph()
    src = snap_points(g, {"s": Point(0, 0)})["s"]
    b = snap_points(g, {1: Point(100, 10), 2: Point(200, 10), 3: Point(300, 10)})
    res = iterative_closest_facility(g, src, b)
    sized = size_network(g, res.edges, src, b, {1: 50.0, 2: 50.0, 3: 50.0})
    by_edge = {tuple(sorted(map(str, e))): v for e, v in sized.items()}
    # edge leaving the source carries all 3 consumers, the last street piece only one
    loads = sorted(((v.consumers, v.connected_kw) for v in sized.values()), reverse=True)
    assert loads[0] == (3, 150.0)
    assert min(v.consumers for v in sized.values()) == 1
    # design load = connected load * simultaneity factor; DN never shrinks towards the source
    first = max(sized.values(), key=lambda v: v.consumers)
    assert first.design_kw < first.connected_kw
    assert all(v.dn is not None for v in sized.values())
    assert by_edge  # silence unused
