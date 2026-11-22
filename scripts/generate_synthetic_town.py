"""
Generate a reproducible synthetic town for demos and tests.

Real input data for the method are BDOT10k streets and building footprints
(geoportal.gov.pl). The original study areas cannot be redistributed here
(utility customer data, third-party test data), so this script builds a
fictional town with the same structure:

* Streets.shp     - noded street centre lines
* Buildings.shp   - building footprints with peak heat load ``peak_kw``, ``kind``
                    and ``connect`` (1 = wants a district heating connection)
* HeatSource.shp  - one heat plant at the edge of the town

CRS: EPSG:2180 (PUWG 1992). Usage: python scripts/generate_synthetic_town.py
"""

import argparse
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely import affinity
from shapely.geometry import LineString, Point, box

CRS = "EPSG:2180"
X0, Y0 = 360_000.0, 345_000.0   # arbitrary origin [m]


def make_streets(rng, nx=9, ny=7, step=130.0):
    # jittered grid of intersections
    pts = {(i, j): (X0 + i * step + rng.normal(0, 12), Y0 + j * step + rng.normal(0, 12))
           for i in range(nx) for j in range(ny)}
    segs = []
    for i in range(nx):
        for j in range(ny):
            for di, dj in ((1, 0), (0, 1)):
                k = (i + di, j + dj)
                if k in pts and rng.random() > 0.12:          # some missing links
                    segs.append(LineString([pts[(i, j)], pts[k]]))
    # one diagonal main road (noded: split where it meets grid nodes)
    diag = [pts[(i, min(i, ny - 1))] for i in range(min(nx, ny))]
    segs += [LineString([a, b]) for a, b in zip(diag[:-1], diag[1:])]
    return segs, pts


def make_buildings(rng, segs):
    rows, n = [], 0
    for s in segs:
        L = s.length
        k = int(L // 32)
        for t in np.linspace(0.15, 0.85, max(k, 1)):
            for side in (-1, 1):
                if rng.random() < 0.25:
                    continue
                p = s.interpolate(t, normalized=True)
                dx, dy = np.array(s.coords[-1]) - np.array(s.coords[0])
                nx_, ny_ = -dy / L, dx / L
                off = rng.uniform(14, 22)
                c = Point(p.x + side * nx_ * off, p.y + side * ny_ * off)
                r = rng.random()
                if r < 0.70:
                    kind, w, h, kw = "single-family", rng.uniform(9, 13), rng.uniform(9, 12), rng.uniform(8, 18)
                elif r < 0.93:
                    kind, w, h, kw = "multi-family", rng.uniform(14, 28), rng.uniform(11, 14), rng.uniform(40, 160)
                else:
                    kind, w, h, kw = "public/service", rng.uniform(20, 40), rng.uniform(15, 25), rng.uniform(100, 350)
                ang = np.degrees(np.arctan2(dy, dx))
                poly = affinity.rotate(box(c.x - w / 2, c.y - h / 2, c.x + w / 2, c.y + h / 2), ang, origin=c)
                n += 1
                rows.append({"bid": n, "kind": kind, "peak_kw": round(kw, 1), "geometry": poly})
    g = gpd.GeoDataFrame(rows, crs=CRS)
    # drop overlapping footprints
    keep, taken = [], []
    for i, geom in enumerate(g.geometry):
        if not any(geom.intersects(t) for t in taken):
            keep.append(i)
            taken.append(geom.buffer(1))
    g = g.iloc[keep].reset_index(drop=True)
    g["bid"] = range(1, len(g) + 1)
    # connection willingness: all multi-family and public buildings,
    # ~30 % of single-family houses (typical early-stage connection rate)
    g["connect"] = ((g["kind"] != "single-family") | (rng.random(len(g)) < 0.30)).astype(int)
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/sample")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)

    segs, pts = make_streets(rng)
    streets = gpd.GeoDataFrame({"sid": range(1, len(segs) + 1)}, geometry=segs, crs=CRS)
    buildings = make_buildings(rng, segs)
    sx, sy = pts[(0, 3)]
    source = gpd.GeoDataFrame({"name": ["Heat plant"]}, geometry=[Point(sx - 60, sy + 5)], crs=CRS)

    streets.to_file(out / "Streets.shp")
    buildings.to_file(out / "Buildings.shp")
    source.to_file(out / "HeatSource.shp")
    print(f"streets: {len(streets)}, buildings: {len(buildings)}, "
          f"to connect: {int(buildings.connect.sum())}, "
          f"their peak load: {buildings.loc[buildings.connect == 1, 'peak_kw'].sum():.0f} kW -> {out}")


if __name__ == "__main__":
    main()
