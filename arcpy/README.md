# ArcGIS Pro scripts

Python (ArcPy) versions of the two ModelBuilder models in [`../model/`](../model/).

| Script | ModelBuilder model | What it does |
|---|---|---|
| `01_generate_network.py` | [Closest Facility iteration](../model/closest_facility_iteration.png) | builds a network dataset from the streets and grows the heating network one building at a time (iterative Closest Facility) |
| `02_size_pipes.py` | [Pipe sizing](../model/pipe_sizing.png) | sums building loads on every segment of the generated network, applies the simultaneity factor and assigns a DN |

## Requirements

- ArcGIS Pro 3.x with the **Network Analyst** extension (`arcpy.nax`)
- input data in a **projected** coordinate system in metres (e.g. EPSG:2180 or EPSG:2177 in Poland). The script stops on WGS 84.
- streets noded at intersections (BDOT10k `SKJZ` or OSM roads after *Planarize Lines*)
- buildings (polygons or points) with an id field and a peak load field [kW]

`02_size_pipes.py` imports the sizing maths from [`../src/dhnet/sizing.py`](../src/dhnet/sizing.py), so keep the repository structure. `networkx` ships with the default ArcGIS Pro Python environment.

## Run

From the *Python Command Prompt* of ArcGIS Pro:

```bat
propy 01_generate_network.py --streets D:\data\Streets.shp --buildings D:\data\Buildings.shp ^
      --source D:\data\HeatSource.shp --workspace D:\data\out --where "connect = 1"

propy 02_size_pipes.py --gdb D:\data\out\DistrictHeating.gdb --buildings D:\data\Buildings.shp ^
      --load-field peak_kw --delta-t 30
```

The synthetic sample in [`../data/sample/`](../data/sample/) works as input (EPSG:2180, fields `bid`, `peak_kw`, `connect`).

Outputs in `DistrictHeating.gdb`:

| Layer | Content |
|---|---|
| `Solution_HeatNetwork` | one route per connected building: `building_id`, `connected_from`, `iteration`, `route_m` |
| `Solution_HeatNetwork_Dissolve` | the same network as one set of lines |
| `Pipes_Sized` | segments with `consumers`, `conn_kw`, `gzf`, `design_kw`, `dn`, `length_m` |
| `Pipes_By_DN` | segments merged by DN |

## Differences to the ModelBuilder model

- **Speed.** The model re-solves the whole Closest Facility problem in every iteration (paper: 5 h for 1127 buildings). The script solves only from the newly connected building and keeps the best route found so far for every remaining building. Adding an incident can only make routes shorter, so the result is the same.
- **Units.** The original setup script created the feature dataset in WGS 84; the script uses the coordinate system of the streets, so lengths are in metres.
- **Simultaneity factor** is applied in the sizing step (see [`../docs/pipe_sizing.md`](../docs/pipe_sizing.md)).

## Status

The ModelBuilder models were used for the paper. The ArcPy scripts were written later from those models and complete the loop that the original script did not have. They have **not been run on every ArcGIS Pro version yet**: `arcpy.nax` result field names (`Total_Length` / `Total_Kilometers`) depend on the network's cost attributes, and the script picks whichever exists. The same algorithm is tested in the open-source package (`pytest`), which needs no ArcGIS licence.
