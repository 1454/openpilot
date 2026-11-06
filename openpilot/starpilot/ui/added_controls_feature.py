"""Added Controls feature owner for driver monitoring/uploads, distance lead speed, Sierra lateral, and GM gas/grade sliders."""

from __future__ import annotations

from dataclasses import replace
import math

from openpilot.selfdrive.controls.lib.gas_command import stock_drag_and_area
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.ui.feature_settings_state import FeatureRow, FeatureSettingsRequest

LB_TO_KG = 0.453592
KG_TO_LB = 1.0 / LB_TO_KG

FALLBACK_WHEELBASE_M = 3.75
FALLBACK_MASS_KG = 2994.0 + 136.0
SIERRA_CENTER_TO_FRONT_RATIO = 0.75
STOCK_AIR_DENSITY = 1.225

TOGGLE_KEYS = ("EndDriverMonitoring", "LeadDistanceVelocity", "SierraLateral")
NUMERIC_KEYS = (
  "GasTireRadius",
  "CenterToFrontRatio",
  "GasVehicleWeight",
  "GasDragCoeff",
  "GasFrontalArea",
  "GasAirDensity",
  "GasGradeGain",
  "GasGradeUphillCap",
)
ADDED_KEYS = frozenset((*TOGGLE_KEYS, *NUMERIC_KEYS))

NUMERIC_SPECS = {
  "GasTireRadius": ("Tire Radius", 0.001, 0.25, 0.60, "m", 4),
  "CenterToFrontRatio": ("Center to Front", 0.01, 0.30, 0.80, "ratio", 2),
  "GasVehicleWeight": ("Weight", 10.0, 2500.0, 15000.0, "lb", 0),
  "GasDragCoeff": ("Drag Coefficient", 0.01, 0.15, 0.80, "Cd", 2),
  "GasFrontalArea": ("Frontal Area", 0.01, 1.50, 6.50, "m²", 2),
  "GasAirDensity": ("Air Density", 0.005, 0.90, 1.40, "kg/m³", 3),
  "GasGradeGain": ("Grade Strength", 5.0, 0.0, 200.0, "%", 0),
  "GasGradeUphillCap": ("Uphill Grade Cap", 0.01, 0.0, 1.00, "m/s²", 2),
}


def _tire_radius(wheelbase: float) -> float:
  return 0.075 * wheelbase + 0.1453


def _fmt(number: float, decimals: int) -> str:
  if decimals == 0:
    return f"{round(number):.0f}"
  return f"{round(number, decimals):g}"


