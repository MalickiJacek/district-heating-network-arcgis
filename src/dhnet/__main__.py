"""CLI: python -m dhnet --help"""

import argparse
import time
from pathlib import Path

from .pipeline import dissolve_by_dn, run, summary
from .sizing import DesignParameters, capacity_table


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dhnet",
                                 description="Generate and size a district heating network on a street graph.")
    ap.add_argument("--streets", required=True)
    ap.add_argument("--buildings", required=True)
    ap.add_argument("--source", required=True, help="heat source point layer")
    ap.add_argument("--out", default="output")
    ap.add_argument("--load-field", default="peak_kw", help="peak heat load per building [kW]")
    ap.add_argument("--id-field", default="bid")
    ap.add_argument("--where", default="connect == 1",
                    help='pandas query selecting buildings to connect ("" = all)')
    ap.add_argument("--from-network", action="store_true",
                    help="measure distances from the whole network built so far (classical Steiner heuristic)")
    ap.add_argument("--delta-t", type=float, default=30.0, help="supply-return temperature difference [K]")
    ap.add_argument("--max-dp", type=float, default=150.0, help="max specific pressure loss [Pa/m]")
    ap.add_argument("--max-v", type=float, default=3.0, help="max velocity [m/s]")
    a = ap.parse_args(argv)

    params = DesignParameters(delta_t_k=a.delta_t, max_pressure_loss_pa_m=a.max_dp, max_velocity_m_s=a.max_v)
    t0 = time.perf_counter()
    res = run(a.streets, a.buildings, a.source, load_field=a.load_field, id_field=a.id_field,
              where=a.where or None, from_network=a.from_network, params=params)
    dt = time.perf_counter() - t0

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    gpkg = out / "district_heating.gpkg"
    res.pipes.to_file(gpkg, layer="pipe_segments", driver="GPKG")
    dissolve_by_dn(res.pipes).to_file(gpkg, layer="pipes_by_dn", driver="GPKG")
    table = summary(res)
    table.to_csv(out / "pipe_lengths_by_dn.csv")

    saved = 1 - res.main_length / res.naive_main_length if res.naive_main_length else 0
    print(f"Buildings connected:       {len(res.network.order)} (unreachable: {len(res.network.unreachable)})")
    print(f"Main pipes:                {res.main_length:,.0f} m")
    print(f"Service connections:       {res.service_length:,.0f} m")
    print(f"Naive shortest-path tree:  {res.naive_main_length:,.0f} m main pipes "
          f"-> iterative method saves {saved:.0%}")
    print(f"Design load at the source: {res.pipes.design_kw.max():,.0f} kW "
          f"(dT = {params.delta_t_k:g} K)")
    print(f"Runtime:                   {dt:.2f} s")
    print("\nPipe length by DN [m]:")
    print(table.to_string())
    print(f"\nCapacity table used (dT={params.delta_t_k:g} K, R<={params.max_pressure_loss_pa_m:g} Pa/m, "
          f"v<={params.max_velocity_m_s:g} m/s):")
    print("  " + ", ".join(f"DN{dn}: {kw:,.0f} kW" for dn, kw in capacity_table(params)))
    print(f"\nResults: {gpkg}")


if __name__ == "__main__":
    main()
