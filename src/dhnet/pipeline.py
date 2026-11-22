"""File-based workflow: read layers, generate the network, size the pipes, write results."""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import networkx as nx
import pandas as pd
from shapely.geometry import LineString

from .routing import NetworkResult, build_street_graph, iterative_closest_facility, snap_points
from .sizing import DesignParameters, size_network


@dataclass
class Result:
    graph: nx.Graph
    source: object
    buildings: dict
    network: NetworkResult
    pipes: gpd.GeoDataFrame          # one row per pipe segment, with DN
    naive_main_length: float          # shortest-path-tree baseline [m]

    @property
    def main_length(self) -> float:
        return float(self.pipes.loc[self.pipes.pipe_type == "main", "length_m"].sum())

    @property
    def service_length(self) -> float:
        return float(self.pipes.loc[self.pipes.pipe_type == "service", "length_m"].sum())


def run(streets_path, buildings_path, source_path, *, load_field="peak_kw",
        id_field="bid", where: str | None = "connect == 1", from_network=False,
        params: DesignParameters | None = None, snap_distance=500.0) -> Result:
    streets = gpd.read_file(streets_path)
    buildings = gpd.read_file(buildings_path).to_crs(streets.crs)
    source = gpd.read_file(source_path).to_crs(streets.crs)
    if not streets.crs or not streets.crs.is_projected:
        raise ValueError("streets must be in a projected (metric) CRS")
    if where:
        try:
            buildings = buildings.query(where)
        except Exception as exc:
            raise ValueError(f"invalid filter expression: {where!r}") from exc

    g = build_street_graph(streets.geometry.explode(index_parts=False))
    src = snap_points(g, {"__source__": source.geometry.iloc[0]}, snap_distance)["__source__"]
    bnodes = snap_points(g, {getattr(r, id_field): r.geometry.centroid
                             for r in buildings.itertuples()}, snap_distance)

    net = iterative_closest_facility(g, src, bnodes, from_network=from_network)
    loads = dict(zip(buildings[id_field], buildings[load_field]))
    sized = size_network(g, net.edges, src, bnodes, loads, params)

    rows = []
    for e, el in sized.items():
        u, v = tuple(e)
        geom = LineString([(g.nodes[u]["x"], g.nodes[u]["y"]), (g.nodes[v]["x"], g.nodes[v]["y"])])
        rows.append({
            "pipe_type": ("plant" if src in (u, v) else
                          "service" if g[u][v].get("service") else "main"),
            "length_m": round(geom.length, 2),
            "consumers": el.consumers,
            "conn_kw": round(el.connected_kw, 1),
            "gzf": round(el.gzf, 3),
            "design_kw": round(el.design_kw, 1),
            "dn": el.dn,
            "geometry": geom,
        })
    pipes = gpd.GeoDataFrame(rows, crs=streets.crs)

    # baseline: every building routed independently to the source (one-shot Closest Facility)
    _, paths = nx.single_source_dijkstra(g, src, weight="length")
    spt = set()
    for n in bnodes.values():
        p = paths.get(n)
        if p:
            spt |= {frozenset(x) for x in zip(p[:-1], p[1:])}
    naive_main = sum(g[u][v]["length"] for u, v in (tuple(x) for x in spt) if not g[u][v].get("service"))

    return Result(g, src, bnodes, net, pipes, naive_main)


def dissolve_by_dn(pipes: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Merge consecutive segments with the same pipe type and DN (like ArcGIS Dissolve)."""
    out = pipes.dissolve(by=["pipe_type", "dn"], aggfunc={"length_m": "sum"}, as_index=False)
    out["geometry"] = out.geometry.line_merge()
    return out


def summary(res: Result) -> pd.DataFrame:
    """Pipe length per DN (main + service), like a bill of quantities."""
    t = (res.pipes.groupby(["dn", "pipe_type"])["length_m"].sum().unstack(fill_value=0)
         .reindex(columns=["main", "service", "plant"], fill_value=0).round(0))
    t["total"] = t.sum(axis=1)
    return t
