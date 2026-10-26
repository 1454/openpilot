"""Estimate lead speed from distance instead of the model velocity.

The model distance stays steady while its lead speed swings. That swing is what
makes the planner accelerate and brake behind a lead whose gap is not changing.
This filter's only measurement is dRel. Lead speed is the state that explains
how that distance moves relative to the ego speed.
"""
import numpy as np

DT_MDL = 0.05
READY_TIME = 1.0
MAX_SPEED_VARIANCE = 4.0
DISTANCE_JUMP = 8.0
MEASUREMENT_VARIANCE = 1.0
ACCEL_NOISE = 0.8


class DistanceLeadVelocity:
  def __init__(self):
    self.reset()

  def reset(self):
    self.x = None
    self.P = None
    self.age = 0.0

  def _seed(self, distance, model_speed):
    self.x = np.array([float(distance), float(model_speed)], dtype=float)
    self.P = np.diag([MEASUREMENT_VARIANCE, 25.0])
    self.age = 0.0

  def update(self, distance, v_ego, model_speed, dt=DT_MDL):
    distance = float(distance)
    v_ego = float(v_ego)
    model_speed = float(model_speed)
    dt = float(dt)
    if self.x is None or not np.isfinite(distance) or dt <= 0.0 or dt > 0.5 or abs(distance - self.x[0]) > DISTANCE_JUMP:
      self._seed(distance, model_speed)
      return model_speed, False

    F = np.array([[1.0, dt], [0.0, 1.0]])
    self.x = F @ self.x
    self.x[0] -= v_ego * dt
    Q = ACCEL_NOISE * np.array([[dt**3 / 3.0, dt**2 / 2.0], [dt**2 / 2.0, dt]])
    self.P = F @ self.P @ F.T + Q

    innovation = distance - self.x[0]
    variance = self.P[0, 0] + MEASUREMENT_VARIANCE
    gain = self.P[:, 0] / variance
    self.x += gain * innovation
    identity = np.eye(2)
    A = identity - np.outer(gain, [1.0, 0.0])
    self.P = A @ self.P @ A.T + np.outer(gain, gain) * MEASUREMENT_VARIANCE
    self.P = 0.5 * (self.P + self.P.T)
    self.age += dt

    ready = self.age >= READY_TIME and self.P[1, 1] <= MAX_SPEED_VARIANCE and np.isfinite(self.x[1])
    if not ready:
      return model_speed, False
    return float(self.x[1]), True


def apply_distance_lead_velocity(enabled, tracker, lead, v_ego, dt=DT_MDL):
  """Replace a vision lead's speed when the toggle is on and the filter is ready.

  Radar leads keep their measured speed. A missing lead resets the tracker so the
  next car ahead starts from a fresh distance history.
  """
  if not enabled or not lead.get("status") or lead.get("radar"):
    tracker.reset()
    return lead
  speed, ready = tracker.update(lead["dRel"], v_ego, lead["vLead"], dt)
  if not ready:
    return lead
  updated = dict(lead)
  updated["vLead"] = speed
  updated["vLeadK"] = speed
  updated["vRel"] = speed - float(v_ego)
  return updated
