"""Estimate lead speed from distance instead of the model velocity.

The model distance stays steady while its lead speed swings. That swing is what
makes the planner accelerate and brake behind a lead whose gap is not changing.
This filter's only measurement is dRel. Lead speed is the state that explains
how that distance moves relative to the ego speed.

A constant-velocity filter settles about 0.7 seconds behind a hard brake and
then holds that error for the whole stop. When the distance innovations say the
gap is closing faster than the steady speed can explain, a maneuver filter takes
over until the stop is done. The model can corroborate that brake. It cannot
start one, and it cannot pull the published speed below the maneuver band.
"""
import logging
import math

import numpy as np

log = logging.getLogger(__name__)

DT_MDL = 0.05
READY_TIME = 1.0
MAX_SPEED_VARIANCE = 4.0
DISTANCE_JUMP = 8.0
MEASUREMENT_VARIANCE = 1.0
ACCEL_NOISE = 0.8

INNOV_TAU = 5.0
CUSUM_DRIFT = 1.0
CUSUM_CLIP = 3.0
CUSUM_CAP = 40.0
RANGE_TRIGGER = 7.0
CORROBORATED_TRIGGER = 4.0
CUE_FRAMES = 3
CUE_SPEED_GAP = 1.0
CUE_ACCEL = -0.75
PULLING_AWAY = -1.0
MANEUVER_JERK = 60.0
EXIT_ACCEL = -0.5
EXIT_ACCEL_FRAMES = 6
EXIT_G = 1.0
EXIT_G_FRAMES = 20
SPEED_DROP_LIMIT = 0.6
SPEED_RISE_LIMIT = 0.25
MAX_SPEED_OFFSET = 8.0
ACCEL_STEP_LIMIT = 0.5
ACCEL_LIMIT = 6.0
NOISE_DISTANCE_BP = [5.0, 10.0, 20.0, 35.0, 50.0, 70.0]
NOISE_SIGMA_V = [0.06, 0.15, 0.3, 0.5, 0.65, 0.9]
NOISE_SIGMA_FLOOR = 0.1


def _sigma_floor(distance):
  scheduled = float(np.interp(distance, NOISE_DISTANCE_BP, NOISE_SIGMA_V))
  return max(scheduled, NOISE_SIGMA_FLOOR)


def _joseph_update(x, P, distance):
  innovation = float(distance - x[0])
  variance = float(P[0, 0] + MEASUREMENT_VARIANCE)
  gain = P[:, 0] / variance
  x = x + gain * innovation
  observation = np.zeros(x.shape[0])
  observation[0] = 1.0
  A = np.eye(x.shape[0]) - np.outer(gain, observation)
  P = A @ P @ A.T + np.outer(gain, gain) * MEASUREMENT_VARIANCE
  P = 0.5 * (P + P.T)
  return x, P, innovation


