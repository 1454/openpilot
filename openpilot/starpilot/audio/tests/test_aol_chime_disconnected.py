from types import SimpleNamespace
from unittest.mock import patch

from openpilot.starpilot.audio.axis_alerts import AudibleAlert, AxisAlerts


class _Sm(dict):
  def __init__(self, native):
    state = SimpleNamespace(alertType='', alertSound=native, enabled=False, active=False)
    super().__init__(selfdriveState=state)
    self.logMonoTime = {}
    self.valid = {'selfdriveState': True}
    self.alive = {'selfdriveState': True}


def test_aol_arm_and_drop_are_silent_and_cruise_chime_still_plays():
  alerts = AxisAlerts()
  intent = SimpleNamespace(
    producerSessionId='card', sequence=1, pauseLateral=False, lateralArmed=True, carStateLogMonoTime=1,
  )
  axis = SimpleNamespace(
    sessionId='selfdrived', sequence=2, sourceCarStateMonoTime=1, intentProducerSessionId='card',
    nativeAcknowledged=True, desiredLateral=True, lateralActive=True, observedMonoTime=1,
  )
  with patch('openpilot.starpilot.audio.axis_alerts.current_intent', return_value=intent), \
       patch('openpilot.starpilot.audio.axis_alerts.current_axis', return_value=axis):
    sm = _Sm(AudibleAlert.none)
    sm.logMonoTime = {'aolIntentWire': 1_000, 'aolAxisState': 1_000, 'selfdriveState': 1_000_000_000}
    assert alerts.update(sm, AudibleAlert.none, 1_000_000_000) == AudibleAlert.none
    intent.sequence = 2
    axis.sequence = 3
    assert alerts.update(sm, AudibleAlert.none, 1_050_000_000) == AudibleAlert.none
    assert alerts.update(sm, AudibleAlert.engage, 1_100_000_000) == AudibleAlert.engage
    assert alerts.update(sm, AudibleAlert.disengage, 1_150_000_000) == AudibleAlert.disengage
