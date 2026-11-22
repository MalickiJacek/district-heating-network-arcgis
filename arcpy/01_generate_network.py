"""
01_generate_network.py  –  ArcGIS Pro / ArcPy

Iterative Closest Facility method for generating district heating routes
(Strzalka, Malicki & Blachowski 2024, Appl. Sci. 14(2), 763, Section 2.3.2).

Python version of the ModelBuilder model "PRZEBIEG_Iteration":
    Incidents  = heat source + buildings already connected
    Facilities = buildings not connected yet
    repeat: Solve Closest Facility -> keep the route with the minimum length ->
            append it to the network -> move that building to Incidents
    until Facilities is empty

Difference to the model (same result, much faster): the model re-solves the full
problem in every iteration (paper: ~220 connections / hour). Adding one incident can
only make routes shorter, so here each iteration solves Closest Facility only from the
NEWLY connected point and keeps the best route found so far for every building.

Requirements: ArcGIS Pro 3.x with the Network Analyst extension.
Run from the ArcGIS Pro Python window, a notebook, or:
    "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\scripts\\propy.bat" 01_generate_network.py ^
        --streets D:\\data\\Streets.shp --buildings D:\\data\\Buildings.shp ^
        --source D:\\data\\HeatSource.shp --workspace D:\\data\\out --where "connect = 1"
"""

import argparse
import os
import time

import arcpy

arcpy.env.overwriteOutput = True


# --------------------------------------------------------------------------- #
# setup (your original script, with a metric coordinate system)
# --------------------------------------------------------------------------- #

def prepare(streets, buildings, heat_source, workspace, where=None):
    """File geodatabase, feature dataset, network dataset, building points."""
    gdb = os.path.join(workspace, "DistrictHeating.gdb")
    if not arcpy.Exists(gdb):
        arcpy.management.CreateFileGDB(workspace, "DistrictHeating.gdb")

    # Network distances must be in metres -> use the CRS of the streets
    # (e.g. EPSG:2180 PUWG 1992 or EPSG:2177 PL-2000), never WGS 84 degrees.
    sr = arcpy.Describe(streets).spatialReference
    if sr.type != "Projected":
        raise ValueError(f"Streets must use a projected CRS in metres, got: {sr.name}")

    fds = os.path.join(gdb, "NA_dataset")
    if not arcpy.Exists(fds):
        arcpy.management.CreateFeatureDataset(gdb, "NA_dataset", sr)

    streets_fc = os.path.join(fds, "Streets")
    if not arcpy.Exists(streets_fc):
        arcpy.conversion.ExportFeatures(streets, streets_fc)

    nd = os.path.join(fds, "Streets_ND")
    if not arcpy.Exists(nd):
        # creates a network dataset with a "Length" cost attribute
        arcpy.na.CreateNetworkDataset(fds, "Streets_ND", ["Streets"], "NO_ELEVATION")
    arcpy.na.BuildNetwork(nd)

    points = os.path.join(gdb, "Building_Points")
    lyr = arcpy.management.MakeFeatureLayer(buildings, "bld_lyr", where or "")
    arcpy.management.FeatureToPoint(lyr, points, "INSIDE")

    source = os.path.join(gdb, "Heat_Source")
    arcpy.conversion.ExportFeatures(heat_source, source)
    return gdb, nd, points, source


def read_points(fc, id_field):
    """{id: PointGeometry}"""
    with arcpy.da.SearchCursor(fc, [id_field, "SHAPE@"]) as cur:
        return {str(i): g for i, g in cur}


# --------------------------------------------------------------------------- #
# Closest Facility from one connected point to all remaining buildings
# --------------------------------------------------------------------------- #

