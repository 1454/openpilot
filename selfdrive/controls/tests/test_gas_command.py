from types import SimpleNamespace

from openpilot.selfdrive.controls.lib.gas_command import resolved_gas_model, stock_drag_and_area


STOCK = (3130.0, 0.42655, 0.30, 4.0054, 1.225)


def test_zero_overrides_keep_the_stock_gas_model():
  mass, radius, drag, area, density = resolved_gas_model(*STOCK, SimpleNamespace(
    gas_vehicle_mass_kg=0.0,
    gas_tire_radius=0.0,
    gas_drag_coeff=0.0,
    gas_frontal_area=0.0,
    gas_air_density=0.0,
  ))
  assert (mass, radius, drag, area, density) == STOCK


def test_each_override_replaces_only_its_term():
  toggles = SimpleNamespace(
    gas_vehicle_mass_kg=2500.0,
    gas_tire_radius=0.419,
    gas_drag_coeff=0.42,
    gas_frontal_area=3.2,
    gas_air_density=1.1,
  )
  assert resolved_gas_model(*STOCK, toggles) == (2500.0, 0.419, 0.42, 3.2, 1.1)


def test_missing_toggles_keep_stock():
  assert resolved_gas_model(*STOCK, None) == STOCK


def test_silverado_uses_published_drag_and_area_and_others_keep_the_estimate():
  assert stock_drag_and_area(True, 3.75) == (0.38, 3.4)
  cd, area = stock_drag_and_area(False, 2.7)
  assert cd == 0.30 and abs(area - (1.05 * 2.7 + 0.0679)) < 1e-9


if __name__ == "__main__":
  test_silverado_uses_published_drag_and_area_and_others_keep_the_estimate()
  test_zero_overrides_keep_the_stock_gas_model()
  test_each_override_replaces_only_its_term()
  test_missing_toggles_keep_stock()
  print("gas command tests passed")
