import builtins
import importlib
import os
import sys
import tempfile
import types
from enum import IntEnum, IntFlag


class _Flag(IntFlag):
  PERSISTENT = 2
  CLEAR_ON_MANAGER_START = 4
  ALL = 0xFFFFFFFF
  DONT_LOG = 32


class _Type(IntEnum):
  STRING = 0
  BOOL = 1
  INT = 2
  FLOAT = 3


class _Unknown(Exception):
  pass


def _make_fake_binding(directory: str):
  known = {"LaneCentering"}
  to_cpp = {
    (str, _Type.STRING): lambda v: v,
    (builtins.bool, _Type.BOOL): lambda v: "1" if v else "0",
    (int, _Type.INT): str,
    (float, _Type.FLOAT): str,
  }
  from_cpp = {
    _Type.STRING: lambda v: v.decode(),
    _Type.BOOL: lambda v: v == b"1",
    _Type.INT: lambda v: int(v),
    _Type.FLOAT: float,
  }

  class FakeParams:
    def __init__(self, d="", *, memory=False, return_defaults=False):
      self.return_defaults = return_defaults or memory

    def check_key(self, key):
      key = key.encode() if isinstance(key, str) else key
      if key.decode() not in known:
        raise _Unknown(key)
      return key

    def get_param_path(self, key=""):
      return os.path.join(directory, key) if key else directory

    def python2cpp(self, proposed, expected, value, key):
      return to_cpp[(proposed, expected)](value)

    def _cpp2python(self, t, value, default, key):
      return None if value is None else from_cpp[t](value)

    def get(self, key, block=False, return_default=False):
      self.check_key(key)
      return None

    def all_keys(self):
      return [k.encode() for k in known]

  module = types.ModuleType("openpilot.common.params_pyx")
  module.Params = FakeParams
  module.ParamKeyFlag = _Flag
  module.ParamKeyType = _Type
  module.UnknownKeyName = _Unknown
  return module


def _load_params_module(directory: str):
  sys.modules["openpilot.common.params_pyx"] = _make_fake_binding(directory)
  sys.modules.pop("openpilot.common.params", None)
  return importlib.import_module("openpilot.common.params")


def test_keys_the_binding_lacks_are_stored_as_files():
  with tempfile.TemporaryDirectory() as directory:
    params_module = _load_params_module(directory)
    try:
      params = params_module.Params(return_defaults=True)

      assert params.get_bool("EndDriverMonitoring") is True
      assert params.get_float("GasGradeGain") == 100.0
      assert params.get_bool("CommaUploadsNerfed") is False

      params.put_bool("EndDriverMonitoring", False)
      params.put_float("GasTireRadius", 0.432)
      params.put_bool("CommaUploadsNerfed", True)

      assert params.get_bool("EndDriverMonitoring") is False
      assert abs(params.get_float("GasTireRadius") - 0.432) < 1e-9
      assert params.get_bool("CommaUploadsNerfed") is True
      assert open(os.path.join(directory, "EndDriverMonitoring"), "rb").read() == b"0"

      params.remove("EndDriverMonitoring")
      assert params.get_bool("EndDriverMonitoring") is True
      assert b"GasTireRadius" in params.all_keys()
      assert params.get_key_flag("GasTireRadius") & _Flag.PERSISTENT
    finally:
      sys.modules.pop("openpilot.common.params_pyx", None)
      sys.modules.pop("openpilot.common.params", None)


def test_unknown_names_still_raise_or_return_defaults():
  with tempfile.TemporaryDirectory() as directory:
    params_module = _load_params_module(directory)
    try:
      params = params_module.Params()
      assert params.get("NotARealKey", default="x") == "x"
      assert params.get_bool("NotARealKey") is False
    finally:
      sys.modules.pop("openpilot.common.params_pyx", None)
      sys.modules.pop("openpilot.common.params", None)


if __name__ == "__main__":
  test_keys_the_binding_lacks_are_stored_as_files()
  test_unknown_names_still_raise_or_return_defaults()
  print("params missing key tests passed")
