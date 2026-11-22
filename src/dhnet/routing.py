"""
District heating route generation on a street network.

Reference implementation of the iterative Closest Facility method from
Strzalka, Malicki & Blachowski (2024), Appl. Sci. 14(2), 763, Section 2.3.2:

    connected   = {heat source}
    unconnected = all buildings
    while unconnected:
        find the shortest street route between any connected point and any
        unconnected building (ArcGIS: Closest Facility, Incidents -> Facilities)
        keep only the route with the minimum length, append it to the network
        move the newly connected building to the connected set

In graph theory this is the shortest-path heuristic for the Steiner tree problem
(Takahashi & Matsuyama, 1980). The paper variant measures distances from the
connected *points* (heat source and building connection points); the
``from_network=True`` variant measures from *any vertex of the network built so
far*, which is the classical heuristic and usually gives a shorter network.

The ArcGIS ModelBuilder model re-solves the whole Closest Facility problem in
every iteration (~220 connections/hour in the paper). Here every iteration only
runs Dijkstra from the newly added vertices and keeps a running minimum, so the
same result is produced in seconds.
"""

from __future__ import annotations

import heapq
import itertools
from dataclasses import dataclass, field

import networkx as nx
from shapely.geometry import LineString, Point

# --------------------------------------------------------------------------- #
# street graph
# --------------------------------------------------------------------------- #


def _key(xy, ndigits=3):
    return (round(xy[0], ndigits), round(xy[1], ndigits))


def build_street_graph(lines) -> nx.Graph:
    """
    Undirected graph from street centre lines (iterable of shapely LineStrings
    in a metric CRS). Every vertex of every line becomes a node, every segment
    an edge weighted by its length. Lines must be noded at intersections
    (true for BDOT10k / OSM road networks after a planarize step).
    """
    g = nx.Graph()
    for line in lines:
        coords = list(line.coords)
        for a, b in zip(coords[:-1], coords[1:]):
            ka, kb = _key(a), _key(b)
            if ka == kb:
                continue
            length = Point(a).distance(Point(b))
            if g.has_edge(ka, kb) and g[ka][kb]["length"] <= length:
                continue
            g.add_edge(ka, kb, length=length)
    for n in g.nodes:
        g.nodes[n]["x"], g.nodes[n]["y"] = n
    return g


def _is_street(n) -> bool:
    return not (isinstance(n, tuple) and len(n) == 2 and n[0] == "pt")


def snap_points(g: nx.Graph, points: dict, max_distance: float = 500.0) -> dict:
    """
    Snap points to the nearest street edge (ArcGIS: Add Locations / Snap) and
    add a service-connection edge from the point to the street.

    ``points`` maps id -> shapely Point. Returns id -> graph node of the point
    (``("pt", id)``). Points further than ``max_distance`` are skipped.
    All points are matched against the current street edges first, then each
    street edge is split once at all of its snap positions.
    """
    from shapely import STRtree

    edges = [(u, v) for u, v in g.edges if _is_street(u) and _is_street(v)]
    lines = [LineString([u, v]) for u, v in edges]
    tree = STRtree(lines)

    per_edge: dict = {}
    for pid, pt in points.items():
        i = int(tree.nearest(pt))
        seg = lines[i]
        dist = seg.distance(pt)
        if dist > max_distance:
            continue
        along = seg.project(pt)
        per_edge.setdefault(i, []).append((along, pid, pt, dist))

    out = {}
    for i, items in per_edge.items():
        u, v = edges[i]
        seg = lines[i]
        items.sort(key=lambda t: t[0])
        chain = [u]
        for along, pid, pt, dist in items:
            q = seg.interpolate(along)
            k = _key((q.x, q.y))
            if k != chain[-1]:
                chain.append(k)
                g.add_node(k, x=k[0], y=k[1])
            node = ("pt", pid)
            g.add_node(node, x=pt.x, y=pt.y, kind="point", pid=pid)
            g.add_edge(node, k, length=max(dist, 0.0), service=True)
            out[pid] = node
        if chain[-1] != v:
            chain.append(v)
        if len(chain) > 2 or chain != [u, v]:
            g.remove_edge(u, v)
            for a, b in zip(chain[:-1], chain[1:]):
                if a != b:
                    g.add_edge(a, b, length=Point(a).distance(Point(b)))
    return out


