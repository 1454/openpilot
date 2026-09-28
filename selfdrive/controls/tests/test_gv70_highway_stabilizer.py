import math

import numpy as np
import pytest

from opendbc.car.hyundai.values import CAR as HYUNDAI_CAR
from openpilot.common.constants import CV
from openpilot.selfdrive.controls.lib.latcontrol_vehicle_tunes import (
  GENESIS_GV70_CARS,
  GenesisGV70HighwayCommandStabilizer,
)


def update_accel(stabilizer: GenesisGV70HighwayCommandStabilizer, lateral_accel: float,
                 speed: float = 30.0, enabled: bool = True) -> float:
  return stabilizer.update(lateral_accel / speed ** 2, speed, enabled, 0.01) * speed ** 2


def test_only_electrified_gv70_selected():
  assert GENESIS_GV70_CARS == (HYUNDAI_CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN,)
  assert HYUNDAI_CAR.GENESIS_G70_2020 not in GENESIS_GV70_CARS


@pytest.mark.parametrize('speed', [10.0, 30.0 * CV.MPH_TO_MS, 40.0 * CV.MPH_TO_MS])
def test_no_change_at_low_speed(speed):
  stabilizer = GenesisGV70HighwayCommandStabilizer()
  for i in range(1200):
    accel = 0.3 * math.sin(2.0 * math.pi * 0.5 * i * 0.01)
    assert update_accel(stabilizer, accel, speed) == pytest.approx(accel)


def test_repeated_highway_reversals_are_bounded_and_damped():
  stabilizer = GenesisGV70HighwayCommandStabilizer()
  raw, shaped = [], []
  for i in range(1200):
    accel = 0.15 + 0.3 * math.sin(2.0 * math.pi * 0.5 * i * 0.01)
    raw.append(accel)
    shaped.append(update_accel(stabilizer, accel))

  assert np.std(shaped[600:]) < 0.75 * np.std(raw[600:])
  assert np.max(np.abs(np.array(raw) - np.array(shaped))) <= 0.20 + 1e-6


def test_repeated_moderate_curve_reversals_are_damped():
  stabilizer = GenesisGV70HighwayCommandStabilizer()
  raw, shaped = [], []
  for i in range(1200):
    accel = 0.55 + 0.25 * math.sin(2.0 * math.pi * 0.5 * i * 0.01)
    raw.append(accel)
    shaped.append(update_accel(stabilizer, accel))

  assert np.std(shaped[600:]) < 0.75 * np.std(raw[600:])
  assert np.max(np.abs(np.array(raw) - np.array(shaped))) <= 0.20 + 1e-6


def test_sustained_curve_is_unchanged():
  stabilizer = GenesisGV70HighwayCommandStabilizer()
  curve = np.concatenate((np.linspace(0.0, 0.8, 150), np.full(300, 0.8), np.linspace(0.8, 0.0, 150)))
  for accel in curve:
    assert update_accel(stabilizer, float(accel)) == pytest.approx(accel)


def test_strong_turn_and_driver_input_reset_stabilizer():
  stabilizer = GenesisGV70HighwayCommandStabilizer()
  for i in range(1000):
    accel = 0.3 * math.sin(2.0 * math.pi * 0.5 * i * 0.01)
    update_accel(stabilizer, accel)

  for _ in range(100):
    assert update_accel(stabilizer, 1.2) == pytest.approx(1.2)
  for _ in range(100):
    assert update_accel(stabilizer, 0.4) == pytest.approx(0.4)

  assert update_accel(stabilizer, -0.3, enabled=False) == pytest.approx(-0.3)
  assert update_accel(stabilizer, 0.3) == pytest.approx(0.3)
