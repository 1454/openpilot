"""Resolve the GM gas-torque model from stock car values plus optional overrides.

A stored value of 0 keeps the stock number. Overrides are the tire radius,
mass, drag coefficient, frontal area, and air density used in:

  torque = tire_radius * (mass * accel + 0.5 * Cd * area * density * speed^2)
"""

from types import SimpleNamespace


# 2020 Silverado crew cab: published Cd 0.38, frontal area about 3.3 to 3.4 m^2.
SILVERADO_DRAG_COEFF = 0.38
SILVERADO_FRONTAL_AREA = 3.4


def stock_drag_and_area(is_silverado: bool, wheelbase: float, generic_drag: float = 0.30) -> tuple[float, float]:
  if is_silverado:
    return SILVERADO_DRAG_COEFF, SILVERADO_FRONTAL_AREA
  return generic_drag, 1.05 * wheelbase + 0.0679


def _pick(toggles, name: str, stock: float, low: float, high: float) -> float:
  value = float(getattr(toggles, name, 0.0) or 0.0)
  if low < value < high:
    return value
  return float(stock)


def resolved_gas_model(stock_mass: float, stock_tire_radius: float, stock_drag: float,
                       stock_area: float, stock_density: float, toggles=None) -> tuple[float, float, float, float, float]:
  toggles = toggles if toggles is not None else SimpleNamespace()
  return (
    _pick(toggles, "gas_vehicle_mass_kg", stock_mass, 200.0, 20000.0),
    _pick(toggles, "gas_tire_radius", stock_tire_radius, 0.2, 0.8),
    _pick(toggles, "gas_drag_coeff", stock_drag, 0.05, 1.5),
    _pick(toggles, "gas_frontal_area", stock_area, 0.5, 12.0),
    _pick(toggles, "gas_air_density", stock_density, 0.5, 2.0),
  )
