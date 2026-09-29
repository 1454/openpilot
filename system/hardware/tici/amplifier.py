#!/usr/bin/env python3
import time
from smbus2 import SMBus
from collections import namedtuple

# https://datasheets.maximintegrated.com/en/ds/MAX98089.pdf

AmpConfig = namedtuple('AmpConfig', ['name', 'value', 'register', 'offset', 'mask'])
EQParams = namedtuple('EQParams', ['K', 'k1', 'k2', 'c1', 'c2'])

def configs_from_eq_params(base, eq_params):
  return [
    AmpConfig("K (high)", (eq_params.K >> 8), base, 0, 0xFF),
    AmpConfig("K (low)", (eq_params.K & 0xFF), base + 1, 0, 0xFF),
    AmpConfig("k1 (high)", (eq_params.k1 >> 8), base + 2, 0, 0xFF),
    AmpConfig("k1 (low)", (eq_params.k1 & 0xFF), base + 3, 0, 0xFF),
    AmpConfig("k2 (high)", (eq_params.k2 >> 8), base + 4, 0, 0xFF),
    AmpConfig("k2 (low)", (eq_params.k2 & 0xFF), base + 5, 0, 0xFF),
    AmpConfig("c1 (high)", (eq_params.c1 >> 8), base + 6, 0, 0xFF),
    AmpConfig("c1 (low)", (eq_params.c1 & 0xFF), base + 7, 0, 0xFF),
    AmpConfig("c2 (high)", (eq_params.c2 >> 8), base + 8, 0, 0xFF),
    AmpConfig("c2 (low)", (eq_params.c2 & 0xFF), base + 9, 0, 0xFF),
  ]

BASE_CONFIG = [
  AmpConfig("MCLK prescaler", 0b01, 0x10, 4, 0b00110000),
  AmpConfig("PM: enable speakers", 0b11, 0x4D, 4, 0b00110000),
  AmpConfig("PM: enable DACs", 0b11, 0x4D, 0, 0b00000011),
  AmpConfig("Enable PLL1", 0b1, 0x12, 7, 0b10000000),
  AmpConfig("Enable PLL2", 0b1, 0x1A, 7, 0b10000000),
  AmpConfig("DAI1: I2S mode", 0b00100, 0x14, 2, 0b01111100),
  AmpConfig("DAI2: I2S mode", 0b00100, 0x1C, 2, 0b01111100),
  AmpConfig("DAI1 Passband filtering: music mode", 0b1, 0x18, 7, 0b10000000),
  AmpConfig("DAI1 voice mode gain (DV1G)", 0b00, 0x2F, 4, 0b00110000),
  AmpConfig("DAI1 attenuation (DV1)", 0x0, 0x2F, 0, 0b00001111),
  AmpConfig("DAI2 attenuation (DV2)", 0x0, 0x31, 0, 0b00001111),
  AmpConfig("DAI2: DC blocking", 0b1, 0x20, 0, 0b00000001),
  AmpConfig("DAI2: High sample rate", 0b0, 0x20, 3, 0b00001000),
  AmpConfig("ALC enable", 0b1, 0x43, 7, 0b10000000),
  AmpConfig("ALC/excursion limiter release time", 0b101, 0x43, 4, 0b01110000),
  AmpConfig("ALC multiband enable", 0b1, 0x43, 3, 0b00001000),
  AmpConfig("DAI1 EQ enable", 0b0, 0x49, 0, 0b00000001),
  AmpConfig("DAI2 EQ clip detection disabled", 0b1, 0x32, 4, 0b00010000),
  AmpConfig("DAI2 EQ attenuation", 0x5, 0x32, 0, 0b00001111),
  AmpConfig("Excursion limiter upper corner freq", 0b100, 0x41, 4, 0b01110000),
  AmpConfig("Excursion limiter lower corner freq", 0b00, 0x41, 0, 0b00000011),
  AmpConfig("Excursion limiter threshold", 0b000, 0x42, 0, 0b00001111),
  AmpConfig("Distortion limit (THDCLP)", 0x6, 0x46, 4, 0b11110000),
  AmpConfig("Distortion limiter release time constant", 0b0, 0x46, 0, 0b00000001),
  AmpConfig("Right DAC input mixer: DAI1 left", 0b0, 0x22, 3, 0b00001000),
  AmpConfig("Right DAC input mixer: DAI1 right", 0b0, 0x22, 2, 0b00000100),
  AmpConfig("Right DAC input mixer: DAI2 left", 0b1, 0x22, 1, 0b00000010),
  AmpConfig("Right DAC input mixer: DAI2 right", 0b0, 0x22, 0, 0b00000001),
  AmpConfig("DAI1 audio port selector", 0b10, 0x16, 6, 0b11000000),
  AmpConfig("DAI2 audio port selector", 0b01, 0x1E, 6, 0b11000000),
  AmpConfig("Enable left digital microphone", 0b1, 0x48, 5, 0b00100000),
  AmpConfig("Enable right digital microphone", 0b1, 0x48, 4, 0b00010000),
  AmpConfig("Enhanced volume smoothing disabled", 0b0, 0x49, 7, 0b10000000),
  AmpConfig("Volume adjustment smoothing disabled", 0b0, 0x49, 6, 0b01000000),
  AmpConfig("Zero-crossing detection disabled", 0b0, 0x49, 5, 0b00100000),
]

