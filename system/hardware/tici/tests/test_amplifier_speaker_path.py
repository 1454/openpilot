from openpilot.system.hardware.tici.amplifier import (
  Amplifier,
  format_mismatches,
  speaker_amp_action,
  speaker_outputs_enabled,
  speaker_path_expected,
)

TIZI_ON = {
  0x10: 0x10, 0x1A: 0x80, 0x1C: 0x10, 0x1E: 0x40, 0x22: 0x21, 0x2B: 0x01, 0x2C: 0x01,
  0x2D: 0x00, 0x3D: 0x17, 0x3E: 0x17, 0x4D: 0x33, 0x51: 0x80,
}


def test_tizi_expected_bits_match_what_the_config_writes():
  expected = speaker_path_expected("tizi")
  assert expected[0x2B] == (0xFF, 0x01)
  assert expected[0x2C] == (0xFF, 0x01)
  assert expected[0x22] == (0xFF, 0x21)
  assert expected[0x4D] == (0x33, 0x33)
  assert expected[0x1E] == (0xC0, 0x40)
  assert expected[0x3D] == (0x1F, 0x17)
  assert expected[0x3E] == (0x1F, 0x17)
  assert expected[0x51] == (0x80, 0x80)


def test_tici_only_checks_registers_its_config_writes():
  expected = speaker_path_expected("tici")
  assert 0x2B not in expected
  assert 0x3D not in expected
  assert expected[0x2C] == (0xFF, 0x01)


def test_programmed_tizi_amp_is_on():
  assert speaker_outputs_enabled("tizi", TIZI_ON)
  assert speaker_amp_action("tizi", TIZI_ON, onroad=True) == "on"


def test_nonzero_but_wrong_speaker_mixer_is_not_on():
  assert not speaker_outputs_enabled("tizi", {**TIZI_ON, 0x2B: 0x02})
  assert not speaker_outputs_enabled("tizi", {**TIZI_ON, 0x2C: 0x81})


def test_dac_path_port_and_volume_are_required():
  assert not speaker_outputs_enabled("tizi", {**TIZI_ON, 0x4D: 0x30})
  assert not speaker_outputs_enabled("tizi", {**TIZI_ON, 0x22: 0x00})
  assert not speaker_outputs_enabled("tizi", {**TIZI_ON, 0x1E: 0x00})
  assert not speaker_outputs_enabled("tizi", {**TIZI_ON, 0x3D: 0x00})


def test_unrelated_bits_do_not_count():
  assert speaker_outputs_enabled("tizi", {**TIZI_ON, 0x3D: 0x97, 0x4D: 0xB3, 0x51: 0x81})


def test_failed_read_never_triggers_a_write():
  assert speaker_amp_action("tizi", None, onroad=True) == "unreadable"


def test_shutdown_only_is_cleared_onroad_and_left_to_powersave_offroad():
  off = {**TIZI_ON, 0x51: 0x00}
  assert speaker_amp_action("tizi", off, onroad=True) == "clear_shutdown"
  assert speaker_amp_action("tizi", off, onroad=False) == "powersave"
  assert speaker_amp_action("tizi", {**off, 0x2B: 0x00}, onroad=False) == "powersave"


def test_wrong_config_while_operating_is_reinitialized():
  assert speaker_amp_action("tizi", {**TIZI_ON, 0x3D: 0x00}, onroad=False) == "initialize"
  assert speaker_amp_action("tizi", {**TIZI_ON, 0x51: 0x00, 0x2B: 0x00}, onroad=True) == "initialize"


def test_format_mismatches():
  assert format_mismatches([]) == "none"
  assert format_mismatches([(0x2B, 0xFF, 0x01, 0x00)]) == "0x2b&0xff:want=0x01,got=0x00"


class FakeAmp(Amplifier):
  def __init__(self, reads, write_ok=True):
    super().__init__()
    self.reads = list(reads)
    self.write_ok = write_ok
    self.writes = []

  def read_speaker_registers(self):
    return self.reads.pop(0)

  def set_configs(self, configs, tries=15):
    self.writes.append(([c.name for c in configs], tries))
    return self.write_ok


def test_unreadable_amp_is_left_alone():
  amp = FakeAmp([None])
  result = amp.ensure_speakers_enabled("tizi", onroad=True)
  assert result["action"] == "unreadable"
  assert amp.writes == []


def test_working_amp_is_not_written():
  amp = FakeAmp([TIZI_ON])
  assert amp.ensure_speakers_enabled("tizi", onroad=True)["action"] == "on"
  assert amp.writes == []


def test_reinit_uses_short_retry_and_reads_after_state():
  amp = FakeAmp([{**TIZI_ON, 0x3D: 0x00}, TIZI_ON])
  result = amp.ensure_speakers_enabled("tizi", onroad=True)
  assert result["action"] == "initialize"
  assert result["after"] == TIZI_ON
  assert result["mismatches_after"] == []
  assert len(amp.writes) == 1 and amp.writes[0][1] == 2


def test_failed_reinit_clears_shutdown_again():
  amp = FakeAmp([{**TIZI_ON, 0x3D: 0x00}, None], write_ok=False)
  result = amp.ensure_speakers_enabled("tizi", onroad=True)
  assert result["write_ok"] is False
  assert result["mismatches_after"] is None
  assert amp.writes[-1] == (["Global shutdown"], 2)


def test_clear_shutdown_writes_only_the_shutdown_bit():
  amp = FakeAmp([{**TIZI_ON, 0x51: 0x00}, TIZI_ON])
  result = amp.ensure_speakers_enabled("tizi", onroad=True)
  assert result["action"] == "clear_shutdown"
  assert amp.writes == [(["Global shutdown"], 2)]
