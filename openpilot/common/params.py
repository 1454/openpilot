import builtins
import contextlib
import ctypes
import datetime
from enum import IntEnum, IntFlag
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import weakref

from openpilot.common.swaglog import cloudlog
from openpilot.common.params_paths import params_path


class ParamKeyFlag(IntFlag):
  PERSISTENT = 0x02
  CLEAR_ON_MANAGER_START = 0x04
  CLEAR_ON_ONROAD_TRANSITION = 0x08
  CLEAR_ON_OFFROAD_TRANSITION = 0x10
  DEVELOPMENT_ONLY = 0x40
  CLEAR_ON_IGNITION_ON = 0x80
  ALL = 0xFFFFFFFF


class ParamKeyType(IntEnum):
  STRING = 0
  BOOL = 1
  INT = 2
  FLOAT = 3
  TIME = 4
  JSON = 5
  BYTES = 6


def _load_header_keys() -> dict[str, dict]:
  """Keys declared in params_keys.h. Used to serve keys a stale compiled binding does not know."""
  params_keys = Path(__file__).with_name("params_keys.h")
  if not params_keys.exists():
    return {}

  keys = {}
  token = re.compile(r'R"[^(\s]*\(.*?\)"|"(?:\\.|[^"\\])*"|[^,\s][^,]*')
  for line in params_keys.read_text(encoding="utf-8", errors="ignore").splitlines():
    match = re.match(r'\s*\{"(\w+)",\s*\{(.*)\}\},?\s*$', line)
    if not match:
      continue
    fields = [t.strip() for t in token.findall(match.group(2))]
    if len(fields) < 2:
      continue
    default = None
    if len(fields) > 2:
      raw_default = fields[2]
      if raw_default.startswith('R"'):
        start = raw_default.find("(")
        end = raw_default.rfind(")")
        if start != -1 and end != -1 and end > start:
          default = raw_default[start + 1:end]
      elif raw_default.startswith('"') and raw_default.endswith('"'):
        default = raw_default[1:-1]
    keys[match.group(1)] = {
      "flags": [f.strip() for f in fields[0].split("|")],
      "type": fields[1],
      "default": default,
    }
  return keys


_HEADER_KEYS = _load_header_keys()
_EXTRA_KEY_CACHE: dict[str, bool] = {}


_suffix = ".dylib" if sys.platform == "darwin" else ".so"
lib = ctypes.CDLL(Path(__file__).with_name(f"libparams_c{_suffix}"))

ParamsHandle = ctypes.c_void_p


class ParamsBuffer(ctypes.Structure):
  _fields_ = [("data", ctypes.c_void_p), ("size", ctypes.c_size_t)]


def _bind_raw(name, args, result=None):
  function = getattr(lib, name)
  function.argtypes = args
  function.restype = result
  return function


params_last_error = _bind_raw("params_last_error", [], ctypes.c_char_p)


def _bind(name, args, result=None):
  function = _bind_raw(name, args, result)

  def checked(*call_args):
    value = function(*call_args)
    if error := params_last_error():
      raise RuntimeError(error.decode())
    return value

  return checked


params_create = _bind("params_create", [ctypes.c_char_p, ctypes.c_size_t], ParamsHandle)
params_destroy = _bind("params_destroy", [ParamsHandle])
params_clear_all = _bind("params_clear_all", [ParamsHandle, ctypes.c_uint])
params_check_key = _bind("params_check_key", [ParamsHandle, ctypes.c_char_p], ctypes.c_bool)
params_get_key_type = _bind("params_get_key_type", [ParamsHandle, ctypes.c_char_p], ctypes.c_int)
params_get_default = _bind("params_get_default", [ParamsHandle, ctypes.c_char_p], ParamsBuffer)
params_get = _bind("params_get", [ParamsHandle, ctypes.c_char_p, ctypes.c_bool], ParamsBuffer)
params_get_bool = _bind("params_get_bool", [ParamsHandle, ctypes.c_char_p, ctypes.c_bool], ctypes.c_bool)
params_put = _bind("params_put", [ParamsHandle, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_bool], ctypes.c_int)
params_put_bool = _bind("params_put_bool", [ParamsHandle, ctypes.c_char_p, ctypes.c_bool, ctypes.c_bool], ctypes.c_int)
params_remove = _bind("params_remove", [ParamsHandle, ctypes.c_char_p], ctypes.c_int)
params_get_path = _bind("params_get_path", [ParamsHandle, ctypes.c_char_p, ctypes.c_size_t], ParamsBuffer)
params_keys_size = _bind("params_keys_size", [ParamsHandle], ctypes.c_size_t)
params_key_at = _bind("params_key_at", [ParamsHandle, ctypes.c_size_t], ParamsBuffer)

