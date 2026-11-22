"""
Pipe sizing for district heating networks.

Everything here is derived from first principles and cited literature, so the
capacity table is reproducible and can be changed by the user:

* thermal capacity of a pipe:      P = m_dot * c_p * dT
* mass flow limited by two design criteria (whichever is stricter):
    - maximum specific pressure loss R_max [Pa/m]   (Darcy-Weisbach + Colebrook)
    - maximum flow velocity v_max [m/s]
* simultaneity (diversity) factor for n downstream consumers after
  Winter, Haslauer & Obernberger (2001), Euroheat & Power 9-10/2001.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# Nominal diameter -> inner diameter [mm] of steel service pipes in pre-insulated
# bonded pipe systems (EN 253, outer diameter x standard wall thickness).
STEEL_INNER_DIAMETER_MM: dict[int, float] = {
    20: 26.9 - 2 * 2.6,
    25: 33.7 - 2 * 3.2,
    32: 42.4 - 2 * 3.2,
    40: 48.3 - 2 * 3.2,
    50: 60.3 - 2 * 3.2,
    65: 76.1 - 2 * 3.2,
    80: 88.9 - 2 * 3.2,
    100: 114.3 - 2 * 3.6,
    125: 139.7 - 2 * 3.6,
    150: 168.3 - 2 * 4.0,
    200: 219.1 - 2 * 4.5,
    250: 273.0 - 2 * 5.0,
    300: 323.9 - 2 * 5.6,
}


@dataclass(frozen=True)
class DesignParameters:
    """Assumptions behind the capacity table (documented in docs/pipe_sizing.md)."""

    delta_t_k: float = 30.0          # supply - return temperature difference [K]
    max_pressure_loss_pa_m: float = 150.0
    max_velocity_m_s: float = 3.0
    roughness_mm: float = 0.05       # absolute roughness of steel pipe
    density_kg_m3: float = 977.8     # water at ~70 degC
    cp_kj_kgk: float = 4.19
    kinematic_viscosity_m2_s: float = 0.413e-6
    diameters: dict[int, float] = field(default_factory=lambda: dict(STEEL_INNER_DIAMETER_MM))


def friction_factor(reynolds: float, rel_roughness: float) -> float:
    """Darcy friction factor: laminar 64/Re, turbulent Colebrook-White (iterative)."""
    if reynolds < 2300:
        return 64.0 / max(reynolds, 1e-9)
    # Swamee-Jain as starting value, then refine Colebrook-White
    f = 0.25 / math.log10(rel_roughness / 3.7 + 5.74 / reynolds ** 0.9) ** 2
    for _ in range(50):
        rhs = -2.0 * math.log10(rel_roughness / 3.7 + 2.51 / (reynolds * math.sqrt(f)))
        f_new = 1.0 / rhs ** 2
        if abs(f_new - f) < 1e-12:
            break
        f = f_new
    return f


def pressure_loss_pa_m(velocity: float, d_m: float, p: DesignParameters) -> float:
    """Specific pressure loss R = f * rho * v^2 / (2 d) [Pa/m]."""
    if velocity <= 0:
        return 0.0
    re = velocity * d_m / p.kinematic_viscosity_m2_s
    f = friction_factor(re, p.roughness_mm / 1000.0 / d_m)
    return f * p.density_kg_m3 * velocity ** 2 / (2.0 * d_m)


def max_velocity(d_m: float, p: DesignParameters) -> float:
    """Highest velocity meeting both criteria (bisection on the pressure-loss limit)."""
    lo, hi = 0.0, p.max_velocity_m_s
    if pressure_loss_pa_m(hi, d_m, p) <= p.max_pressure_loss_pa_m:
        return hi
    for _ in range(100):
        mid = (lo + hi) / 2
        if pressure_loss_pa_m(mid, d_m, p) <= p.max_pressure_loss_pa_m:
            lo = mid
        else:
            hi = mid
    return lo


def capacity_kw(dn: int, p: DesignParameters | None = None) -> float:
    """Maximum thermal power [kW] a pipe of nominal diameter ``dn`` can carry."""
    p = p or DesignParameters()
    d_m = p.diameters[dn] / 1000.0
    area = math.pi * d_m ** 2 / 4.0
    m_dot = p.density_kg_m3 * max_velocity(d_m, p) * area          # kg/s
    return m_dot * p.cp_kj_kgk * p.delta_t_k                         # kW


def capacity_table(p: DesignParameters | None = None) -> list[tuple[int, float]]:
    """[(DN, max kW), ...] sorted by DN."""
    p = p or DesignParameters()
    return [(dn, capacity_kw(dn, p)) for dn in sorted(p.diameters)]


def select_dn(load_kw: float, table: list[tuple[int, float]]) -> int | None:
    """Smallest DN whose capacity covers ``load_kw``; None if even the largest is too small."""
    for dn, cap in table:
        if load_kw <= cap:
            return dn
    return None


def simultaneity_factor(n_consumers: int) -> float:
    """
    Diversity factor for n consumers (Winter, Haslauer & Obernberger 2001):
        g(n) = 0.449245 + 0.550755 / (1 + (n / 53.84) ** 1.7609)
    g(1) ~ 1.0, decreasing towards ~0.45 for large networks.
    """
    if n_consumers <= 0:
        return 0.0
    return 0.449245 + 0.550755 / (1.0 + (n_consumers / 53.84) ** 1.7609)


# --------------------------------------------------------------------------- #
# sizing a generated network
# --------------------------------------------------------------------------- #

@dataclass
class EdgeLoad:
    consumers: int = 0          # buildings supplied through this edge
    connected_kw: float = 0.0   # sum of their peak loads
    gzf: float = 0.0            # simultaneity factor g(consumers)
    design_kw: float = 0.0      # connected_kw * gzf
    dn: int | None = None


def size_network(g, edges, source, buildings: dict, loads_kw: dict,
                 params: DesignParameters | None = None) -> dict:
    """
    Accumulate loads along the network and choose a DN for every edge.

    Mirrors the WYMIAROWANIE ModelBuilder model: every building is routed to
    the heat source *along the generated network* (Closest Facility on the
    network), the loads of all routes sharing an edge are summed (in ArcGIS:
    Split Line At Vertices + Dissolve on segment centroids with SUM), the
    simultaneity factor is applied and the edge is classified into a DN.

    g         : graph containing the network edges
    edges     : iterable of frozenset({u, v}) -> the generated network
    source    : graph node of the heat source
    buildings : building id -> graph node
    loads_kw  : building id -> peak heat load [kW]
    returns   : frozenset({u, v}) -> EdgeLoad
    """
    import networkx as nx

    params = params or DesignParameters()
    table = capacity_table(params)
    net = nx.Graph()
    for e in edges:
        u, v = tuple(e)
        net.add_edge(u, v, length=g[u][v]["length"])

    _, paths = nx.single_source_dijkstra(net, source, weight="length")
    acc: dict = {frozenset(e): EdgeLoad() for e in edges}
    for bid, node in buildings.items():
        path = paths.get(node)
        if path is None:
            continue
        load = float(loads_kw.get(bid, 0.0))
        for a, b in zip(path[:-1], path[1:]):
            el = acc[frozenset((a, b))]
            el.consumers += 1
            el.connected_kw += load

    for el in acc.values():
        el.gzf = simultaneity_factor(el.consumers)
        el.design_kw = el.connected_kw * el.gzf
        el.dn = select_dn(el.design_kw, table) if el.consumers else None
    return acc