# --------------------------------------------------------------------------- #
# iterative closest facility
# --------------------------------------------------------------------------- #


@dataclass
class NetworkResult:
    edges: set = field(default_factory=set)            # frozenset({u, v}) edges of the network
    order: list = field(default_factory=list)           # building ids in connection order
    route_lengths: list = field(default_factory=list)   # length of the chosen route per iteration [m]
    added_lengths: list = field(default_factory=list)   # new pipe length per iteration [m]
    unreachable: list = field(default_factory=list)

    @property
    def total_length(self) -> float:
        """Length of the generated network (each street segment counted once)."""
        return sum(self.added_lengths)


def iterative_closest_facility(g: nx.Graph, source, buildings: dict,
                               from_network: bool = False) -> NetworkResult:
    """
    Grow a heating network from ``source`` (graph node) to every building
    (``buildings``: id -> graph node), one shortest connection per iteration.

    from_network=False : distance measured from connected points only (paper / ModelBuilder model)
    from_network=True  : distance measured from any vertex of the network built so far
    """
    target_of = {node: bid for bid, node in buildings.items()}
    remaining = set(target_of)
    res = NetworkResult()

    dist: dict = {}      # best known distance from the connected set to every node
    pred: dict = {}      # predecessor on that best path
    heap: list = []      # (distance, tie-breaker, building node) candidates
    counter = itertools.count()

    def relax_from(start_nodes):
        """Dijkstra from newly connected nodes, improving dist/pred in place."""
        pq = [(0.0, next(counter), n) for n in start_nodes]
        for n in start_nodes:
            dist[n] = 0.0
            pred[n] = None
        while pq:
            d, _, u = heapq.heappop(pq)
            if d > dist.get(u, float("inf")):
                continue
            if u in remaining:
                heapq.heappush(heap, (d, next(counter), u))
            for v, attr in g[u].items():
                # service connections of *other* buildings are dead ends:
                # a route may end at a building but never pass through one
                if v != u and g.nodes[v].get("kind") == "point" and v not in remaining:
                    continue
                if g.nodes[u].get("kind") == "point" and u not in start_nodes and u in remaining:
                    continue
                nd = d + attr["length"]
                if nd < dist.get(v, float("inf")) - 1e-9:
                    dist[v] = nd
                    pred[v] = u
                    heapq.heappush(pq, (nd, next(counter), v))

    relax_from([source])
    while remaining:
        # pop the closest still-unconnected building (lazy deletion of stale entries)
        while heap and (heap[0][2] not in remaining or heap[0][0] > dist[heap[0][2]] + 1e-9):
            heapq.heappop(heap)
        if not heap:
            res.unreachable = sorted(target_of[n] for n in remaining)
            break
        d, _, b = heapq.heappop(heap)

        # walk back along predecessors -> new route
        path = [b]
        while pred[path[-1]] is not None:
            path.append(pred[path[-1]])
        new_edges = {frozenset(e) for e in zip(path[:-1], path[1:])} - res.edges
        res.added_lengths.append(sum(g[u][v]["length"] for u, v in (tuple(e) for e in new_edges)))
        res.edges |= new_edges
        res.order.append(target_of[b])
        res.route_lengths.append(d)
        remaining.discard(b)

        new_nodes = path if from_network else [b]
        relax_from(new_nodes)

    return res


def network_lines(g: nx.Graph, res: NetworkResult) -> list[LineString]:
    """Shapely segments of the generated network (for export / plotting)."""
    out = []
    for e in res.edges:
        u, v = tuple(e)
        out.append(LineString([(g.nodes[u]["x"], g.nodes[u]["y"]),
                               (g.nodes[v]["x"], g.nodes[v]["y"])]))
    return out
