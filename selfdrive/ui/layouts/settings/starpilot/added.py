from __future__ import annotations

from openpilot.common.constants import CV
from openpilot.system.ui.lib.multilang import tr_noop
from openpilot.selfdrive.controls.lib.gas_command import stock_drag_and_area

from openpilot.selfdrive.ui.layouts.settings.starpilot.panel import _SettingsPage
from openpilot.selfdrive.ui.layouts.settings.starpilot.aethergrid import (
  AetherSettingsView,
  SettingRow,
  SettingSection,
)

# Matches the Silverado curb weight plus the standard cargo allowance used in the gas command.
_FALLBACK_WHEELBASE_M = 3.75
_FALLBACK_MASS_KG = 2994.0 + 136.0
_SIERRA_CENTER_TO_FRONT_RATIO = 0.75
_STOCK_AIR_DENSITY = 1.225


def _tire_radius(wheelbase: float) -> float:
  return 0.075 * wheelbase + 0.1453


class StarPilotAddedLayout(_SettingsPage):
  """Added toggles plus the gas-torque and vehicle-model adjustments."""

  def __init__(self):
    super().__init__()
    p = self._params
    self._cp_raw = None
    self._cp = None

    def set_end_driver_monitoring(state: bool):
      p.put_bool("EndDriverMonitoring", state)
      p.put_bool("NoUploads", state)

    toggles = [
      SettingRow(
        "EndDriverMonitoring", "toggle", tr_noop("End Driver Monitoring"),
        subtitle=tr_noop("On stops driver monitoring and comma uploads. Off restores driver monitoring and restarts uploads."),
        get_state=lambda: p.get_bool("EndDriverMonitoring"),
        set_state=set_end_driver_monitoring,
      ),
      SettingRow(
        "LeadDistanceVelocity", "toggle", tr_noop("Distance Lead Speed"),
        subtitle=tr_noop("Estimate the lead car's speed from its distance instead of the model speed."),
        get_state=lambda: p.get_bool("LeadDistanceVelocity"),
        set_state=lambda s: p.put_bool("LeadDistanceVelocity", s),
      ),
      SettingRow(
        "SierraLateral", "toggle", tr_noop("Sierra Lateral"),
        subtitle=tr_noop("Silverado and Sierra only. Uses the batmobile lateral set and allows more steering angle at speed. Center to front stays 0.75 until you set that value below."),
        get_state=lambda: p.get_bool("SierraLateral"),
        set_state=lambda s: p.put_bool("SierraLateral", s),
      ),
    ]
    vehicle = [
      SettingRow(
        "GasTireRadius", "value", tr_noop("Tire Radius"),
        subtitle=tr_noop("Wheel radius in the gas torque command. The shown value is stock until you change it."),
        get_value=self._tire_radius_label,
        on_click=lambda: self._show_slider(
          "GasTireRadius", 0.25, 0.60, step=0.001, unit=" m", value_type="float",
          current_value=self._effective_tire_radius(), title=tr_noop("Tire Radius"),
        ),
      ),
      SettingRow(
        "CenterToFrontRatio", "value", tr_noop("Center to Front"),
        subtitle=tr_noop("Fraction of the wheelbase from the front axle to the center of gravity. Sierra Lateral uses 0.75 until this is set."),
        get_value=self._center_label,
        on_click=lambda: self._show_slider(
          "CenterToFrontRatio", 0.30, 0.80, step=0.01, value_type="float",
          current_value=self._effective_center_ratio(), title=tr_noop("Center to Front"),
        ),
      ),
      SettingRow(
        "GasVehicleWeight", "value", tr_noop("Weight"),
        subtitle=tr_noop("Vehicle weight used to turn acceleration into gas torque, in pounds. Includes the usual cargo allowance until you change it."),
        get_value=self._weight_label,
        on_click=lambda: self._show_slider(
          "GasVehicleWeight", 2500, 15000, step=10, unit=" lb", value_type="float",
          current_value=self._effective_weight_lb(), title=tr_noop("Weight"),
        ),
      ),
      SettingRow(
        "GasDragCoeff", "value", tr_noop("Drag Coefficient"),
        subtitle=tr_noop("Cd in the gas torque command. Stock is 0.38 on the Silverado (published) and 0.30 on other cars."),
        get_value=self._drag_label,
        on_click=lambda: self._show_slider(
          "GasDragCoeff", 0.15, 0.80, step=0.01, value_type="float",
          current_value=self._effective_drag(), title=tr_noop("Drag Coefficient"),
        ),
      ),
      SettingRow(
        "GasFrontalArea", "value", tr_noop("Frontal Area"),
        subtitle=tr_noop("Frontal area in the gas torque command. Stock is 3.4 m² on the Silverado and a wheelbase estimate on other cars."),
        get_value=self._area_label,
        on_click=lambda: self._show_slider(
          "GasFrontalArea", 1.5, 6.5, step=0.01, unit=" m²", value_type="float",
          current_value=self._effective_area(), title=tr_noop("Frontal Area"),
        ),
      ),
      SettingRow(
        "GasAirDensity", "value", tr_noop("Air Density"),
        subtitle=tr_noop("Air density in the gas torque command. Stock is 1.225 kg/m³."),
        get_value=self._density_label,
        on_click=lambda: self._show_slider(
          "GasAirDensity", 0.90, 1.40, step=0.005, unit=" kg/m³", value_type="float",
          current_value=self._effective_density(), title=tr_noop("Air Density"),
        ),
      ),
      SettingRow(
        "GasGradeGain", "value", tr_noop("Grade Strength"),
        subtitle=tr_noop("How much of the hill pitch is added to the gas command. 100% is stock, 0% ignores hills."),
        get_value=lambda: f"{self._grade_gain():.0f}%",
        on_click=lambda: self._show_slider(
          "GasGradeGain", 0, 200, step=5, unit="%", value_type="float",
          current_value=self._grade_gain(), title=tr_noop("Grade Strength"),
        ),
      ),
      SettingRow(
        "GasGradeUphillCap", "value", tr_noop("Uphill Grade Cap"),
        subtitle=tr_noop("Most extra acceleration the uphill term can add. Stock is 0.20 m/s². Downhill help is not capped."),
        get_value=lambda: f"{self._grade_cap():.2f} m/s²",
        on_click=lambda: self._show_slider(
          "GasGradeUphillCap", 0.0, 1.0, step=0.01, unit=" m/s²", value_type="float",
          current_value=self._grade_cap(), title=tr_noop("Uphill Grade Cap"),
        ),
      ),
    ]
    self._manager_view = AetherSettingsView(
      self,
      [
        SettingSection(title=tr_noop("Toggles"), rows=toggles),
        SettingSection(title=tr_noop("Vehicle and gas torque"), rows=vehicle),
      ],
      header_title=tr_noop("Added Controls"),
      header_subtitle=tr_noop("Driver monitoring, distance lead speed, Sierra lateral, and the gas torque model."),
    )

  def _car_params(self):
    raw = self._params.get("CarParams")
    if raw == self._cp_raw:
      return self._cp
    self._cp_raw = raw
    self._cp = None
    if not raw:
      return None
    try:
      from cereal import car
      import cereal.messaging as messaging
      self._cp = messaging.log_from_bytes(raw, car.CarParams)
    except Exception:
      self._cp = None
    return self._cp

  def _wheelbase(self) -> float:
    cp = self._car_params()
    wheelbase = float(getattr(cp, "wheelbase", 0.0) or 0.0) if cp is not None else 0.0
    return wheelbase if wheelbase > 1.0 else _FALLBACK_WHEELBASE_M

  def _stock_mass_kg(self) -> float:
    cp = self._car_params()
    mass = float(getattr(cp, "mass", 0.0) or 0.0) if cp is not None else 0.0
    return mass if mass > 200.0 else _FALLBACK_MASS_KG

  def _stock_center_ratio(self) -> float:
    cp = self._car_params()
    wheelbase = float(getattr(cp, "wheelbase", 0.0) or 0.0) if cp is not None else 0.0
    center = float(getattr(cp, "centerToFront", 0.0) or 0.0) if cp is not None else 0.0
    if wheelbase > 1.0 and center > 0.0:
      return center / wheelbase
    return 0.5

  def _sierra_center_active(self) -> bool:
    if not self._params.get_bool("SierraLateral"):
      return False
    cp = self._car_params()
    if cp is None:
      return True
    return "SILVERADO" in str(getattr(cp, "carFingerprint", ""))

  def _stored(self, key: str) -> float:
    try:
      return float(self._params.get_float(key) or 0.0)
    except (TypeError, ValueError):
      return 0.0

  def _effective_tire_radius(self) -> float:
    stored = self._stored("GasTireRadius")
    return stored if stored > 0.0 else _tire_radius(self._wheelbase())

  def _effective_center_ratio(self) -> float:
    stored = self._stored("CenterToFrontRatio")
    if stored > 0.0:
      return stored
    if self._sierra_center_active():
      return _SIERRA_CENTER_TO_FRONT_RATIO
    return self._stock_center_ratio()

  def _effective_weight_lb(self) -> float:
    stored = self._stored("GasVehicleWeight")
    if stored > 0.0:
      return stored
    return self._stock_mass_kg() / CV.LB_TO_KG

  def _is_silverado(self) -> bool:
    cp = self._car_params()
    if cp is None:
      return True
    return "SILVERADO" in str(getattr(cp, "carFingerprint", "")).upper()

  def _effective_drag(self) -> float:
    stored = self._stored("GasDragCoeff")
    return stored if stored > 0.0 else stock_drag_and_area(self._is_silverado(), self._wheelbase())[0]

  def _effective_area(self) -> float:
    stored = self._stored("GasFrontalArea")
    return stored if stored > 0.0 else stock_drag_and_area(self._is_silverado(), self._wheelbase())[1]

  def _effective_density(self) -> float:
    stored = self._stored("GasAirDensity")
    return stored if stored > 0.0 else _STOCK_AIR_DENSITY

  def _tire_radius_label(self) -> str:
    return f"{self._effective_tire_radius():.3f} m"

  def _center_label(self) -> str:
    return f"{self._effective_center_ratio():.2f}"

  def _weight_label(self) -> str:
    return f"{self._effective_weight_lb():.0f} lb"

  def _drag_label(self) -> str:
    return f"{self._effective_drag():.2f}"

  def _area_label(self) -> str:
    return f"{self._effective_area():.2f} m²"

  def _density_label(self) -> str:
    return f"{self._effective_density():.3f}"

  def _grade_gain(self) -> float:
    return float(self._params.get_float("GasGradeGain"))

  def _grade_cap(self) -> float:
    return float(self._params.get_float("GasGradeUphillCap"))