def solve_from(nd_layer, origin_name, origin_geom, remaining, search_tolerance):
    """
    Closest Facility with ONE incident (the newly connected point) and all
    remaining buildings as facilities. Returns {building_id: (length, polyline)}.
    """
    cf = arcpy.nax.ClosestFacility(nd_layer)
    cf.travelDirection = arcpy.nax.TravelDirection.ToFacility
    cf.defaultTargetFacilityCount = len(remaining)       # every reachable building
    cf.routeShapeType = arcpy.nax.RouteShapeType.TrueShape
    cf.searchTolerance = search_tolerance
    cf.searchToleranceUnits = arcpy.nax.DistanceUnits.Meters
    cf.distanceUnits = arcpy.nax.DistanceUnits.Meters
    cf.ignoreInvalidLocations = True

    with cf.insertCursor(arcpy.nax.ClosestFacilityInputDataType.Incidents, ["Name", "SHAPE@"]) as cur:
        cur.insertRow([origin_name, origin_geom])
    with cf.insertCursor(arcpy.nax.ClosestFacilityInputDataType.Facilities, ["Name", "SHAPE@"]) as cur:
        for bid, geom in remaining.items():
            cur.insertRow([bid, geom])

    result = cf.solve()
    if not result.solveSucceeded:
        return {}

    fac_type = arcpy.nax.ClosestFacilityOutputDataType.Facilities
    route_type = arcpy.nax.ClosestFacilityOutputDataType.Routes
    oid_to_name = {oid: name for oid, name in result.searchCursor(fac_type, ["ObjectID", "Name"])}

    # arcpy.nax reports route cost in standardised fields; pick the distance one
    fields = result.fieldNames(route_type)
    for cost_field, to_m in (("Total_Kilometers", 1000.0), ("Total_Length", 1.0),
                             ("Total_Miles", 1609.344), ("Total_Other", 1.0)):
        if cost_field in fields:
            break
    else:
        raise RuntimeError(f"No route length field among: {fields}")

    out = {}
    for fac_oid, cost, shape in result.searchCursor(route_type, ["FacilityOID", cost_field, "SHAPE@"]):
        bid = oid_to_name.get(fac_oid)
        if bid is not None and cost is not None:
            out[bid] = (cost * to_m, shape)
    return out


# --------------------------------------------------------------------------- #
# main loop
# --------------------------------------------------------------------------- #

def generate(streets, buildings, heat_source, workspace, id_field="bid", where=None,
             search_tolerance=500):
    arcpy.CheckOutExtension("Network")
    gdb, nd, points_fc, source_fc = prepare(streets, buildings, heat_source, workspace, where)
    nd_layer = "streets_nd_layer"
    arcpy.nax.MakeNetworkDatasetLayer(nd, nd_layer)

    remaining = read_points(points_fc, id_field)
    source_geom = next(iter(read_points(source_fc, "OID@").values()))
    sr = arcpy.Describe(points_fc).spatialReference

    solution = arcpy.management.CreateFeatureclass(gdb, "Solution_HeatNetwork", "POLYLINE",
                                                   spatial_reference=sr)[0]
    arcpy.management.AddFields(solution, [["building_id", "TEXT", "", 50],
                                          ["connected_from", "TEXT", "", 50],
                                          ["iteration", "LONG"],
                                          ["route_m", "DOUBLE"]])

    best = {}                   # building_id -> (length, polyline, connected_from)
    newly_connected = ("SOURCE", source_geom)
    iteration, t0 = 0, time.time()

    with arcpy.da.InsertCursor(solution, ["building_id", "connected_from", "iteration",
                                          "route_m", "SHAPE@"]) as out:
        while remaining:
            # 1) routes from the newly connected point; keep the best route per building
            name, geom = newly_connected
            for bid, (length, shape) in solve_from(nd_layer, name, geom, remaining,
                                                   search_tolerance).items():
                if bid not in best or length < best[bid][0]:
                    best[bid] = (length, shape, name)

            candidates = {b: v for b, v in best.items() if b in remaining}
            if not candidates:
                arcpy.AddWarning(f"{len(remaining)} building(s) cannot be reached: "
                                 f"{', '.join(sorted(remaining)[:20])}")
                break

            # 2) the route with the minimum length wins (model: Export Features, MIN(Total_Length))
            bid = min(candidates, key=lambda b: candidates[b][0])
            length, shape, origin = candidates[bid]
            iteration += 1
            out.insertRow([bid, origin, iteration, length, shape])

            # 3) move the building from Facilities to Incidents
            newly_connected = (bid, remaining.pop(bid))
            if iteration % 25 == 0:
                arcpy.AddMessage(f"{iteration} buildings connected, {len(remaining)} left "
                                 f"({time.time() - t0:.0f} s)")

    # one polyline network without duplicated overlapping parts
    dissolved = os.path.join(gdb, "Solution_HeatNetwork_Dissolve")
    arcpy.management.Dissolve(solution, dissolved, multi_part="SINGLE_PART", unsplit_lines="UNSPLIT_LINES")
    arcpy.AddMessage(f"Done: {iteration} connections in {time.time() - t0:.0f} s -> {solution}")
    return solution, dissolved, points_fc, source_fc


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--streets", required=True)
    ap.add_argument("--buildings", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--workspace", required=True, help="folder for DistrictHeating.gdb")
    ap.add_argument("--id-field", default="bid")
    ap.add_argument("--where", default=None, help='SQL filter, e.g. "connect = 1"')
    ap.add_argument("--tolerance", type=float, default=500, help="search tolerance [m]")
    a = ap.parse_args()
    generate(a.streets, a.buildings, a.source, a.workspace, a.id_field, a.where, a.tolerance)
