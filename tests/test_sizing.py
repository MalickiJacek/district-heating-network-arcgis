import math

import pytest

from dhnet.sizing import (DesignParameters, capacity_kw, capacity_table, pressure_loss_pa_m,
                          select_dn, simultaneity_factor)


def test_pressure_loss_matches_hand_calculation():
    # DN100 (di = 107.1 mm), v = 1.0 m/s: Re ~ 2.6e5, Colebrook f ~ 0.0185 -> R ~ 84 Pa/m
    p = DesignParameters()
    r = pressure_loss_pa_m(1.0, 0.1071, p)
    assert 75 < r < 95


def test_capacity_respects_both_criteria():
    p = DesignParameters()
    for dn, d_mm in p.diameters.items():
        d = d_mm / 1000
        m_dot = capacity_kw(dn, p) / (p.cp_kj_kgk * p.delta_t_k)
        v = m_dot / (p.density_kg_m3 * math.pi * d ** 2 / 4)
        assert v <= p.max_velocity_m_s + 1e-6
        assert pressure_loss_pa_m(v, d, p) <= p.max_pressure_loss_pa_m + 0.5


def test_capacity_grows_with_dn_and_delta_t():
    caps20 = [kw for _, kw in capacity_table(DesignParameters(delta_t_k=20))]
    caps40 = [kw for _, kw in capacity_table(DesignParameters(delta_t_k=40))]
    assert caps20 == sorted(caps20)
    # capacity is linear in delta T
    assert all(c40 == pytest.approx(2 * c20) for c20, c40 in zip(caps20, caps40))


def test_select_dn():
    table = [(20, 10.0), (25, 20.0), (32, 40.0)]
    assert select_dn(0.0, table) == 20
    assert select_dn(10.0, table) == 20
    assert select_dn(10.1, table) == 25
    assert select_dn(41, table) is None


def test_simultaneity_factor_shape():
    assert simultaneity_factor(1) == pytest.approx(1.0, abs=0.01)
    values = [simultaneity_factor(n) for n in (1, 5, 20, 100, 1000)]
    assert values == sorted(values, reverse=True)
    assert 0.44 < simultaneity_factor(10_000) < 0.46
    assert simultaneity_factor(0) == 0.0