class AddedControlsFeature:
  def __init__(self, owner):
    self.owner = owner

  def _cp(self):
    return self.owner.vehicle_params()

  def _is_silverado(self) -> bool:
    cp = self._cp()
    if cp is None:
      return True
    fp = str(getattr(cp, "carFingerprint", "") or "")
    return "SILVERADO" in fp or not fp

  def _wheelbase(self) -> float:
    cp = self._cp()
    wb = float(getattr(cp, "wheelbase", 0.0) or 0.0) if cp is not None else 0.0
    return wb if wb > 0.5 else FALLBACK_WHEELBASE_M

  def _stock_mass_kg(self) -> float:
    cp = self._cp()
    mass = float(getattr(cp, "mass", 0.0) or 0.0) if cp is not None else 0.0
    return mass if mass > 200.0 else FALLBACK_MASS_KG

  def _stock_center_ratio(self) -> float:
    cp = self._cp()
    wb = self._wheelbase()
    if cp is not None:
      center = float(getattr(cp, "centerToFront", 0.0) or 0.0)
      if 0.2 < center / wb < 0.9:
        return center / wb
    return 0.5

  def _sierra_enabled(self) -> bool:
    raw, readable = read_saved(self.owner.params, "SierraLateral", 8)
    return bool(readable and raw == b"1" and self._is_silverado())

  def _stock_display_number(self, key: str) -> float:
    wb = self._wheelbase()
    is_silverado = self._is_silverado()
    cd, area = stock_drag_and_area(is_silverado, wb)
    if key == "GasTireRadius":
      return round(_tire_radius(wb), 4)
    if key == "CenterToFrontRatio":
      return SIERRA_CENTER_TO_FRONT_RATIO if self._sierra_enabled() else round(self._stock_center_ratio(), 2)
    if key == "GasVehicleWeight":
      return float(round(self._stock_mass_kg() * KG_TO_LB / 10.0) * 10.0)
    if key == "GasDragCoeff":
      return round(cd, 2)
    if key == "GasFrontalArea":
      return round(area, 2)
    if key == "GasAirDensity":
      return STOCK_AIR_DENSITY
    if key == "GasGradeGain":
      return 100.0
    if key == "GasGradeUphillCap":
      return 0.20
    return 0.0

  def _bool_row(self, key: str, label: str, default_on: bool, allowed: bool, reason: str, *, dependencies=()) -> FeatureRow:
    raw, readable = read_saved(self.owner.params, key, 16)
    default_str = "On" if default_on else "Off"
    if raw is None and readable:
      val_str = default_str
      valid = True
    elif readable and raw in (b"0", b"1"):
      val_str = "On" if raw == b"1" else "Off"
      valid = True
    else:
      val_str = "Invalid saved value"
      valid = False
    return FeatureRow(
      key=key,
      label=label,
      value=val_str,
      source=raw,
      choices=("Off", "On") if valid else (),
      available=bool(allowed and valid),
      default_value=default_str,
      repair_value=default_str if (not valid and readable) else "",
      dependencies=dependencies,
      reason="Invalid saved value" if not valid else reason,
    )

  def _numeric_row(self, key: str, allowed: bool) -> FeatureRow:
    label, step, low, high, unit, decimals = NUMERIC_SPECS[key]
    raw, readable = read_saved(self.owner.params, key, 64)
    stock_num = self._stock_display_number(key)
    default_str = _fmt(stock_num, decimals)
    valid = readable
    display_num = stock_num
    if not readable:
      valid = False
    elif raw is not None:
      try:
        parsed = float(raw.decode("utf-8"))
        if not math.isfinite(parsed):
          valid = False
        elif key == "CenterToFrontRatio":
          if parsed == 0.0 or (self._sierra_enabled() and abs(parsed - round(self._stock_center_ratio(), 2)) < 1e-4):
            display_num = stock_num
          elif low <= parsed <= high:
            display_num = parsed
          else:
            valid = False
        elif key in ("GasGradeGain", "GasGradeUphillCap"):
          if low <= parsed <= high:
            display_num = parsed
          else:
            valid = False
        else:
          if parsed == 0.0:
            display_num = stock_num
          elif low <= parsed <= high:
            display_num = parsed
          else:
            valid = False
      except (UnicodeDecodeError, ValueError, OverflowError):
        valid = False

    wb = self._wheelbase()
    cd, area = stock_drag_and_area(self._is_silverado(), wb)
    reasons = {
      "GasTireRadius": f"Wheel radius in the gas torque command. Stock is {_tire_radius(wb):.4f} m.",
      "CenterToFrontRatio": (
        f"Front axle to center of gravity as a fraction of wheelbase ({wb:.2f} m). "
        f"Stock is {self._stock_center_ratio():.2f}; Sierra Lateral uses 0.75 until set."
      ),
      "GasVehicleWeight": (
        f"Vehicle weight in pounds for gas torque (and Silverado/Sierra steering model). "
        f"Stock is {round(self._stock_mass_kg() * KG_TO_LB):.0f} lb ({self._stock_mass_kg():.0f} kg)."
      ),
      "GasDragCoeff": f"Aerodynamic drag coefficient Cd in the gas torque command. Stock is {cd:.2f}.",
      "GasFrontalArea": f"Frontal area in the gas torque command. Stock is {area:.2f} m².",
      "GasAirDensity": f"Air density in the gas torque command. Stock is {STOCK_AIR_DENSITY:.3f} kg/m³.",
      "GasGradeGain": "Percentage of road pitch added to the gas command. 100% is stock; 0% ignores hills.",
      "GasGradeUphillCap": "Maximum uphill grade assist in the gas command (m/s²). Stock is 0.20 m/s².",
    }
    deps = (("SierraLateral", read_saved(self.owner.params, "SierraLateral", 8)[0]),) if key == "CenterToFrontRatio" else ()
    return FeatureRow(
      key=key,
      label=label,
      value=_fmt(display_num, decimals) if valid else "Invalid saved value",
      source=raw,
      step=step if valid else 0.0,
      minimum=low,
      maximum=high,
      unit=unit,
      available=bool(allowed and valid),
      default_value=default_str,
      repair_value=default_str if (not valid and readable) else "",
      dependencies=deps,
      reason="Invalid saved value" if not valid else reasons[key],
    )

  def rows(self, configurable: bool) -> list[FeatureRow]:
    allowed = bool(configurable and self.owner.authority("preferences"))
    no_uploads_raw, _ = read_saved(self.owner.params, "NoUploads", 16)
    rows: list[FeatureRow] = [
      FeatureRow("heading:added_toggles", "Toggles", ""),
      self._bool_row(
        "EndDriverMonitoring",
        "End Driver Monitoring",
        True,
        allowed,
        "On ends driver monitoring alerts and stops comma uploads. Off restores stock driver monitoring and restarts uploads.",
        dependencies=(("NoUploads", no_uploads_raw),),
      ),
      self._bool_row(
        "LeadDistanceVelocity",
        "Distance Lead Speed",
        False,
        allowed,
        "Estimates a vision lead's speed and hard braking from distance instead of the model speed. Radar leads keep radar speed.",
      ),
      self._bool_row(
        "SierraLateral",
        "Sierra Lateral",
        False,
        allowed,
        "Silverado and Sierra only. Uses the batmobile lateral set (17.6 ratio, 0.75 center, 0.27 s delay) and raises the high-speed steering limit.",
      ),
      FeatureRow("heading:added_gas", "Vehicle & Gas Torque", ""),
    ]
    for key in NUMERIC_KEYS:
      rows.append(self._numeric_row(key, allowed))
    return rows

  def apply(self, request: FeatureSettingsRequest) -> bool:
    key = request.key
    if key not in ADDED_KEYS or request.vehicle_fingerprint is not None or request.capability is not None:
      return False
    if not self.owner.authority("preferences"):
      return False
    if key in TOGGLE_KEYS:
      if request.value not in ("Off", "On"):
        return False
      raw_target = b"1" if request.value == "On" else b"0"
      if key == "EndDriverMonitoring":
        no_uploads_raw, no_uploads_ok = read_saved(self.owner.params, "NoUploads", 16)
        if not no_uploads_ok or request.dependencies != (("NoUploads", no_uploads_raw),):
          return False

        def authorized_dm() -> bool:
          cur_no_up, ok = read_saved(self.owner.params, "NoUploads", 16)
          return bool(self.owner.authority("preferences") and ok and cur_no_up == no_uploads_raw)

        res = commit_exact(
          self.owner.params, key=key, max_bytes=16, raw=raw_target,
          expected=request.expected, authorized=authorized_dm, temp_prefix=".added-dm-",
        )
        if not (res.committed and res.verified):
          return False
        self.owner.params.put_bool("NoUploads", request.value == "On", block=True)
        return True

      if request.dependencies:
        return False
      res = commit_exact(
        self.owner.params, key=key, max_bytes=16, raw=raw_target,
        expected=request.expected, authorized=lambda: self.owner.authority("preferences"),
        temp_prefix=".added-toggle-",
      )
      return bool(res.committed and res.verified)

    label, _step, low, high, _unit, decimals = NUMERIC_SPECS[key]
    del label
    if key == "CenterToFrontRatio":
      sierra_raw, sierra_ok = read_saved(self.owner.params, "SierraLateral", 8)
      if not sierra_ok or request.dependencies != (("SierraLateral", sierra_raw),):
        return False
    elif request.dependencies:
      return False
    try:
      number = float(request.value)
    except (TypeError, ValueError, OverflowError):
      return False
    if not math.isfinite(number):
      return False
    if not low <= number <= high:
      return False
    encoded = _fmt(number, decimals).encode("utf-8")

    def authorized_num() -> bool:
      if not self.owner.authority("preferences"):
        return False
      if key == "CenterToFrontRatio":
        cur_sierra, ok = read_saved(self.owner.params, "SierraLateral", 8)
        return bool(ok and (("SierraLateral", cur_sierra),) == request.dependencies)
      return True

    res = commit_exact(
      self.owner.params, key=key, max_bytes=64, raw=encoded,
      expected=request.expected, authorized=authorized_num, temp_prefix=".added-num-",
    )
    return bool(res.committed and res.verified)