class DistanceLeadVelocity:
  def __init__(self):
    self.reset()

  def reset(self):
    self.x = None
    self.P = None
    self.age = 0.0
    self.ready_latched = False
    self.innov_var = MEASUREMENT_VARIANCE
    self.g = 0.0
    self.cue_frames = 0
    self.in_event = False
    self.xm = None
    self.Pm = None
    self.maneuver_updates = 0
    self.saw_brake = False
    self.soft_frames = 0
    self.low_g_frames = 0
    self.rise_limit = False
    self.output_speed = None
    self.published_accel = None
    self.events = []

  def _seed(self, distance, model_speed):
    self.x = np.array([float(distance), float(model_speed)], dtype=float)
    self.P = np.diag([MEASUREMENT_VARIANCE, 25.0])
    self.age = 0.0
    self.ready_latched = False
    self.innov_var = MEASUREMENT_VARIANCE
    self.g = 0.0
    self.cue_frames = 0
    self.in_event = False
    self.xm = None
    self.Pm = None
    self.maneuver_updates = 0
    self.saw_brake = False
    self.soft_frames = 0
    self.low_g_frames = 0
    self.rise_limit = False
    self.output_speed = None
    self.published_accel = None

  def _exit_event(self, reanchor):
    if self.in_event and reanchor and self.xm is not None:
      self.x[1] = min(float(self.x[1]), float(self.xm[1]))
      self.P[1, 1] = max(float(self.P[1, 1]), float(self.Pm[1, 1]))
      self.P = 0.5 * (self.P + self.P.T)
      self.rise_limit = True
      self.events.append("exit")
      log.info("distance lead speed brake exit")
    elif self.in_event:
      self.events.append("jump")
      self.rise_limit = False
    self.in_event = False
    self.xm = None
    self.Pm = None
    self.maneuver_updates = 0
    self.saw_brake = False
    self.soft_frames = 0
    self.low_g_frames = 0
    self.g = 0.0
    self.published_accel = None

  def _enter_event(self):
    self.in_event = True
    self.rise_limit = False
    self.xm = np.array([float(self.x[0]), float(self.x[1]), 0.0], dtype=float)
    self.Pm = np.diag([float(self.P[0, 0]), float(self.P[1, 1]) + 4.0, 9.0])
    self.maneuver_updates = 0
    self.saw_brake = False
    self.soft_frames = 0
    self.low_g_frames = 0
    self.events.append("enter")
    log.info("distance lead speed brake enter")

  def _predict_maneuver(self, v_ego, dt):
    F = np.array([
      [1.0, dt, 0.5 * dt * dt],
      [0.0, 1.0, dt],
      [0.0, 0.0, 1.0],
    ])
    self.xm = F @ self.xm
    self.xm[0] -= v_ego * dt
    dt2 = dt * dt
    dt3 = dt2 * dt
    dt4 = dt3 * dt
    dt5 = dt4 * dt
    Q = MANEUVER_JERK * np.array([
      [dt5 / 20.0, dt4 / 8.0, dt3 / 6.0],
      [dt4 / 8.0, dt3 / 3.0, dt2 / 2.0],
      [dt3 / 6.0, dt2 / 2.0, dt],
    ])
    self.Pm = F @ self.Pm @ F.T + Q

  def _publish(self, v_steady, model_speed, model_accel):
    self.published_accel = None
    if not self.ready_latched:
      return float(model_speed)

    if self.in_event and self.xm is not None:
      v_man = float(self.xm[1])
      a_man = float(self.xm[2])
      sig_v = math.sqrt(max(float(self.Pm[1, 1]), 0.0))
      sig_a = math.sqrt(max(float(self.Pm[2, 2]), 0.0))
      model_clamped = min(max(float(model_speed), v_man - sig_v), v_man + sig_v)
      speed = min(v_steady, max(v_man - sig_v, model_clamped))
      speed = max(speed, v_steady - MAX_SPEED_OFFSET)
      if self.output_speed is not None:
        speed = max(speed, self.output_speed - SPEED_DROP_LIMIT)
      model_a = min(max(float(model_accel), a_man - 2.0 * sig_a), a_man + sig_a)
      accel = min(model_a, 0.0)
      accel = min(max(accel, -ACCEL_LIMIT), 0.0)
      previous_accel = model_accel if self.output_speed is None else getattr(self, "_output_accel", model_accel)
      accel = min(max(accel, previous_accel - ACCEL_STEP_LIMIT), previous_accel + ACCEL_STEP_LIMIT)
      accel = min(max(accel, -ACCEL_LIMIT), 0.0)
      self._output_accel = float(accel)
      self.published_accel = float(accel)
    elif self.rise_limit and self.output_speed is not None:
      speed = min(v_steady, self.output_speed + SPEED_RISE_LIMIT)
      speed = max(speed, self.output_speed - SPEED_DROP_LIMIT)
      self._output_accel = model_accel
    else:
      speed = v_steady
      self._output_accel = model_accel

    self.output_speed = float(speed)
    return float(speed)

  def update(self, distance, v_ego, model_speed, model_accel=0.0, dt=DT_MDL):
    distance = float(distance)
    v_ego = float(v_ego)
    model_speed = float(model_speed)
    model_accel = float(model_accel)
    dt = float(dt)
    if not np.isfinite(distance) or not np.isfinite(model_speed) or dt <= 0.0 or dt > 0.5:
      self._seed(distance if np.isfinite(distance) else 0.0, model_speed if np.isfinite(model_speed) else 0.0)
      return model_speed, False
    if self.x is None:
      self._seed(distance, model_speed)
      return model_speed, False
    if abs(distance - self.x[0]) > DISTANCE_JUMP:
      self._exit_event(reanchor=False)
      self.x[0] = distance
      self.cue_frames = 0
      if self.ready_latched:
        self.output_speed = float(self.x[1])
        return float(self.x[1]), True
      return model_speed, False

    F = np.array([[1.0, dt], [0.0, 1.0]])
    self.x = F @ self.x
    self.x[0] -= v_ego * dt
    Q = ACCEL_NOISE * np.array([[dt**3 / 3.0, dt**2 / 2.0], [dt**2 / 2.0, dt]])
    self.P = F @ self.P @ F.T + Q
    innovation = float(distance - self.x[0])

    sigma = max(math.sqrt(max(self.innov_var, 0.0)), _sigma_floor(distance))
    z = -innovation / sigma
    if self.ready_latched and not self.in_event and abs(z) < CUSUM_CLIP:
      alpha = 1.0 - math.exp(-dt / INNOV_TAU)
      self.innov_var = (1.0 - alpha) * self.innov_var + alpha * innovation * innovation
    if self.ready_latched:
      self.g = min(CUSUM_CAP, max(0.0, self.g + min(z, CUSUM_CLIP) - CUSUM_DRIFT))

    self.x, self.P, _ = _joseph_update(self.x, self.P, distance)
    self.age += dt
    if self.age >= READY_TIME and self.P[1, 1] <= MAX_SPEED_VARIANCE and np.isfinite(self.x[1]):
      self.ready_latched = True

    v_steady = float(self.x[1])
    if model_speed <= v_steady - CUE_SPEED_GAP or model_accel <= CUE_ACCEL:
      self.cue_frames += 1
    else:
      self.cue_frames = 0

    pulling_away = (v_ego - v_steady) <= PULLING_AWAY
    entered = False
    if self.ready_latched and not self.in_event and not pulling_away:
      corroborated = self.cue_frames >= CUE_FRAMES
      if self.g > RANGE_TRIGGER or (self.g > CORROBORATED_TRIGGER and corroborated):
        self._enter_event()
        entered = True

    if self.in_event and not entered:
      self._predict_maneuver(v_ego, dt)
      self.xm, self.Pm, _ = _joseph_update(self.xm, self.Pm, distance)
      self.maneuver_updates += 1
      a_man = float(self.xm[2])
      sig_a = math.sqrt(max(float(self.Pm[2, 2]), 0.0))
      if a_man < EXIT_ACCEL:
        self.saw_brake = True
      if self.saw_brake and a_man + sig_a >= EXIT_ACCEL:
        self.soft_frames += 1
      else:
        self.soft_frames = 0
      if self.g < EXIT_G:
        self.low_g_frames += 1
      else:
        self.low_g_frames = 0
      if (self.saw_brake and self.soft_frames >= EXIT_ACCEL_FRAMES) or self.low_g_frames >= EXIT_G_FRAMES:
        self._exit_event(reanchor=True)
        v_steady = float(self.x[1])

    speed = self._publish(v_steady, model_speed, model_accel)
    if not self.ready_latched or not np.isfinite(speed):
      return model_speed, False
    return float(speed), True


def apply_distance_lead_velocity(enabled, tracker, lead, v_ego, dt=DT_MDL, distance_offset=0.0):
  """Replace a vision lead's speed when the toggle is on and the filter is ready.

  Radar leads keep their measured speed. A missing lead resets the tracker so the
  next car ahead starts from a fresh distance history. distance_offset is the
  amount already removed from dRel for the stopped-lead gap, so the filter can
  watch the raw distance. A settings change in that gap is not a closing lead.
  """
  if not enabled or not lead.get("status") or lead.get("radar"):
    tracker.reset()
    return lead
  raw_distance = float(lead["dRel"]) + float(distance_offset)
  speed, ready = tracker.update(raw_distance, v_ego, lead["vLead"], lead.get("aLeadK", 0.0), dt)
  if not ready:
    return lead
  updated = dict(lead)
  updated["vLead"] = speed
  updated["vLeadK"] = speed
  updated["vRel"] = speed - float(v_ego)
  if tracker.published_accel is not None:
    updated["aLeadK"] = tracker.published_accel
  return updated
