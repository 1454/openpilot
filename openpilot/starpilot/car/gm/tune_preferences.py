from opendbc.car.gm.long_tune import tune_options
from openpilot.starpilot.saved_source import read_saved

LB_TO_KG = 0.453592

KEY = "GMLongitudinalTune"
CHOICES = {0: "Vehicle Default", 1: "ACC Tune"}


def read_choice(params):
  try:
    raw, readable = read_saved(params, KEY, 8)
  except (OSError, RuntimeError, TypeError, ValueError):
    return 0, None, False, False
  values = {None: 0, b"0": 0, b"1": 1}
  return values.get(raw, 0), raw, readable, readable and raw in values


def selected_tune(params, *, enabled):
  try:
    value, _, _, valid = read_choice(params)
    safe, readable = read_saved(params, "SafeMode", 8)
    return value if enabled and valid and readable and safe in (None, b"0") else 0
  except (OSError, RuntimeError, TypeError, ValueError):
    return 0


class AccTunePreference:
  def __init__(self, cp, params):
    self.cp, self.params = cp, params
    self.read_ns = 0
    self.enabled = False

  def update(self, now_ns):
    if type(now_ns) is not int or now_ns <= 0 or 1 not in tune_options(self.cp):
      self.enabled = False
      return False
    if now_ns < self.read_ns:
      self.read_ns = 0
      self.enabled = False
      return False
    if self.read_ns == 0 or now_ns - self.read_ns >= 500_000_000:
      self.read_ns = now_ns
      try:
        master = read_saved(self.params, "OpenpilotEnabledToggle", 8) == (b"1", True)
        disabled, readable = read_saved(self.params, "DisableOpenpilotLongitudinal", 8)
        self.enabled = bool(readable and disabled in (None, b"0") and selected_tune(self.params, enabled=master) == 1)
      except (OSError, RuntimeError, TypeError, ValueError):
        self.enabled = False
    return self.enabled


TRUCK_KEY = "TruckTuning"


def read_truck_choice(params):
  raw, readable = read_saved(params, TRUCK_KEY, 8)
  valid = readable and raw in (None, b"0", b"1")
  return bool(valid and raw == b"1"), raw, readable, valid


class TruckTuningPreference:
  def __init__(self, cp, params):
    self.cp, self.params = cp, params
    self.read_ns = 0
    self.enabled = False

  def update(self, now_ns):
    from opendbc.car.gm.truck_longitudinal import truck_tuning_supported
    if type(now_ns) is not int or now_ns <= 0 or not truck_tuning_supported(self.cp):
      self.enabled = False
      return False
    if now_ns < self.read_ns:
      self.read_ns = 0
      self.enabled = False
      return False
    if self.read_ns == 0 or now_ns - self.read_ns >= 500_000_000:
      self.read_ns = now_ns
      try:
        enabled, _, _, valid = read_truck_choice(self.params)
        master = read_saved(self.params, "OpenpilotEnabledToggle", 8) == (b"1", True)
        safe, safe_readable = read_saved(self.params, "SafeMode", 8)
        disabled, disable_readable = read_saved(self.params, "DisableOpenpilotLongitudinal", 8)
        self.enabled = bool(enabled and valid and master and safe_readable and safe in (None, b"0") and
                            disable_readable and disabled in (None, b"0"))
      except (OSError, RuntimeError, TypeError, ValueError):
        self.enabled = False
    return self.enabled


class GradeCompensationPreference:
  """Worker-side live input selection; no CP/safety/tune/limiter mutation."""
  def __init__(self, params):
    self.params = params
    self.read_ns = 0
    self.enabled = True

  def update(self, now_ns):
    if type(now_ns) is not int or now_ns <= 0:
      return self.enabled
    if self.read_ns and 0 <= now_ns - self.read_ns < 1_000_000_000:
      return self.enabled
    self.read_ns = now_ns
    try:
      choice, readable = read_saved(self.params, 'LongPitch', 8)
      safe, safe_readable = read_saved(self.params, 'SafeMode', 8)
      master, master_readable = read_saved(self.params, 'OpenpilotEnabledToggle', 8)
      self.enabled = not (readable and choice == b'0' and master_readable and master == b'1' and
                          safe_readable and safe in (None, b'0'))
    except (OSError, RuntimeError, TypeError, ValueError):
      self.enabled = True
    return self.enabled


class GasModelPreference:
  """Worker-side live gas torque and grade model overrides; 0 or SafeMode keeps stock."""

  def __init__(self, params):
    self.params = params
    self.read_ns = 0
    self._reset_stock()

  def _reset_stock(self):
    self.gas_tire_radius = 0.0
    self.gas_vehicle_mass_kg = 0.0
    self.gas_drag_coeff = 0.0
    self.gas_frontal_area = 0.0
    self.gas_air_density = 0.0
    self.gas_grade_gain = 1.0
    self.gas_grade_uphill_cap = 0.20

  def _float_val(self, key: str, default: float) -> float:
    raw, readable = read_saved(self.params, key, 64)
    if not readable or raw is None:
      return default
    try:
      val = float(raw)
    except (TypeError, ValueError, OverflowError):
      return default
    import math
    return val if math.isfinite(val) else default

  def update(self, now_ns):
    if type(now_ns) is not int or now_ns <= 0:
      return self
    if now_ns < self.read_ns:
      self.read_ns = 0
      self._reset_stock()
      return self
    if self.read_ns == 0 or now_ns - self.read_ns >= 1_000_000_000:
      self.read_ns = now_ns
      try:
        safe, safe_readable = read_saved(self.params, "SafeMode", 8)
        if not safe_readable or safe not in (None, b"0"):
          self._reset_stock()
          return self
        self.gas_tire_radius = self._float_val("GasTireRadius", 0.0)
        self.gas_vehicle_mass_kg = self._float_val("GasVehicleWeight", 0.0) * LB_TO_KG
        self.gas_drag_coeff = self._float_val("GasDragCoeff", 0.0)
        self.gas_frontal_area = self._float_val("GasFrontalArea", 0.0)
        self.gas_air_density = self._float_val("GasAirDensity", 0.0)
        self.gas_grade_gain = self._float_val("GasGradeGain", 100.0) * 0.01
        self.gas_grade_uphill_cap = self._float_val("GasGradeUphillCap", 0.20)
      except (OSError, RuntimeError, TypeError, ValueError):
        self._reset_stock()
    return self
