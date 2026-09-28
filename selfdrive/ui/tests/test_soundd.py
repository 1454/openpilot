from types import SimpleNamespace

from cereal import custom, log
from cereal import messaging
from cereal.messaging import SubMaster, PubMaster
from openpilot.selfdrive.ui.soundd import (
  MIC_DEAD_DB,
  SELFDRIVE_STATE_TIMEOUT,
  SOUNDD_SERVICES,
  Soundd,
  alert_gain_from_weighted_db,
  check_selfdrive_timeout_alert,
  is_turn_steering_limit_alert,
  should_mute_turn_steering_limit_alert,
  starpilot_alert_key,
)

import numpy as np
import pytest
import time
import wave

AudibleAlert = log.SelfdriveState.AudibleAlert
StarPilotAudibleAlert = custom.StarPilotCarControl.HUDControl.AudibleAlert


class _SounddAttributeSpy:
  def __init__(self, soundd):
    self.soundd = soundd
    self.alert_assignments: list[tuple[object, float]] = []
    self.volume_writes: list[tuple[float, object]] = []
    self._cls = type(soundd)
    self._orig_setattr = self._cls.__setattr__

  def __enter__(self):
    spy = self

    def setattr_hook(obj, name, value):
      if obj is spy.soundd:
        if name == "current_alert":
          spy.alert_assignments.append((value, obj.current_volume))
        elif name == "current_volume":
          alert = obj.__dict__.get("current_alert", AudibleAlert.none)
          spy.volume_writes.append((value, alert))
      return spy._orig_setattr(obj, name, value)

    self._cls.__setattr__ = setattr_hook
    return self

  def __exit__(self, *_exc):
    self._cls.__setattr__ = self._orig_setattr


def assert_nonzero_volume_when_alert_assigned(assignments, *, expected_volume=None):
  non_none = [(alert, volume) for alert, volume in assignments if alert != AudibleAlert.none]
  assert non_none, "expected at least one non-none alert assignment"
  for _alert, volume in non_none:
    assert volume != 0.0
    if expected_volume is not None:
      assert volume == pytest.approx(expected_volume)


def assert_no_zero_volume_while_alert_active(volume_writes):
  bad = [(volume, alert) for volume, alert in volume_writes
         if volume == 0.0 and alert != AudibleAlert.none]
  assert not bad, f"current_volume must not be 0 while an alert is active: {bad}"


