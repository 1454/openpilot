from openpilot.selfdrive.controls.lib.lead_distance_velocity import (
  DistanceLeadVelocity,
  apply_distance_lead_velocity,
)


def _run(distance_fn, model_speed_fn, v_ego=25.0, seconds=4.0, dt=0.05, model_accel_fn=None):
  tracker = DistanceLeadVelocity()
  speed = None
  ready = False
  steps = int(seconds / dt)
  for i in range(steps):
    t = i * dt
    model_accel = 0.0 if model_accel_fn is None else model_accel_fn(t)
    speed, ready = tracker.update(distance_fn(t), v_ego, model_speed_fn(t), model_accel, dt)
  return speed, ready, tracker


def test_steady_gap_ignores_swinging_model_speed():
  speed, ready, tracker = _run(lambda t: 30.0, lambda t: 10.0 if int(t * 10) % 2 == 0 else 40.0)
  assert ready
  assert abs(speed - 25.0) < 1.0
  assert "enter" not in tracker.events


def test_closing_gap_estimates_slower_lead():
  speed, ready, _tracker = _run(lambda t: 40.0 - 3.0 * t, lambda t: 25.0)
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


def test_distance_jump_does_not_invent_a_brake():
  tracker = DistanceLeadVelocity()
  lead = {"status": True, "radar": False, "dRel": 30.0, "vLead": 40.0, "vLeadK": 40.0, "aLeadK": 0.0}
  for _ in range(80):
    updated = apply_distance_lead_velocity(True, tracker, lead, v_ego=25.0)
  assert abs(updated["vLead"] - 25.0) < 1.0
  lead["dRel"] = 12.0
  updated = apply_distance_lead_velocity(True, tracker, lead, v_ego=25.0)
  assert abs(updated["vLead"] - 25.0) < 1.0
  assert "enter" not in tracker.events


def test_stopped_distance_offset_is_not_a_closing_gap():
  tracker = DistanceLeadVelocity()
  lead = {"status": True, "radar": False, "dRel": 30.0, "vLead": 25.0, "vLeadK": 25.0, "aLeadK": 0.0}
  for _ in range(80):
    apply_distance_lead_velocity(True, tracker, lead, v_ego=25.0, distance_offset=0.0)
  lead["dRel"] = 27.0
  updated = apply_distance_lead_velocity(True, tracker, lead, v_ego=25.0, distance_offset=3.0)
  assert abs(updated["vLead"] - 25.0) < 1.0


def _brake_trace(model_agrees, dt=0.05):
  tracker = DistanceLeadVelocity()
  v_ego = 25.0
  v_lead = 25.0
  distance = 20.0
  for _ in range(int(12.0 / dt)):
    tracker.update(distance, v_ego, v_ego, 0.0, dt)
  samples = []
  for i in range(int(2.0 / dt)):
    t = i * dt
    if i > 0:
      v_lead = max(0.0, v_lead - 6.0 * dt)
      distance -= (v_ego - v_lead) * dt
    model_speed = v_lead if model_agrees else v_ego
    model_accel = -6.0 if model_agrees else 0.0
    speed, ready = tracker.update(distance, v_ego, model_speed, model_accel, dt)
    samples.append((t, speed, ready, tracker.published_accel, tracker.in_event or "enter" in tracker.events))
  return samples


def test_hard_brake_is_picked_up_before_the_slow_filter():
  blind = _brake_trace(False)
  agreed = _brake_trace(True)

  def at(samples, t):
    return min(samples, key=lambda sample: abs(sample[0] - t))

  assert at(blind, 0.75)[4]
  assert at(agreed, 0.65)[4]
  assert at(agreed, 1.0)[1] < 20.0
  assert at(blind, 1.3)[1] < 20.0
  assert any(sample[3] is not None and sample[3] < 0.0 for sample in agreed)
  for samples in (blind, agreed):
    speeds = [sample[1] for sample in samples]
    assert all(earlier - later <= 0.6 + 1e-6 for earlier, later in zip(speeds, speeds[1:]))
