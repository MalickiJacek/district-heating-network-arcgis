"""
02_size_pipes.py  –  ArcGIS Pro / ArcPy

Pipe sizing of a generated district heating network.
Python version of the ModelBuilder model "WYMIAROWANIE":

  model (ArcGIS tools)                               this script
  -------------------------------------------------  ---------------------------------------
  Closest Facility on the network, building -> plant  shortest path in the network graph
  Join Field (energy of each building)                load per building from --load-field
  Split Line At Vertices + Dissolve on centroid X/Y   sum of loads per network segment
  with SUM of energy
  Calculate Field: DN class (K20/K30/K40/K60)         DN from a capacity table computed from
                                                      pipe hydraulics (see docs/pipe_sizing.md)

New compared to the model: a simultaneity (diversity) factor g(n) is applied to the
summed peak load (Winter et al. 2001), because not all buildings reach their peak at
the same time.

Input: the network from 01_generate_network.py (Solution_HeatNetwork), the building
points and heat source it created, and the buildings layer with a peak load field [kW].
The sizing maths is shared with the open-source package (src/dhnet/sizing.py);
networkx is part of the default ArcGIS Pro Python environment.

Usage (ArcGIS Pro Python):
    propy.bat 02_size_pipes.py --gdb D:\\data\\out\\DistrictHeating.gdb ^
        --buildings D:\\data\\Buildings.shp --load-field peak_kw --delta-t 30
"""

import argparse
import os
import sys

import arcpy
import networkx as nx

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from dhnet.sizing import DesignParameters, capacity_table, select_dn, simultaneity_factor  # noqa: E402

arcpy.env.overwriteOutput = True


def key(pt, ndigits=2):
    return (round(pt.X, ndigits), round(pt.Y, ndigits))


def network_graph(network_fc):
    """Graph of every segment of every route (overlapping routes share segments)."""
    g = nx.Graph()
    ends = {}                                   # building_id -> end vertex of its route
    with arcpy.da.SearchCursor(network_fc, ["building_id", "SHAPE@"]) as cur:
        for bid, line in cur:
            for part in line:
                pts = [p for p in part if p]
                for a, b in zip(pts[:-1], pts[1:]):
                    ka, kb = key(a), key(b)
                    if ka != kb:
                        g.add_edge(ka, kb, length=((a.X - b.X) ** 2 + (a.Y - b.Y) ** 2) ** 0.5)
                if pts:
                    ends[str(bid)] = key(pts[-1])
    return g, ends


def nearest_node(g, geom):
    x, y = geom.firstPoint.X, geom.firstPoint.Y
    return min(g.nodes, key=lambda n: (n[0] - x) ** 2 + (n[1] - y) ** 2)


def size(gdb, buildings, id_field="bid", load_field="peak_kw", params=None):
    params = params or DesignParameters()
    table = capacity_table(params)
    network_fc = os.path.join(gdb, "Solution_HeatNetwork")
    source_fc = os.path.join(gdb, "Heat_Source")

    g, ends = network_graph(network_fc)
    with arcpy.da.SearchCursor(source_fc, ["SHAPE@"]) as cur:
        source = nearest_node(g, next(cur)[0])
    loads = {}
    with arcpy.da.SearchCursor(buildings, [id_field, load_field]) as cur:
        for bid, kw in cur:
            loads[str(bid)] = float(kw or 0)

    # every building -> plant along the network (model: Closest Facility on the solution)
    _, paths = nx.single_source_dijkstra(g, source, weight="length")
    consumers, kw_sum = {}, {}
    for bid, end in ends.items():
        path = paths.get(end)
        if not path:
            arcpy.AddWarning(f"building {bid} not connected to the plant in the network")
            continue
        for a, b in zip(path[:-1], path[1:]):
            e = frozenset((a, b))
            consumers[e] = consumers.get(e, 0) + 1
            kw_sum[e] = kw_sum.get(e, 0.0) + loads.get(bid, 0.0)

    sr = arcpy.Describe(network_fc).spatialReference
    out_fc = arcpy.management.CreateFeatureclass(gdb, "Pipes_Sized", "POLYLINE", spatial_reference=sr)[0]
    arcpy.management.AddFields(out_fc, [["consumers", "LONG"], ["conn_kw", "DOUBLE"], ["gzf", "DOUBLE"],
                                        ["design_kw", "DOUBLE"], ["dn", "SHORT"], ["length_m", "DOUBLE"]])
    with arcpy.da.InsertCursor(out_fc, ["consumers", "conn_kw", "gzf", "design_kw", "dn",
                                        "length_m", "SHAPE@"]) as cur:
        for e, n in consumers.items():
            a, b = tuple(e)
            gzf = simultaneity_factor(n)
            design = kw_sum[e] * gzf
            line = arcpy.Polyline(arcpy.Array([arcpy.Point(*a), arcpy.Point(*b)]), sr)
            cur.insertRow([n, kw_sum[e], gzf, design, select_dn(design, table), line.length, line])

    # merge consecutive segments with the same DN (model: final Dissolve)
    by_dn = os.path.join(gdb, "Pipes_By_DN")
    arcpy.management.Dissolve(out_fc, by_dn, ["dn"], [["length_m", "SUM"], ["design_kw", "MAX"]],
                              multi_part="SINGLE_PART", unsplit_lines="UNSPLIT_LINES")
    arcpy.AddMessage(f"Sized {len(consumers)} segments -> {out_fc}, {by_dn}")
    return out_fc, by_dn


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--gdb", required=True, help="DistrictHeating.gdb created by 01_generate_network.py")
    ap.add_argument("--buildings", required=True)
    ap.add_argument("--id-field", default="bid")
    ap.add_argument("--load-field", default="peak_kw")
    ap.add_argument("--delta-t", type=float, default=30.0)
    ap.add_argument("--max-dp", type=float, default=150.0)
    ap.add_argument("--max-v", type=float, default=3.0)
    a = ap.parse_args()
    size(a.gdb, a.buildings, a.id_field, a.load_field,
         DesignParameters(delta_t_k=a.delta_t, max_pressure_loss_pa_m=a.max_dp, max_velocity_m_s=a.max_v))