class TestSoundd:
  def test_does_not_consume_car_state_reader(self):
    assert "carState" not in SOUNDD_SERVICES
    assert "starpilotSelfdriveState" in SOUNDD_SERVICES

  def test_turn_steering_limit_alert_detection(self):
    assert is_turn_steering_limit_alert("steerSaturated/warning")
    assert is_turn_steering_limit_alert("goatSteerSaturated/warning")
    assert is_turn_steering_limit_alert("thisIsFineSteerSaturated/warning")
    assert not is_turn_steering_limit_alert("laneChangeBlocked/warning")

  def test_turn_steering_limit_alert_is_muted_only_below_threshold(self):
    assert should_mute_turn_steering_limit_alert("steerSaturated/warning", 10.0, 25.0)
    assert not should_mute_turn_steering_limit_alert("steerSaturated/warning", 25.0, 25.0)
    assert not should_mute_turn_steering_limit_alert("steerSaturated/warning", 30.0, 25.0)
    assert not should_mute_turn_steering_limit_alert("steerSaturated/warning", 10.0, 0.0)
    assert not should_mute_turn_steering_limit_alert("laneChangeBlocked/warning", 10.0, 25.0)

  def test_load_sounds_skips_missing_custom_clips(self, tmp_path):
    soundd = Soundd.__new__(Soundd)
    soundd.sound_directory = tmp_path / "sounds"
    soundd.sound_directory.mkdir()
    soundd.random_events_directory = tmp_path / "random_events"
    soundd.random_events_directory.mkdir()

    soundd.load_sounds()

    assert AudibleAlert.engage in soundd.loaded_sounds
    assert AudibleAlert.warningImmediate in soundd.loaded_sounds
    assert starpilot_alert_key(StarPilotAudibleAlert.angry) not in soundd.loaded_sounds
    soundd.current_alert = starpilot_alert_key(StarPilotAudibleAlert.angry)
    soundd.current_volume = 1.0
    soundd.current_sound_frame = 0
    np.testing.assert_array_equal(soundd.get_sound_data(4), np.zeros(4, dtype=np.float32))

  def test_load_sounds_falls_back_to_stock_when_custom_is_invalid(self, tmp_path):
    soundd = Soundd.__new__(Soundd)
    soundd.sound_directory = tmp_path / "sounds"
    soundd.sound_directory.mkdir()
    soundd.random_events_directory = tmp_path / "random_events"
    soundd.random_events_directory.mkdir()

    invalid = soundd.sound_directory / "warning_immediate.wav"
    with wave.open(str(invalid), "w") as wav:
      wav.setnchannels(2)
      wav.setsampwidth(2)
      wav.setframerate(44100)
      wav.writeframes(b"\x00\x00" * 64)

    soundd.load_sounds()

    assert AudibleAlert.warningImmediate in soundd.loaded_sounds
    assert soundd.loaded_sounds[AudibleAlert.warningImmediate].size > 0

  def test_load_sounds_skips_empty_wav(self, tmp_path):
    soundd = Soundd.__new__(Soundd)
    soundd.sound_directory = tmp_path / "sounds"
    soundd.sound_directory.mkdir()
    soundd.random_events_directory = tmp_path / "random_events"
    soundd.random_events_directory.mkdir()

    empty = soundd.sound_directory / "engage.wav"
    with wave.open(str(empty), "w") as wav:
      wav.setnchannels(1)
      wav.setsampwidth(2)
      wav.setframerate(48000)
      wav.writeframes(b"")

    soundd.load_sounds()

    assert AudibleAlert.engage in soundd.loaded_sounds
    assert soundd.loaded_sounds[AudibleAlert.engage].size > 0

  def test_load_sounds_falls_back_when_custom_wav_is_truncated(self, tmp_path):
    soundd = Soundd.__new__(Soundd)
    soundd.sound_directory = tmp_path / "sounds"
    soundd.sound_directory.mkdir()
    soundd.random_events_directory = tmp_path / "random_events"
    soundd.random_events_directory.mkdir()

    (soundd.sound_directory / "warning_immediate.wav").write_bytes(b"RIFF")

    soundd.load_sounds()

    assert AudibleAlert.warningImmediate in soundd.loaded_sounds
    assert soundd.loaded_sounds[AudibleAlert.warningImmediate].size > 0

  def test_load_sounds_falls_back_when_custom_wav_has_odd_payload(self, tmp_path):
    soundd = Soundd.__new__(Soundd)
    soundd.sound_directory = tmp_path / "sounds"
    soundd.sound_directory.mkdir()
    soundd.random_events_directory = tmp_path / "random_events"
    soundd.random_events_directory.mkdir()

    odd = soundd.sound_directory / "warning_immediate.wav"
    with wave.open(str(odd), "w") as wav:
      wav.setnchannels(1)
      wav.setsampwidth(2)
      wav.setframerate(48000)
      wav.writeframes(b"\x00\x00\x00\x00")
    odd.write_bytes(odd.read_bytes()[:-1])

    soundd.load_sounds()

    assert AudibleAlert.warningImmediate in soundd.loaded_sounds
    assert soundd.loaded_sounds[AudibleAlert.warningImmediate].size > 0

  def test_missing_goat_keeps_stock_critical_alert(self, tmp_path):
    soundd = Soundd.__new__(Soundd)
    soundd.sound_directory = tmp_path / "sounds"
    soundd.sound_directory.mkdir()
    soundd.random_events_directory = tmp_path / "random_events"
    soundd.random_events_directory.mkdir()
    soundd.load_sounds()

    goat_alert = starpilot_alert_key(StarPilotAudibleAlert.goat)
    assert goat_alert not in soundd.loaded_sounds
    assert AudibleAlert.warningImmediate in soundd.loaded_sounds

    assert soundd.select_critical_alert(AudibleAlert.warningImmediate, True) == AudibleAlert.warningImmediate

    soundd.loaded_sounds[goat_alert] = soundd.loaded_sounds[AudibleAlert.warningImmediate]
    assert soundd.select_critical_alert(AudibleAlert.warningImmediate, True) == goat_alert
    assert soundd.select_critical_alert(AudibleAlert.warningImmediate, False) == AudibleAlert.warningImmediate

  def test_bluetooth_submit_success_still_outputs_local_pcm(self):
    soundd = Soundd.__new__(Soundd)
    samples = np.array([0.25, -0.5], dtype=np.float32)
    soundd.get_sound_data = lambda _frames: samples
    data_out = np.zeros((2, 1), dtype=np.float32)
    soundd.pending_stream_status = None

    soundd.bluetooth_audio = type("Sink", (), {"submit": lambda self, _samples: True})()
    soundd.callback(data_out, 2, None, None)
    np.testing.assert_array_equal(data_out[:, 0], samples)

    soundd.bluetooth_audio = type("Sink", (), {"submit": lambda self, _samples: False})()
    soundd.callback(data_out, 2, None, None)
    np.testing.assert_array_equal(data_out[:, 0], samples)

  def test_alert_volume_controller_first_alert_frame_is_audible(self):
    soundd = Soundd.__new__(Soundd)
    soundd.params_memory = type("Params", (), {"get": lambda self, _key: None, "remove": lambda self, _key: None})()
    soundd.error_log = type("Path", (), {"is_file": lambda self: False})()
    soundd.openpilot_crashed_played = False
    soundd.selfdrive_timeout_alert = False
    soundd.starpilot_toggles = SimpleNamespace(
      alert_volume_controller=True,
      goat_scream_critical_alerts=False,
      turn_steering_limit_mute_speed=0.0,
      engage_volume=80,
      disengage_volume=80,
      refuse_volume=80,
      prompt_volume=80,
      promptDistracted_volume=80,
      warningSoft_volume=80,
      warningImmediate_volume=80,
      below_steer_speed_volume=80,
    )
    soundd.volume_map = {AudibleAlert.engage: 0.8}
    soundd.current_alert = AudibleAlert.none
    soundd.current_alert_type = ""
    soundd.current_volume = 0.0
    soundd.auto_volume = 0.2
    soundd.loaded_sounds = {AudibleAlert.engage: np.ones(4800, dtype=np.float32)}
    soundd.current_sound_frame = 0
    soundd.model_ready_pending = False
    soundd.model_ready_last_check = 0.0
    soundd.model_ready_params = SimpleNamespace(
      get_bool=lambda _key: False,
    )
    soundd.model_ready_chime = type("Chime", (), {"update": lambda *args, **kwargs: False, "consume": lambda self: None})()

    sm = {
      "selfdriveState": SimpleNamespace(
        alertSound=SimpleNamespace(raw=AudibleAlert.engage),
        alertType="engage",
        alertStatus=log.SelfdriveState.AlertStatus.normal,
        alertSize=log.SelfdriveState.AlertSize.none,
      ),
      "starpilotSelfdriveState": SimpleNamespace(
        vEgo=20.0,
        alertSound=SimpleNamespace(raw=StarPilotAudibleAlert.none),
        alertType="",
      ),
    }
    sm = type("SounddSM", (dict,), {
      "updated": {"selfdriveState": True, "starpilotSelfdriveState": False, "soundPressure": False},
      "valid": {"selfdriveState": True, "starpilotSelfdriveState": True},
      "alive": {"selfdriveState": True},
    })(sm)

    with _SounddAttributeSpy(soundd) as spy:
      soundd.update_audible_frame(sm)

    assert soundd.current_alert == AudibleAlert.engage
    assert soundd.current_volume == pytest.approx(0.8)
    assert_nonzero_volume_when_alert_assigned(spy.alert_assignments, expected_volume=0.8)

  def test_error_log_alert_sets_volume_when_prompt_plays(self):
    soundd = Soundd.__new__(Soundd)
    soundd.params_memory = type("Params", (), {"get": lambda self, _key: None, "remove": lambda self, _key: None})()
    soundd.error_log = type("Path", (), {"is_file": lambda self: True})()
    soundd.openpilot_crashed_played = False
    soundd.selfdrive_timeout_alert = False
    soundd.starpilot_toggles = SimpleNamespace(
      alert_volume_controller=True,
      goat_scream_critical_alerts=False,
      turn_steering_limit_mute_speed=0.0,
      prompt_volume=80,
    )
    soundd.volume_map = {AudibleAlert.prompt: 0.8}
    soundd.current_alert = AudibleAlert.none
    soundd.current_alert_type = ""
    soundd.current_volume = 0.0
    soundd.auto_volume = 0.2
    soundd.loaded_sounds = {AudibleAlert.prompt: np.ones(4800, dtype=np.float32)}
    soundd.current_sound_frame = 0

    sm = {
      "starpilotSelfdriveState": SimpleNamespace(vEgo=20.0),
    }
    sm = type("SounddSM", (dict,), {
      "updated": {"selfdriveState": False, "starpilotSelfdriveState": False, "soundPressure": False},
      "valid": {"selfdriveState": True, "starpilotSelfdriveState": True},
      "alive": {"selfdriveState": True},
    })(sm)

    with _SounddAttributeSpy(soundd) as spy:
      soundd.get_audible_alert(sm)

    assert soundd.current_alert == AudibleAlert.prompt
    assert soundd.current_volume == pytest.approx(0.8)
    assert soundd.openpilot_crashed_played is True
    assert_nonzero_volume_when_alert_assigned(spy.alert_assignments, expected_volume=0.8)

  def test_alert_volume_controller_survives_same_frame_sound_pressure_update(self):
    soundd = Soundd.__new__(Soundd)
    soundd.params_memory = type("Params", (), {"get": lambda self, _key: None, "remove": lambda self, _key: None})()
    soundd.error_log = type("Path", (), {"is_file": lambda self: False})()
    soundd.openpilot_crashed_played = False
    soundd.selfdrive_timeout_alert = False
    soundd.starpilot_toggles = SimpleNamespace(
      alert_volume_controller=True,
      goat_scream_critical_alerts=False,
      turn_steering_limit_mute_speed=0.0,
      engage_volume=80,
      disengage_volume=80,
      refuse_volume=80,
      prompt_volume=80,
      promptDistracted_volume=80,
      warningSoft_volume=80,
      warningImmediate_volume=80,
      below_steer_speed_volume=80,
    )
    soundd.volume_map = {AudibleAlert.engage: 0.8}
    soundd.current_alert = AudibleAlert.none
    soundd.current_alert_type = ""
    soundd.current_volume = 0.0
    soundd.auto_volume = 0.2
    soundd.loaded_sounds = {AudibleAlert.engage: np.ones(4800, dtype=np.float32)}
    soundd.current_sound_frame = 0
    soundd.model_ready_pending = False
    soundd.model_ready_last_check = 0.0
    soundd.model_ready_params = SimpleNamespace(get_bool=lambda _key: False)
    soundd.model_ready_chime = type("Chime", (), {"update": lambda *args, **kwargs: False, "consume": lambda self: None})()
    soundd.spl_filter_weighted = type("Filter", (), {"update": lambda self, _value: None, "x": 40.0})()

    sm = {
      "selfdriveState": SimpleNamespace(
        alertSound=SimpleNamespace(raw=AudibleAlert.engage),
        alertType="engage",
        alertStatus=log.SelfdriveState.AlertStatus.normal,
        alertSize=log.SelfdriveState.AlertSize.none,
      ),
      "starpilotSelfdriveState": SimpleNamespace(
        vEgo=20.0,
        alertSound=SimpleNamespace(raw=StarPilotAudibleAlert.none),
        alertType="",
      ),
      "soundPressure": SimpleNamespace(soundPressureWeightedDb=40.0),
    }
    sm = type("SounddSM", (dict,), {
      "updated": {"selfdriveState": True, "starpilotSelfdriveState": False, "soundPressure": True},
      "valid": {"selfdriveState": True, "starpilotSelfdriveState": True},
      "alive": {"selfdriveState": True},
    })(sm)

    with _SounddAttributeSpy(soundd) as spy:
      soundd.update_audible_frame(sm)

    assert soundd.current_alert == AudibleAlert.engage
    assert soundd.current_volume == pytest.approx(0.8)
    assert_nonzero_volume_when_alert_assigned(spy.alert_assignments, expected_volume=0.8)
    assert_no_zero_volume_while_alert_active(spy.volume_writes)

  def test_stock_alert_wins_when_both_sources_have_sound(self):
    soundd = Soundd.__new__(Soundd)
    soundd.params_memory = type("Params", (), {"get": lambda self, _key: None, "remove": lambda self, _key: None})()
    soundd.error_log = type("Path", (), {"is_file": lambda self: False})()
    soundd.openpilot_crashed_played = False
    soundd.selfdrive_timeout_alert = False
    soundd.starpilot_toggles = SimpleNamespace(
      alert_volume_controller=False,
      goat_scream_critical_alerts=False,
      turn_steering_limit_mute_speed=0.0,
    )
    soundd.volume_map = {}
    soundd.current_alert = AudibleAlert.none
    soundd.current_alert_type = ""
    soundd.current_volume = 1.0
    soundd.auto_volume = 0.5
    angry_key = starpilot_alert_key(StarPilotAudibleAlert.angry)
    soundd.loaded_sounds = {
      AudibleAlert.engage: np.ones(4800, dtype=np.float32),
      angry_key: np.ones(4800, dtype=np.float32),
    }
    soundd.current_sound_frame = 0
    soundd.model_ready_pending = False
    soundd.model_ready_last_check = 0.0
    soundd.model_ready_params = SimpleNamespace(get_bool=lambda _key: False)
    soundd.model_ready_chime = type("Chime", (), {"update": lambda *args, **kwargs: False, "consume": lambda self: None})()

    sm = {
      "selfdriveState": SimpleNamespace(
        alertSound=SimpleNamespace(raw=AudibleAlert.engage),
        alertType="engage",
        alertStatus=log.SelfdriveState.AlertStatus.normal,
        alertSize=log.SelfdriveState.AlertSize.none,
      ),
      "starpilotSelfdriveState": SimpleNamespace(
        vEgo=20.0,
        alertSound=SimpleNamespace(raw=StarPilotAudibleAlert.angry),
        alertType="angry/warning",
      ),
    }
    sm = type("SounddSM", (dict,), {
      "updated": {"selfdriveState": True, "starpilotSelfdriveState": True, "soundPressure": False},
      "valid": {"selfdriveState": True, "starpilotSelfdriveState": True},
      "alive": {"selfdriveState": True},
    })(sm)

    soundd.get_audible_alert(sm)

    assert soundd.current_alert == AudibleAlert.engage
    assert soundd.current_alert_type == "engage"
    assert soundd.current_alert != angry_key

  def test_volume_map_miss_uses_auto_volume_with_alert_volume_controller(self):
    soundd = Soundd.__new__(Soundd)
    soundd.params_memory = type("Params", (), {"get": lambda self, _key: None, "remove": lambda self, _key: None})()
    soundd.error_log = type("Path", (), {"is_file": lambda self: False})()
    soundd.openpilot_crashed_played = False
    soundd.selfdrive_timeout_alert = False
    soundd.starpilot_toggles = SimpleNamespace(
      alert_volume_controller=True,
      goat_scream_critical_alerts=False,
      turn_steering_limit_mute_speed=25.0,
      engage_volume=80,
      disengage_volume=80,
      refuse_volume=80,
      prompt_volume=80,
      promptDistracted_volume=80,
      warningSoft_volume=80,
      warningImmediate_volume=80,
      below_steer_speed_volume=80,
    )
    soundd.volume_map = {}
    soundd.current_alert = AudibleAlert.none
    soundd.current_alert_type = "engage"
    soundd.current_volume = 0.0
    soundd.auto_volume = 0.37
    soundd.loaded_sounds = {AudibleAlert.engage: np.ones(4800, dtype=np.float32)}
    soundd.current_sound_frame = 0
    soundd.model_ready_pending = False
    soundd.model_ready_last_check = 0.0
    soundd.model_ready_params = SimpleNamespace(get_bool=lambda _key: False)
    soundd.model_ready_chime = type("Chime", (), {"update": lambda *args, **kwargs: False, "consume": lambda self: None})()

    sm = {
      "selfdriveState": SimpleNamespace(
        alertSound=SimpleNamespace(raw=AudibleAlert.engage),
        alertType="engage",
        alertStatus=log.SelfdriveState.AlertStatus.normal,
        alertSize=log.SelfdriveState.AlertSize.none,
      ),
      "starpilotSelfdriveState": SimpleNamespace(
        vEgo=30.0,
        alertSound=SimpleNamespace(raw=StarPilotAudibleAlert.none),
        alertType="",
      ),
    }
    sm = type("SounddSM", (dict,), {
      "updated": {"selfdriveState": True, "starpilotSelfdriveState": False, "soundPressure": False},
      "valid": {"selfdriveState": True, "starpilotSelfdriveState": True},
      "alive": {"selfdriveState": True},
    })(sm)

    assert soundd.get_volume_override(AudibleAlert.engage) == pytest.approx(1.01)

    soundd.update_audible_frame(sm)

    assert soundd.current_alert == AudibleAlert.engage
    assert soundd.current_volume == pytest.approx(0.37)
    assert soundd.current_volume != 0.0
    assert soundd.current_volume != pytest.approx(1.01)

  def test_starpilot_selfdrive_state_update_plays_starpilot_alert(self):
    soundd = Soundd.__new__(Soundd)
    soundd.params_memory = type("Params", (), {"get": lambda self, _key: None, "remove": lambda self, _key: None})()
    soundd.error_log = type("Path", (), {"is_file": lambda self: False})()
    soundd.openpilot_crashed_played = False
    soundd.selfdrive_timeout_alert = False
    soundd.starpilot_toggles = SimpleNamespace(
      alert_volume_controller=False,
      goat_scream_critical_alerts=False,
      turn_steering_limit_mute_speed=0.0,
    )
    soundd.volume_map = {}
    soundd.current_alert = AudibleAlert.none
    soundd.current_alert_type = ""
    soundd.current_volume = 1.0
    soundd.auto_volume = 0.5
    soundd.loaded_sounds = {}
    angry_key = starpilot_alert_key(StarPilotAudibleAlert.angry)
    soundd.loaded_sounds[angry_key] = np.ones(4800, dtype=np.float32)
    soundd.current_sound_frame = 0
    soundd.model_ready_pending = False
    soundd.model_ready_last_check = 0.0
    soundd.model_ready_params = SimpleNamespace(get_bool=lambda _key: False)
    soundd.model_ready_chime = type("Chime", (), {"update": lambda *args, **kwargs: False, "consume": lambda self: None})()

    sm = {
      "selfdriveState": SimpleNamespace(
        alertSound=SimpleNamespace(raw=AudibleAlert.none),
        alertType="",
        alertStatus=log.SelfdriveState.AlertStatus.normal,
        alertSize=log.SelfdriveState.AlertSize.none,
      ),
      "starpilotSelfdriveState": SimpleNamespace(
        vEgo=20.0,
        alertSound=SimpleNamespace(raw=StarPilotAudibleAlert.angry),
        alertType="angry/warning",
      ),
    }
    sm = type("SounddSM", (dict,), {
      "updated": {"selfdriveState": False, "starpilotSelfdriveState": True, "soundPressure": False},
      "valid": {"selfdriveState": True, "starpilotSelfdriveState": True},
      "alive": {"selfdriveState": True},
    })(sm)

    soundd.get_audible_alert(sm)

    assert soundd.current_alert == angry_key
    assert soundd.current_alert_type == "angry/warning"

  def test_check_selfdrive_timeout_alert(self):
    sm = SubMaster(['selfdriveState'])
    pm = PubMaster(['selfdriveState'])

    for _ in range(100):
      cs = messaging.new_message('selfdriveState')
      cs.selfdriveState.enabled = True

      pm.send("selfdriveState", cs)

      time.sleep(0.01)

      sm.update(0)

      assert not check_selfdrive_timeout_alert(sm)

    for _ in range(SELFDRIVE_STATE_TIMEOUT * 110):
      sm.update(0)
      time.sleep(0.01)

    assert check_selfdrive_timeout_alert(sm)

  # TODO: add test with micd for checking that soundd actually outputs sounds


def test_dead_mic_holds_full_alert_gain():
  assert MIC_DEAD_DB == 5.0
  assert alert_gain_from_weighted_db(0.0, volume_base=10) == 1.0
  assert alert_gain_from_weighted_db(60.0, volume_base=10) == pytest.approx(1.0)
  quiet_room = alert_gain_from_weighted_db(30.0, volume_base=10)
  assert quiet_room == pytest.approx(10 ** -0.9)
  assert quiet_room < 0.2
