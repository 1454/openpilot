from openpilot.selfdrive.controls.lib.lead_distance_velocity import (
  DistanceLeadVelocity,
  apply_distance_lead_velocity,
)


def _run(distance_fn, model_speed_fn, v_ego=25.0, seconds=4.0, dt=0.05):
  tracker = DistanceLeadVelocity()
  speed = None
  ready = False
  steps = int(seconds / dt)
  for i in range(steps):
    t = i * dt
    speed, ready = tracker.update(distance_fn(t), v_ego, model_speed_fn(t), dt)
  return speed, ready


def test_steady_gap_ignores_swinging_model_speed():
  speed, ready = _run(lambda t: 30.0, lambda t: 10.0 if int(t * 10) % 2 == 0 else 40.0)
  assert ready
  assert abs(speed - 25.0) < 1.0


def test_closing_gap_estimates_slower_lead():
  speed, ready = _run(lambda t: 40.0 - 3.0 * t, lambda t: 25.0)
  assert ready
  assert abs(speed - 22.0) < 1.0


def test_toggle_off_leaves_model_speed():
  tracker = DistanceLeadVelocity()
  lead = {"status": True, "radar": False, "dRel": 30.0, "vLead": 10.0, "vLeadK": 10.0, "vRel": -15.0}
  updated = apply_distance_lead_velocity(False, tracker, lead, v_ego=25.0)
  assert updated["vLead"] == 10.0
  assert tracker.x is None


def test_radar_lead_is_not_replaced():
  tracker = DistanceLeadVelocity()
  lead = {"status": True, "radar": True, "dRel": 30.0, "vLead": 18.0, "vLeadK": 18.0, "vRel": -7.0}
  for _ in range(80):
    updated = apply_distance_lead_velocity(True, tracker, lead, v_ego=25.0)
  assert updated["vLead"] == 18.0
  assert tracker.x is None


def test_vision_lead_switches_after_the_filter_settles():
  tracker = DistanceLeadVelocity()
  lead = {"status": True, "radar": False, "dRel": 30.0, "vLead": 10.0, "vLeadK": 10.0, "vRel": -15.0}
  updated = apply_distance_lead_velocity(True, tracker, lead, v_ego=25.0)
  assert updated["vLead"] == 10.0
  for _ in range(80):
    updated = apply_distance_lead_velocity(True, tracker, lead, v_ego=25.0)
  assert abs(updated["vLead"] - 25.0) < 1.0
  assert abs(updated["vRel"]) < 1.0
