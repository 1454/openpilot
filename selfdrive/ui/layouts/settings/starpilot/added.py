from __future__ import annotations

from openpilot.system.ui.lib.multilang import tr_noop

from openpilot.selfdrive.ui.layouts.settings.starpilot.panel import _SettingsPage
from openpilot.selfdrive.ui.layouts.settings.starpilot.aethergrid import (
  AetherSettingsView,
  SettingRow,
  SettingSection,
)


class StarPilotAddedLayout(_SettingsPage):
  """Distance lead speed, Sierra lateral, and the driver-monitoring switch."""

  def __init__(self):
    super().__init__()
    p = self._params

    def set_end_driver_monitoring(state: bool):
      p.put_bool("EndDriverMonitoring", state)
      p.put_bool("NoUploads", state)

    rows = [
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
        subtitle=tr_noop("Silverado and Sierra only. Uses the batmobile lateral set and allows more steering angle at speed."),
        get_state=lambda: p.get_bool("SierraLateral"),
        set_state=lambda s: p.put_bool("SierraLateral", s),
      ),
    ]
    self._manager_view = AetherSettingsView(
      self,
      [SettingSection(title="", rows=rows)],
      header_title=tr_noop("Added Controls"),
      header_subtitle=tr_noop("Driver monitoring, distance lead speed, and Sierra lateral."),
    )