CONFIGS = {
  "tici": [
    AmpConfig("Right speaker output from right DAC", 0b1, 0x2C, 0, 0b11111111),
    AmpConfig("Right Speaker Mixer Gain", 0b00, 0x2D, 2, 0b00001100),
    AmpConfig("Right speaker output volume", 0x1c, 0x3E, 0, 0b00011111),
    AmpConfig("DAI2 EQ enable", 0b1, 0x49, 1, 0b00000010),

    *configs_from_eq_params(0x84, EQParams(0x274F, 0xC0FF, 0x3BF9, 0x0B3C, 0x1656)),
    *configs_from_eq_params(0x8E, EQParams(0x1009, 0xC6BF, 0x2952, 0x1C97, 0x30DF)),
    *configs_from_eq_params(0x98, EQParams(0x0F75, 0xCBE5, 0x0ED2, 0x2528, 0x3E42)),
    *configs_from_eq_params(0xA2, EQParams(0x091F, 0x3D4C, 0xCE11, 0x1266, 0x2807)),
    *configs_from_eq_params(0xAC, EQParams(0x0A9E, 0x3F20, 0xE573, 0x0A8B, 0x3A3B)),
  ],
  "tizi": [
    AmpConfig("Left speaker output from left DAC", 0b1, 0x2B, 0, 0b11111111),
    AmpConfig("Right speaker output from right DAC", 0b1, 0x2C, 0, 0b11111111),
    AmpConfig("Left Speaker Mixer Gain", 0b00, 0x2D, 0, 0b00000011),
    AmpConfig("Right Speaker Mixer Gain", 0b00, 0x2D, 2, 0b00001100),
    AmpConfig("Left speaker output volume", 0x17, 0x3D, 0, 0b00011111),
    AmpConfig("Right speaker output volume", 0x17, 0x3E, 0, 0b00011111),

    AmpConfig("DAI2 EQ enable", 0b0, 0x49, 1, 0b00000010),
    AmpConfig("DAI2: DC blocking", 0b0, 0x20, 0, 0b00000001),
    AmpConfig("ALC enable", 0b0, 0x43, 7, 0b10000000),
    AmpConfig("DAI2 EQ attenuation", 0x2, 0x32, 0, 0b00001111),
    AmpConfig("Excursion limiter upper corner freq", 0b001, 0x41, 4, 0b01110000),
    AmpConfig("Excursion limiter threshold", 0b100, 0x42, 0, 0b00001111),
    AmpConfig("Distortion limit (THDCLP)", 0x0, 0x46, 4, 0b11110000),
    AmpConfig("Distortion limiter release time constant", 0b1, 0x46, 0, 0b00000001),
    AmpConfig("Left DAC input mixer: DAI1 left", 0b0, 0x22, 7, 0b10000000),
    AmpConfig("Left DAC input mixer: DAI1 right", 0b0, 0x22, 6, 0b01000000),
    AmpConfig("Left DAC input mixer: DAI2 left", 0b1, 0x22, 5, 0b00100000),
    AmpConfig("Left DAC input mixer: DAI2 right", 0b0, 0x22, 4, 0b00010000),
    AmpConfig("Right DAC input mixer: DAI2 left", 0b0, 0x22, 1, 0b00000010),
    AmpConfig("Right DAC input mixer: DAI2 right", 0b1, 0x22, 0, 0b00000001),
    AmpConfig("Volume adjustment smoothing disabled", 0b1, 0x49, 6, 0b01000000),
  ],
}

