"""E2E slowing stays off unless plannerd attaches params and the toggle is on."""

import unittest

from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR
from openpilot.cereal import messaging
from openpilot.selfdrive.controls.lib.longcontrol import LongCtrlState
from openpilot.selfdrive.controls.lib.longitudinal_planner import E2E_ASSIST_KEY, LongitudinalPlanner


class OffParams:
  def get_bool(self, key):
    return False


class BrokenParams:
  def get_bool(self, key):
    raise RuntimeError(key)


def bus(stamp):
  names = ('modelV2', 'carState', 'carControl', 'controlsState', 'selfdriveState', 'radarState', 'vehicleParameters')
  events = {name: messaging.new_message(name) for name in names}
  events['modelV2'].modelV2.orientationRate.z = [0.0] * 33
  events['modelV2'].modelV2.velocity.x = [20.0] * 33
  events['modelV2'].modelV2.position.x = [float(i * 5) for i in range(33)]
  events['modelV2'].modelV2.position.y = [0.0] * 33
  events['modelV2'].modelV2.action.desiredAcceleration = -1.5
  events['modelV2'].modelV2.action.shouldStop = False
  events['carState'].carState.vCruise = 80.0
  events['carState'].carState.vEgo = 20.0
  events['carState'].carState.aEgo = 0.0
  events['carState'].carState.canValid = True
  events['carControl'].carControl.longActive = True
  events['carControl'].carControl.orientationNED = [0.0, 0.0, 0.0]
  events['selfdriveState'].selfdriveState.enabled = True
  events['controlsState'].controlsState.longControlState = LongCtrlState.pid
  events['radarState'].radarState.leadOne.present = True
  events['radarState'].radarState.leadOne.dRel = 40.0
  events['radarState'].radarState.leadOne.vLead = 20.0
  events['radarState'].radarState.leadOne.vRel = 0.0
  events['radarState'].radarState.leadOne.modelProb = 1.0

  class Bus:
    def __init__(self):
      self.logMonoTime = dict.fromkeys(names, stamp)
      self.valid = dict.fromkeys(names, True)
      self.alive = dict.fromkeys(names, True)
      self.data = {name: getattr(messaging.log_from_bytes(msg.to_bytes()), name) for name, msg in events.items()}

    def __getitem__(self, name):
      return self.data[name]

  return Bus()


class E2ESlowingToggleTests(unittest.TestCase):
  def test_missing_or_failed_params_match_the_assist_being_off(self):
    cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
    cp.openpilotLongitudinalControl = True
    missing = LongitudinalPlanner(cp, init_v=20.0)
    off = LongitudinalPlanner(cp, init_v=20.0)
    failed = LongitudinalPlanner(cp, init_v=20.0)
    self.assertIsNone(missing.added_toggles.params)
    off.added_toggles.params = OffParams()
    failed.added_toggles.params = BrokenParams()
    sm = bus(1_000_000_000)
    for planner in (missing, off, failed):
      planner.update(sm, now_ns=1_000_000_000)
      self.assertFalse(planner.e2e_assist_active)
      self.assertFalse(planner.added_toggles[E2E_ASSIST_KEY])
    self.assertEqual(float(missing.output_a_target), float(off.output_a_target))
    self.assertEqual(bool(missing.output_should_stop), bool(off.output_should_stop))
    self.assertEqual(float(missing.output_a_target), float(failed.output_a_target))
    self.assertEqual(bool(missing.output_should_stop), bool(failed.output_should_stop))
