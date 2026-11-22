from types import SimpleNamespace

from opendbc.car.gm.ordinary import demands
from opendbc.car.gm.truck_longitudinal import TruckTuning


def _car():
  return SimpleNamespace(carFingerprint="CHEVROLET_SILVERADO", wheelbase=3.75, mass=2800.0)


def _demand(accel, speed, state, stopping=False):
  return demands(accel, speed, None, _car(), inactive_gas=-500, brake_threshold=0.0,
                 stopping=stopping, friction_state=state)


def test_near_stop_creep_does_not_tap_the_friction_brake():
  state = TruckTuning()
  gas, brake = _demand(-0.05, 0.5, state)
  assert brake == 0
  assert gas != -500
  assert state.friction_active is False


def test_stopping_keeps_the_hold_brake():
  state = TruckTuning()
  gas, brake = _demand(-0.25, 0.2, state, stopping=True)
  assert brake == 25
  assert gas == -500
  assert state.friction_active is True


def test_real_braking_engages_and_a_smaller_request_holds_until_release():
  state = TruckTuning()
  gas, brake = _demand(-0.85, 1.0, state)
  assert brake >= 40
  assert gas == -500
  assert state.friction_active is True
  gas, brake = _demand(-0.2, 1.0, state)
  assert brake > 8
  assert state.friction_active is True
  gas, brake = _demand(-0.05, 1.0, state)
  assert brake == 0
  assert gas != -500
  assert state.friction_active is False