SHUTDOWN_REGISTER = 0x51

# Registers on the DAI2 to speaker playback path: MCLK, PLL2, DAI2 format and port,
# DAC input mixer, speaker mixers and gain, speaker volume, power management, shutdown.
SPEAKER_PATH_REGISTERS = (0x10, 0x1A, 0x1C, 0x1E, 0x22, 0x2B, 0x2C, 0x2D, 0x3D, 0x3E, 0x4D, SHUTDOWN_REGISTER)


def expected_register_bits(configs: list[AmpConfig]) -> dict[int, tuple[int, int]]:
  """Fold configs in write order into {register: (mask, value)}. Later writes win on overlapping bits."""
  expected: dict[int, tuple[int, int]] = {}
  for config in configs:
    mask = config.mask & 0xFF
    value = (config.value << config.offset) & mask
    old_mask, old_value = expected.get(config.register, (0, 0))
    expected[config.register] = (old_mask | mask, (old_value & ~mask) | value)
  return expected


def speaker_path_expected(model: str) -> dict[int, tuple[int, int]]:
  cfgs = [*BASE_CONFIG, *CONFIGS[model], AmpConfig("Global shutdown", 0b1, SHUTDOWN_REGISTER, 7, 0b10000000)]
  expected = expected_register_bits(cfgs)
  return {reg: expected[reg] for reg in SPEAKER_PATH_REGISTERS if reg in expected}


def speaker_path_mismatches(model: str, registers: dict[int, int]) -> list[tuple[int, int, int, int | None]]:
  """(register, mask, expected, actual) for every speaker path register that differs from the config."""
  mismatches = []
  for reg, (mask, value) in speaker_path_expected(model).items():
    actual = registers.get(reg)
    if actual is None or (actual & mask) != value:
      mismatches.append((reg, mask, value, actual))
  return mismatches


def speaker_outputs_enabled(model: str, registers: dict[int, int]) -> bool:
  return not speaker_path_mismatches(model, registers)


def speaker_amp_action(model: str, registers: dict[int, int] | None, onroad: bool) -> str:
  """What soundd may do about the amp. Never shuts the amp down on a failed read."""
  if registers is None:
    return "unreadable"
  mismatches = speaker_path_mismatches(model, registers)
  if not mismatches:
    return "on"
  in_shutdown = any(reg == SHUTDOWN_REGISTER for reg, *_ in mismatches)
  if in_shutdown and not onroad:
    # hardwared power save shuts the amp down offroad and reinitializes it when leaving power save.
    return "powersave"
  if in_shutdown and len(mismatches) == 1:
    return "clear_shutdown"
  return "initialize"


def format_registers(registers: dict[int, int] | None) -> str:
  if registers is None:
    return "unreadable"
  return " ".join(f"{reg:#04x}={value:#04x}" for reg, value in sorted(registers.items()))


