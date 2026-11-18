"""LaneChangeFreshSignal adds an off-to-on blinker edge without changing the default helper."""

from types import SimpleNamespace
import unittest

from openpilot.cereal import log
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper, LANE_CHANGE_START_TIME
from openpilot.starpilot.lateral.lane_change_preferences import LaneChangePolicy

OFF = log.LaneChangeState.off
PRE = log.LaneChangeState.preLaneChange
STARTING = log.LaneChangeState.laneChangeStarting


def car(**changes):
  values = {"vEgo": 30.0, "leftBlinker": False, "rightBlinker": False,
            "steeringPressed": False, "steeringTorque": 0.0, "leftBlindspot": False, "rightBlindspot": False}
  values.update(changes)
  return SimpleNamespace(**values)


def helper(**policy):
  defaults = dict(one_per_signal=False, turn_signal_guidance=False, auto_lane_change=False, auto_delay_s=0.0)
  defaults.update(policy)
  return DesireHelper(LaneChangePolicy(**defaults))


def arm(lane, fresh):
  lane.update(car(), True, 1.0, fresh_signal=fresh)


def complete(lane, blink, fresh):
  torque = 1.0 if blink["leftBlinker"] else -1.0
  lane.update(car(**blink, steeringPressed=True, steeringTorque=torque), True, 1.0, fresh_signal=fresh)
  assert lane.lane_change_state == STARTING
  # The start timer is compared after each model step, so it needs one step past the threshold.
  for _ in range(int(LANE_CHANGE_START_TIME / DT_MDL) + 2):
    lane.update(car(**blink), True, 0.0, fresh_signal=fresh)
    if lane.lane_change_state != STARTING:
      return
  raise AssertionError("lane change did not finish")


class FreshSignalTests(unittest.TestCase):
  def test_default_off_still_rearms_while_the_signal_is_held(self):
    lane = helper()
    left = dict(leftBlinker=True)
    lane.update(car(**left), True, 1.0)
    self.assertEqual(lane.lane_change_state, PRE)
    complete(lane, left, False)
    self.assertEqual(lane.lane_change_state, PRE)

  def test_default_off_treats_engagement_and_hazard_release_as_edges(self):
    engaged = helper()
    left = car(leftBlinker=True)
    engaged.update(left, False, 1.0)
    engaged.update(left, True, 1.0)
    self.assertEqual(engaged.lane_change_state, PRE)

    hazards = helper()
    hazards.update(car(leftBlinker=True, rightBlinker=True), True, 1.0)
    hazards.update(car(rightBlinker=True), True, 1.0)
    self.assertEqual(hazards.lane_change_state, PRE)

  def test_fresh_signal_requires_an_off_to_on_edge_after_engagement(self):
    lane = helper()
    left = dict(leftBlinker=True)
    arm(lane, True)
    lane.update(car(**left), True, 1.0, fresh_signal=True)
    self.assertEqual(lane.lane_change_state, PRE)
    complete(lane, left, True)
    self.assertEqual(lane.lane_change_state, OFF)
    lane.update(car(**left), True, 1.0, fresh_signal=True)
    self.assertEqual(lane.lane_change_state, OFF)
    lane.update(car(), True, 1.0, fresh_signal=True)
    lane.update(car(**left), True, 1.0, fresh_signal=True)
    self.assertEqual(lane.lane_change_state, PRE)

  def test_signal_already_on_at_engagement_or_startup_does_not_start(self):
    startup = helper()
    left = car(leftBlinker=True)
    startup.update(left, True, 1.0, fresh_signal=True)
    self.assertEqual(startup.lane_change_state, OFF)

    engaged = helper()
    engaged.update(left, False, 1.0, fresh_signal=True)
    engaged.update(left, True, 1.0, fresh_signal=True)
    self.assertEqual(engaged.lane_change_state, OFF)

  def test_hazards_and_direction_changes_do_not_arm_a_fresh_request(self):
    hazards = helper()
    arm(hazards, True)
    hazards.update(car(leftBlinker=True, rightBlinker=True), True, 1.0, fresh_signal=True)
    hazards.update(car(rightBlinker=True), True, 1.0, fresh_signal=True)
    self.assertEqual(hazards.lane_change_state, OFF)

    direction = helper()
    arm(direction, True)
    direction.update(car(leftBlinker=True), True, 1.0, fresh_signal=True)
    self.assertEqual(direction.lane_change_state, PRE)
    direction.update(car(rightBlinker=True), True, 1.0, fresh_signal=True)
    self.assertEqual(direction.lane_change_state, OFF)
    direction.update(car(rightBlinker=True), True, 1.0, fresh_signal=True)
    self.assertEqual(direction.lane_change_state, OFF)

  def test_fresh_signal_keeps_auto_blindspot_and_navigation_gates(self):
    auto = helper(auto_lane_change=True)
    arm(auto, True)
    left = car(leftBlinker=True)
    auto.update(left, True, 1.0, auto_evidence=True, engaged=True, fresh_signal=True)
    self.assertEqual(auto.lane_change_state, PRE)
    auto.update(left, True, 1.0, auto_evidence=True, engaged=True, fresh_signal=True)
    self.assertEqual(auto.lane_change_state, STARTING)

    blocked = helper(auto_lane_change=True)
    arm(blocked, True)
    blocked.update(left, True, 1.0, auto_evidence=True, engaged=True, fresh_signal=True)
    blocked.update(car(leftBlinker=True, leftBlindspot=True), True, 1.0, auto_evidence=True, engaged=True, fresh_signal=True)
    self.assertEqual(blocked.lane_change_state, PRE)
    self.assertEqual(blocked.auto_status, "blindspotBlocked")

    navigation = helper()
    arm(navigation, True)
    navigation.update(left, True, 1.0, fresh_signal=True, navigation_turn=True)
    self.assertEqual(navigation.lane_change_state, OFF)

  def test_one_per_signal_still_blocks_until_the_stalk_is_released(self):
    lane = helper(one_per_signal=True)
    left = dict(leftBlinker=True)
    arm(lane, False)
    lane.update(car(**left), True, 1.0)
    complete(lane, left, False)
    self.assertEqual(lane.lane_change_state, OFF)
    lane.update(car(**left), True, 1.0)
    self.assertEqual(lane.lane_change_state, OFF)
    lane.update(car(), True, 1.0)
    lane.update(car(**left), True, 1.0)
    self.assertEqual(lane.lane_change_state, PRE)
