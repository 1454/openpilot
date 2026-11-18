import ctypes
import importlib
import os
import sys
import tempfile
import types


def _make_fake_c_lib(directory: str):
  known = {
    b"LaneCentering": (1, b"0"),
  }

  class _FakeFunction:
    def __init__(self, fn):
      self._fn = fn
      self.argtypes = None
      self.restype = None

    def __call__(self, *args):
      return self._fn(*args)

  def _buf(data: bytes | None):
    from openpilot.common.params import ParamsBuffer
    if data is None:
      return ParamsBuffer(None, 0)
    raw = ctypes.create_string_buffer(data, len(data))
    _FakeLib._keep.append(raw)
    return ParamsBuffer(ctypes.cast(raw, ctypes.c_void_p).value, len(data))

  class _FakeLib:
    _keep: list = []

    params_last_error = _FakeFunction(lambda: None)
    params_create = _FakeFunction(lambda path, size: 1)
    params_destroy = _FakeFunction(lambda h: None)
    params_check_key = _FakeFunction(lambda h, k: k in known)
    params_get_key_type = _FakeFunction(lambda h, k: known[k][0])
    params_get_default = _FakeFunction(lambda h, k: _buf(known[k][1]))
    params_get = _FakeFunction(lambda h, k, block: _buf(b""))
    params_get_bool = _FakeFunction(lambda h, k, block: False)
    params_put = _FakeFunction(lambda h, k, v, sz, block: 0)
    params_put_bool = _FakeFunction(lambda h, k, v, block: 0)
    params_remove = _FakeFunction(lambda h, k: 0)
    params_get_path = _FakeFunction(
      lambda h, k, sz: _buf((os.path.join(directory, k.decode()) if k else directory).encode())
    )
    params_keys_size = _FakeFunction(lambda h: len(known))
    params_key_at = _FakeFunction(lambda h, i: _buf(list(known)[i]))

    @staticmethod
    def _clear_all(h, tx_flag):
      for name in os.listdir(directory):
        if name == ".lock":
          continue
        if name.encode() not in known:
          os.remove(os.path.join(directory, name))

  _FakeLib.params_clear_all = _FakeFunction(_FakeLib._clear_all)
  return _FakeLib()


def _load_params_module(directory: str):
  swaglog = types.ModuleType("openpilot.common.swaglog")
  swaglog.cloudlog = types.SimpleNamespace(warning=lambda *a, **k: None)
  sys.modules["openpilot.common.swaglog"] = swaglog

  original_cdll = ctypes.CDLL
  ctypes.CDLL = lambda *a, **k: _make_fake_c_lib(directory)
  try:
    sys.modules.pop("openpilot.common.params", None)
    return importlib.import_module("openpilot.common.params")
  finally:
    ctypes.CDLL = original_cdll


def test_keys_the_binding_lacks_are_stored_as_files():
  with tempfile.TemporaryDirectory() as directory:
    params_module = _load_params_module(directory)
    try:
      params = params_module.Params(return_defaults=True)

      assert params.get_bool("EndDriverMonitoring") is True
      assert params.get_float("GasGradeGain", return_default=True) == 100.0
      assert params.get("GasGradeUphillCap") == 0.2
      assert params.get_bool("LeadDistanceVelocity") is False

      params.put_bool("EndDriverMonitoring", False)
      params.put_float("GasTireRadius", 0.432)
      params.put_bool("LeadDistanceVelocity", True)

      assert params.get_bool("EndDriverMonitoring") is False
      assert abs(params.get_float("GasTireRadius") - 0.432) < 1e-9
      assert params.get_bool("LeadDistanceVelocity") is True
      assert open(os.path.join(directory, "EndDriverMonitoring"), "rb").read() == b"0"

      params.remove("EndDriverMonitoring")
      assert params.get_bool("EndDriverMonitoring") is True
      assert b"GasTireRadius" in params.all_keys()
      assert params.get_key_flag("GasTireRadius") & params_module.ParamKeyFlag.PERSISTENT
    finally:
      sys.modules.pop("openpilot.common.params", None)


def test_clear_all_keeps_fallback_files():
  with tempfile.TemporaryDirectory() as directory:
    params_module = _load_params_module(directory)
    try:
      params = params_module.Params()
      params.put_bool("EndDriverMonitoring", False)
      params.put_float("GasTireRadius", 0.432)
      stray = os.path.join(directory, "NotAKey")
      open(stray, "wb").write(b"x")

      params.clear_all(params_module.ParamKeyFlag.CLEAR_ON_MANAGER_START)

      assert params.get_bool("EndDriverMonitoring") is False
      assert abs(params.get_float("GasTireRadius") - 0.432) < 1e-9
      assert not os.path.exists(stray)
      assert [n for n in os.listdir(directory) if n.startswith(".") and n != ".lock"] == []
    finally:
      sys.modules.pop("openpilot.common.params", None)


def test_unknown_names_still_raise():
  with tempfile.TemporaryDirectory() as directory:
    params_module = _load_params_module(directory)
    try:
      params = params_module.Params()
      try:
        params.get("NotARealKey")
      except params_module.UnknownKeyName:
        pass
      else:
        raise AssertionError("Expected UnknownKeyName")
    finally:
      sys.modules.pop("openpilot.common.params", None)


if __name__ == "__main__":
  test_keys_the_binding_lacks_are_stored_as_files()
  test_clear_all_keeps_fallback_files()
  test_unknown_names_still_raise()
  print("params missing key tests passed")