def format_mismatches(mismatches) -> str:
  return " ".join(
    f"{reg:#04x}&{mask:#04x}:want={value:#04x},got={'none' if actual is None else f'{actual & mask:#04x}'}"
    for reg, mask, value, actual in mismatches
  ) or "none"


class Amplifier:
  AMP_I2C_BUS = 0
  AMP_ADDRESS = 0x10

  def __init__(self, debug=False):
    self.debug = debug

  def _get_shutdown_config(self, amp_disabled: bool) -> AmpConfig:
    return AmpConfig("Global shutdown", 0b0 if amp_disabled else 0b1, 0x51, 7, 0b10000000)

  def _set_configs(self, configs: list[AmpConfig]) -> None:
    with SMBus(self.AMP_I2C_BUS) as bus:
      for config in configs:
        if self.debug:
          print(f"Setting \"{config.name}\" to {config.value}:")

        old_value = bus.read_byte_data(self.AMP_ADDRESS, config.register, force=True)
        new_value = (old_value & (~config.mask)) | ((config.value << config.offset) & config.mask)
        bus.write_byte_data(self.AMP_ADDRESS, config.register, new_value, force=True)

        if self.debug:
          print(f"  Changed {hex(config.register)}: {hex(old_value)} -> {hex(new_value)}")

  def set_configs(self, configs: list[AmpConfig], tries: int = 15) -> bool:
    # retry in case panda is using the amp
    backoff = 0.
    for i in range(tries):
      try:
        self._set_configs(configs)
        return True
      except OSError:
        backoff += 0.1
        time.sleep(backoff)
        print(f"Failed to set amp config, {tries - i - 1} retries left")
    return False

  def set_global_shutdown(self, amp_disabled: bool, tries: int = 15) -> bool:
    return self.set_configs([self._get_shutdown_config(amp_disabled), ], tries=tries)

  def read_registers(self, registers, tries: int = 3, delay: float = 0.02) -> dict[int, int] | None:
    for _ in range(tries):
      try:
        with SMBus(self.AMP_I2C_BUS) as bus:
          return {reg: bus.read_byte_data(self.AMP_ADDRESS, reg, force=True) for reg in registers}
      except OSError:
        time.sleep(delay)
    return None

  def read_speaker_registers(self) -> dict[int, int] | None:
    return self.read_registers(SPEAKER_PATH_REGISTERS)

  def ensure_speakers_enabled(self, model: str, onroad: bool, tries: int = 2) -> dict:
    """Check the speaker path and repair it only when the registers were read and are wrong."""
    before = self.read_speaker_registers()
    action = speaker_amp_action(model, before, onroad)
    result = {"action": action, "before": before, "after": None, "write_ok": None,
              "mismatches": speaker_path_mismatches(model, before) if before is not None else None}
    if action == "clear_shutdown":
      result["write_ok"] = self.set_global_shutdown(False, tries=tries)
    elif action == "initialize":
      result["write_ok"] = self.initialize_configuration(model, tries=tries)
      if not result["write_ok"]:
        # initialize_configuration sets shutdown first. Do not leave the amp shut down after a partial write.
        self.set_global_shutdown(False, tries=tries)
    else:
      return result
    after = self.read_speaker_registers()
    result["after"] = after
    result["mismatches_after"] = speaker_path_mismatches(model, after) if after is not None else None
    return result

  def initialize_configuration(self, model: str, tries: int = 15) -> bool:
    cfgs = [
      self._get_shutdown_config(True),
      *BASE_CONFIG,
      *CONFIGS[model],
      self._get_shutdown_config(False),
    ]
    return self.set_configs(cfgs, tries=tries)


if __name__ == "__main__":
  with open("/sys/firmware/devicetree/base/model") as f:
    model = f.read().strip('\x00')
  model = model.split('comma ')[-1]

  amp = Amplifier()
  amp.initialize_configuration(model)
