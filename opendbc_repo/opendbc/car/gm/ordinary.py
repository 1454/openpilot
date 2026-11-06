"""Ordinary GM interception demand in normalized GM wire units."""

import math
import numpy as np
from opendbc.car.gm.long_tune import acc_tune_limits
from opendbc.car.gm.truck_longitudinal import (shape_truck_positive_accel, smooth_truck_follow_accel,
                                               shape_truck_pitch_accel, shape_truck_friction_brake)
from openpilot.selfdrive.controls.lib.gas_command import (
  grade_settings,
  limit_grade_feedforward,
  resolved_gas_model,
  stock_drag_and_area,
)


def demands(accel, speed, orientation, cp, *, min_gas=-650, max_gas=2041, inactive_gas=-650, brake_threshold=-0.1, stop_speed=0.25, acc_tune=False,
            truck_tuning=None, lead_visible=False, set_speed_error=0.0, stopping=False, gas_model=None):
  is_silverado = str(getattr(cp, "carFingerprint", "")) in ("CHEVROLET_SILVERADO", "CHEVROLET_SILVERADO_CC")
  stock_cd, stock_area = stock_drag_and_area(is_silverado, cp.wheelbase)
  stock_radius = 0.075 * cp.wheelbase + 0.1453
  mass, radius, coeff_drag, frontal, air_density = resolved_gas_model(
    cp.mass, stock_radius, stock_cd, stock_area, 1.225, gas_model
  )
  grade_gain, grade_uphill_cap = grade_settings(gas_model)
  pitch = 0.0
  if orientation is not None and len(orientation) == 3 and speed > stop_speed and math.isfinite(orientation[1]):
    pitch = math.sin(orientation[1]) * 9.81 * grade_gain
    if truck_tuning is not None:
      pitch = shape_truck_pitch_accel(pitch, speed, truck_tuning.enabled)
    pitch = limit_grade_feedforward(accel, pitch, grade_uphill_cap)
  maximum = acc_tune_limits(speed, 2., 6150, 6150)[0] if acc_tune else 2.
  accel_input = accel + pitch
  if truck_tuning is not None:
    accel_input = shape_truck_positive_accel(accel_input, speed, truck_tuning.enabled, lead_visible, set_speed_error)
  command = float(np.clip(accel_input, -4.0, maximum))
  if truck_tuning is not None:
    command = smooth_truck_follow_accel(command, truck_tuning.follow_accel, speed,
                                       truck_tuning.enabled, lead_visible, stopping)
    truck_tuning.follow_accel = command
  torque = radius * (mass * command + 0.5 * coeff_drag * frontal * air_density * speed ** 2)
  gas = int(round(np.clip(torque + 6150, min_gas + 6150, max_gas + 6150))) - 6150
  brake = int(round(np.interp(min(torque / (radius * mass), 0), [-4.0, brake_threshold], [400, 0])))
  if truck_tuning is not None:
    if truck_tuning.enabled:
      brake, truck_tuning.friction_active = shape_truck_friction_brake(
        brake, command, stopping, truck_tuning.friction_active)
    else:
      truck_tuning.friction_active = False
  return (inactive_gas if brake > 0 else gas), brake
