"""Resolve the GM gas-torque model from stock car values plus optional overrides.

A stored value of 0 keeps the stock number. Overrides are the tire radius,
mass, drag coefficient, frontal area, and air density used in:

  torque = tire_radius * (mass * accel + 0.5 * Cd * area * density * speed^2)
"""

from types import SimpleNamespace


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