PYTHON_2_CPP = {
  (str, ParamKeyType.STRING): lambda v: v,
  (builtins.bool, ParamKeyType.BOOL): lambda v: "1" if v else "0",
  (int, ParamKeyType.INT): str,
  (float, ParamKeyType.FLOAT): str,
  (datetime.datetime, ParamKeyType.TIME): lambda v: v.isoformat(),
  (dict, ParamKeyType.JSON): json.dumps,
  (list, ParamKeyType.JSON): json.dumps,
  (bytes, ParamKeyType.BYTES): lambda v: v,
}
CPP_2_PYTHON = {
  ParamKeyType.STRING: lambda v: v.decode("utf-8"),
  ParamKeyType.BOOL: lambda v: v == b"1",
  ParamKeyType.INT: int,
  ParamKeyType.FLOAT: float,
  ParamKeyType.TIME: lambda v: datetime.datetime.fromisoformat(v.decode("utf-8")),
  ParamKeyType.JSON: json.loads,
  ParamKeyType.BYTES: lambda v: v,
}


def ensure_bytes(v):
  return v.encode() if isinstance(v, str) else v


def _copy_string(value):
  if value.data is None:
    return None
  return ctypes.string_at(value.data, value.size)


class UnknownKeyName(Exception):
  pass


class Params:
  def __init__(self, d="", *, return_defaults=False):
    path = ensure_bytes(params_path(d))
    self.p = params_create(path, len(path))
    self._finalizer = weakref.finalize(self, params_destroy, self.p)
    self._finalizer.atexit = False  # daemon threads can still be using live Params handles during interpreter shutdown
    self.d = d
    self._extra_return_defaults = bool(return_defaults)

  def __reduce__(self):
    return (type(self), (self.d,))

  def _extra_name(self, key) -> str | None:
    raw = ensure_bytes(key)
    if b"\0" in raw:
      raise UnknownKeyName(raw)
    name = raw.decode("utf-8", errors="ignore")
    cached = _EXTRA_KEY_CACHE.get(name)
    if cached is None:
      if params_check_key(self.p, raw):
        cached = False
      else:
        cached = name in _HEADER_KEYS
      _EXTRA_KEY_CACHE[name] = cached
    return name if cached else None

  def _extra_type(self, name: str) -> ParamKeyType:
    return getattr(ParamKeyType, _HEADER_KEYS[name]["type"], ParamKeyType.STRING)

  def _extra_default_bytes(self, name: str) -> bytes | None:
    default = _HEADER_KEYS[name]["default"]
    return default.encode("utf-8") if default is not None else None

  def _extra_path(self, name: str) -> str:
    return self.get_param_path(name)

  def _extra_read(self, name: str, *, block: bool = False) -> bytes:
    path = self._extra_path(name)
    while True:
      try:
        with open(path, "rb") as f:
          data = f.read()
      except OSError:
        data = b""
      if data != b"" or not block:
        return data
      time.sleep(0.1)

  @contextlib.contextmanager
  def _extra_lock(self):
    lock_path = os.path.join(os.path.dirname(self.get_param_path()), ".lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o666)
    try:
      fcntl.flock(fd, fcntl.LOCK_EX)
      yield
    finally:
      try:
        fcntl.flock(fd, fcntl.LOCK_UN)
      finally:
        os.close(fd)

  def _extra_write(self, name: str, data: bytes, *, locked: bool = False) -> None:
    path = self._extra_path(name)
    directory = os.path.dirname(path)
    with contextlib.nullcontext() if locked else self._extra_lock():
      fd, tmp = tempfile.mkstemp(dir=directory, prefix=f".{name}.")
      try:
        with os.fdopen(fd, "wb") as f:
          f.write(data)
          f.flush()
          os.fsync(f.fileno())
        os.chmod(tmp, 0o666)
        os.replace(tmp, path)
      except BaseException:
        with contextlib.suppress(OSError):
          os.remove(tmp)
        raise
      dir_fd = os.open(directory, os.O_RDONLY)
      try:
        os.fsync(dir_fd)
      finally:
        os.close(dir_fd)

  def _extra_names(self) -> list[str]:
    known = {
      _copy_string(params_key_at(self.p, i)).decode("utf-8", errors="ignore")
      for i in range(params_keys_size(self.p))
    }
    return [name for name in _HEADER_KEYS if name not in known]

  def clear_all(self, tx_flag=ParamKeyFlag.ALL):
    # The C++ clearAll deletes every file whose key it does not know, which
    # would wipe the fallback files. Keep the ones that are not being cleared.
    keep = {}
    for name in self._extra_names():
      flag = 0
      for flag_name in _HEADER_KEYS[name]["flags"]:
        flag |= int(getattr(ParamKeyFlag, flag_name, 0))
      if flag & int(tx_flag):
        continue
      raw = self._extra_read(name)
      if raw:
        keep[name] = raw
    params_clear_all(self.p, int(tx_flag))
    if keep:
      with self._extra_lock():
        for name, raw in keep.items():
          self._extra_write(name, raw, locked=True)

  def check_key(self, key):
    key = ensure_bytes(key)
    if b"\0" in key:
      raise UnknownKeyName(key)
    if params_check_key(self.p, key):
      return key
    name = key.decode("utf-8", errors="ignore")
    if name in _HEADER_KEYS:
      _EXTRA_KEY_CACHE[name] = True
      return key
    raise UnknownKeyName(key)

  def python2cpp(self, proposed_type, expected_type, value, key):
    cast = PYTHON_2_CPP.get((proposed_type, expected_type))
    if cast:
      return cast(value)
    raise TypeError(f"Type mismatch while writing param {key}: {proposed_type=} {expected_type=} {value=}")

  def _cpp2python(self, t, value, default, key):
    if value is None:
      return None
    try:
      return CPP_2_PYTHON[t](value)
    except (KeyError, TypeError, ValueError):
      cloudlog.warning(f"Failed to cast param {key} with {value=} from type {t=}")
      return self._cpp2python(t, default, None, key)

  def _default(self, key):
    name = self._extra_name(key)
    if name is not None:
      return self._extra_default_bytes(name)
    return _copy_string(params_get_default(self.p, key))

  def get(self, key, block=False, return_default=False):
    name = self._extra_name(key)
    if name is not None:
      t = self._extra_type(name)
      use_default = return_default or (getattr(self, "_extra_return_defaults", False) and not block)
      default = self._extra_default_bytes(name) if use_default else None
      value = self._extra_read(name, block=block)
      if value == b"":
        if block:
          raise KeyboardInterrupt
        return self._cpp2python(t, default, None, key)
      return self._cpp2python(t, value, default, key)
    k = self.check_key(key)
    t = self.get_type(k)
    use_default = return_default or (getattr(self, "_extra_return_defaults", False) and not block)
    default = self._default(k) if use_default else None
    value = _copy_string(params_get(self.p, k, block))
    if value == b"":
      if block:
        raise KeyboardInterrupt
      return self._cpp2python(t, default, None, key)
    return self._cpp2python(t, value, default, key)

  def get_bool(self, key, block=False):
    name = self._extra_name(key)
    if name is not None:
      return bool(self.get(key, block=block, return_default=True))
    return bool(params_get_bool(self.p, self.check_key(key), block))

  def get_float(self, key, block=False, return_default=False, default=0.0):
    val = self.get(key, block=block, return_default=return_default)
    if val is None:
      return float(default)
    try:
      return float(val)
    except (TypeError, ValueError):
      return float(default)

  def _put_cast(self, key, dat):
    return ensure_bytes(self.python2cpp(type(dat), self.get_type(key), dat, key))

  def put(self, key, dat, block=False):
    """Write a parameter. block=True waits until it is persisted to disk."""
    name = self._extra_name(key)
    if name is not None:
      value = ensure_bytes(self.python2cpp(type(dat), self._extra_type(name), dat, key))
      self._extra_write(name, value)
      return
    k = self.check_key(key)
    value = self._put_cast(k, dat)
    params_put(self.p, k, value, len(value), block)

  def put_bool(self, key, val, block=False):
    name = self._extra_name(key)
    if name is not None:
      self._extra_write(name, b"1" if val else b"0")
      return
    params_put_bool(self.p, self.check_key(key), val, block)

  def put_float(self, key, val, block=False):
    self.put(key, float(val), block=block)

  def remove(self, key):
    name = self._extra_name(key)
    if name is not None:
      with self._extra_lock():
        with contextlib.suppress(OSError):
          os.remove(self._extra_path(name))
      return
    params_remove(self.p, self.check_key(key))

  def get_param_path(self, key=""):
    key = ensure_bytes(key)
    return _copy_string(params_get_path(self.p, key, len(key))).decode()

  def get_type(self, key):
    name = self._extra_name(key)
    if name is not None:
      return self._extra_type(name)
    return ParamKeyType(params_get_key_type(self.p, self.check_key(key)))

  def get_key_flag(self, key):
    name = self.check_key(key).decode("utf-8", errors="ignore")
    flag = 0
    for flag_name in _HEADER_KEYS.get(name, {}).get("flags", ()):
      flag |= int(getattr(ParamKeyFlag, flag_name, 0))
    return flag

  def all_keys(self):
    keys = []
    for i in range(params_keys_size(self.p)):
      keys.append(_copy_string(params_key_at(self.p, i)))
    known = {k.decode("utf-8", errors="ignore") for k in keys}
    keys.extend(name.encode("utf-8") for name in _HEADER_KEYS if name not in known)
    return keys

  def get_default_value(self, key):
    name = self._extra_name(key)
    if name is not None:
      default = self._extra_default_bytes(name)
      return self._cpp2python(self._extra_type(name), default, None, key)
    k = self.check_key(key)
    return self._cpp2python(self.get_type(k), self._default(k), None, key)

  def cpp2python(self, key, value):
    return self._cpp2python(self.get_type(key), value, None, key)


if __name__ == "__main__":
  import sys

  params = Params()
  key = sys.argv[1]
  params.check_key(key)
  if len(sys.argv) == 3:
    val = sys.argv[2]
    print(f"SET: {key} = {val}")
    params.put(key, val, block=True)
  elif len(sys.argv) == 2:
    print(f"GET: {key} = {params.get(key)}")
