from __future__ import annotations

from enum import IntEnum, IntFlag
import contextlib
import fcntl
import os
from pathlib import Path
import re
import tempfile

SETTINGS_SIMPLE = 0
SETTINGS_ADVANCED = 1


def _load_settings_tiers() -> dict[str, int]:
  params_keys = Path(__file__).with_name("params_keys.h")
  if not params_keys.exists():
    return {}

  tiers = {}
  for line in params_keys.read_text(encoding="utf-8", errors="ignore").splitlines():
    if not line.lstrip().startswith('{"'):
      continue
    parts = line.split('"')
    if len(parts) >= 2:
      tiers[parts[1]] = SETTINGS_SIMPLE if "SETTINGS_SIMPLE" in line else SETTINGS_ADVANCED
  return tiers


_SETTINGS_TIERS = _load_settings_tiers()


def _load_header_keys() -> dict[str, dict]:
  """Keys declared in params_keys.h. Used to serve keys a stale compiled binding does not know."""
  params_keys = Path(__file__).with_name("params_keys.h")
  if not params_keys.exists():
    return {}

  keys = {}
  token = re.compile(r'"[^"]*"|[^,\s][^,]*')
  for line in params_keys.read_text(encoding="utf-8", errors="ignore").splitlines():
    match = re.match(r'\s*\{"(\w+)",\s*\{(.*)\}\},?\s*$', line)
    if not match:
      continue
    fields = [t.strip() for t in token.findall(match.group(2))]
    if len(fields) < 2:
      continue
    default = fields[2].strip('"') if len(fields) > 2 and fields[2].startswith('"') else None
    stock = fields[3].strip('"') if len(fields) > 3 and fields[3].startswith('"') else None
    try:
      tuning = int(fields[4]) if len(fields) > 4 else 0
    except ValueError:
      tuning = 0
    keys[match.group(1)] = {
      "flags": [f.strip() for f in fields[0].split("|")],
      "type": fields[1],
      "default": default,
      "stock": stock,
      "tuning": tuning,
      "tier": SETTINGS_SIMPLE if len(fields) > 5 and "SETTINGS_SIMPLE" in fields[5] else SETTINGS_ADVANCED,
    }
  return keys


_HEADER_KEYS = _load_header_keys()
_EXTRA_KEY_CACHE: dict[str, bool] = {}

try:
  from openpilot.common.params_pyx import Params as _Params, ParamKeyFlag, ParamKeyType, UnknownKeyName
except Exception:
  class UnknownKeyName(KeyError):
    pass

  class ParamKeyFlag(IntFlag):
    PERSISTENT = 0x02
    CLEAR_ON_MANAGER_START = 0x04
    CLEAR_ON_ONROAD_TRANSITION = 0x08
    CLEAR_ON_OFFROAD_TRANSITION = 0x10
    DONT_LOG = 0x20
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

  def _load_key_types() -> dict[str, ParamKeyType]:
    key_types: dict[str, ParamKeyType] = {}
    params_keys = Path(__file__).with_name("params_keys.h")
    if not params_keys.exists():
      return key_types

    for line in params_keys.read_text(encoding="utf-8", errors="ignore").splitlines():
      if not line.lstrip().startswith('{"'):
        continue

      parts = line.split('"')
      if len(parts) < 2:
        continue

      key = parts[1]
      for type_name in ParamKeyType.__members__:
        if f", {type_name}" in line:
          key_types[key] = ParamKeyType[type_name]
          break
      else:
        key_types[key] = ParamKeyType.STRING

    return key_types

  _KEY_TYPES = _load_key_types()
  _PERSISTENT_STORE: dict[str, object] = {}
  _MEMORY_STORE: dict[str, object] = {}

  class Params:
    def __init__(self, d: str | None = None, memory: bool = False, return_defaults: bool = False):
      self.d = d if d is not None else ""
      self.m = memory
      self.return_defaults = return_defaults
      self._store = _MEMORY_STORE if memory else _PERSISTENT_STORE

    def __reduce__(self):
      return type(self), (self.d, self.m, self.return_defaults)

    def clear_all(self, tx_flag=ParamKeyFlag.ALL):
      self._store.clear()

    def check_key(self, key):
      if isinstance(key, bytes):
        key = key.decode("utf-8")
      return str(key)

    def _coerce_bool(self, value) -> bool:
      if isinstance(value, bool):
        return value
      if isinstance(value, (int, float)):
        return bool(value)
      if isinstance(value, bytes):
        return value == b"1"
      if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "none", "null")
      return bool(value)

    def _coerce_value(self, key: str, value):
      value_type = self.get_type(key)
      if value_type == ParamKeyType.BOOL:
        return self._coerce_bool(value)
      if value_type == ParamKeyType.INT:
        return int(float(value))
      if value_type == ParamKeyType.FLOAT:
        return float(value)
      if value_type == ParamKeyType.BYTES and isinstance(value, str):
        return value.encode("utf-8")
      return value

    def get(self, key, block: bool = False, return_default: bool = False, encoding=None, default=None):
      key = self.check_key(key)
      value = self._store.get(key, default)
      if value is None:
        return default
      if encoding is not None and isinstance(value, bytes):
        try:
          return value.decode(encoding)
        except Exception:
          return value.decode("utf-8", errors="replace")
      return value

    def get_bool(self, key, block: bool = False, default: bool = False):
      value = self.get(key, block=block, return_default=True, default=default)
      return self._coerce_bool(value)

    def get_int(self, key, block: bool = False, return_default: bool = False, default: int = 0):
      value = self.get(key, block=block, return_default=return_default, encoding="utf-8", default=default)
      if value is None or value == "":
        return default
      try:
        return int(float(value))
      except (TypeError, ValueError):
        return default

    def get_float(self, key, block: bool = False, return_default: bool = False, default: float = 0.0):
      value = self.get(key, block=block, return_default=return_default, encoding="utf-8", default=default)
      if value is None or value == "":
        return default
      try:
        return float(value)
      except (TypeError, ValueError):
        return default

    def put(self, key, dat):
      key = self.check_key(key)
      self._store[key] = self._coerce_value(key, dat)

    def put_bool(self, key, val: bool):
      self.put(key, bool(val))

    def put_nonblocking(self, key, dat):
      self.put(key, dat)

    def put_bool_nonblocking(self, key, val: bool):
      self.put_bool(key, val)

    def put_int(self, key, val):
      self.put(key, int(val))

    def put_float(self, key, val):
      self.put(key, float(val))

    def remove(self, key):
      self._store.pop(self.check_key(key), None)

    def get_param_path(self, key: str = ""):
      base = Path(tempfile.gettempdir()) / ("params_memory" if self.m else "params")
      base.mkdir(parents=True, exist_ok=True)
      return str(base / key) if key else str(base)

    def get_type(self, key):
      return _KEY_TYPES.get(self.check_key(key), ParamKeyType.STRING)

    def all_keys(self):
      return list(_KEY_TYPES)

    def get_default_value(self, key):
      return None

    def cpp2python(self, key, value):
      return self._coerce_value(self.check_key(key), value)

    def get_key_flag(self, key):
      return ParamKeyFlag.PERSISTENT

    def get_stock_value(self, key):
      return None

    def get_tuning_level(self, key):
      return 0

    def get_settings_tier(self, key):
      return _SETTINGS_TIERS.get(self.check_key(key), SETTINGS_ADVANCED)

else:
  assert _Params
  assert ParamKeyFlag
  assert ParamKeyType
  assert UnknownKeyName

  class Params(_Params):
    # A compiled binding built before a key was added does not know that key.
    # Keys declared in params_keys.h but missing from the binding are stored as
    # plain files in the same params directory instead of failing.
    def __init__(self, d="", *, memory=False, return_defaults=False):
      # The binding's own return_defaults field is private to Cython.
      self._extra_return_defaults = bool(return_defaults or memory)

    def _extra_name(self, key) -> str | None:
      name = key.decode("utf-8", errors="ignore") if isinstance(key, bytes) else str(key)
      cached = _EXTRA_KEY_CACHE.get(name)
      if cached is None:
        try:
          _Params.check_key(self, name)
          cached = False
        except UnknownKeyName:
          cached = name in _HEADER_KEYS
        _EXTRA_KEY_CACHE[name] = cached
      return name if cached else None

    def _extra_type(self, name):
      return getattr(ParamKeyType, _HEADER_KEYS[name]["type"], ParamKeyType.STRING)

    def _extra_path(self, name) -> str:
      return self.get_param_path(name)

    def _extra_read(self, name) -> bytes:
      try:
        with open(self._extra_path(name), "rb") as f:
          return f.read()
      except OSError:
        return b""

    @contextlib.contextmanager
    def _extra_lock(self):
      # Same lock file the C++ side takes for writes and clear_all.
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

    def _extra_write(self, name, data: bytes, locked: bool = False) -> None:
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

    def _extra_default_bytes(self, name):
      default = _HEADER_KEYS[name]["default"]
      return default.encode("utf-8") if default is not None else None

    def check_key(self, key):
      name = self._extra_name(key)
      if name is not None:
        return name.encode("utf-8")
      return super().check_key(key)

    def get_type(self, key):
      name = self._extra_name(key)
      if name is not None:
        return self._extra_type(name)
      return super().get_type(key)

    def get_default_value(self, key):
      name = self._extra_name(key)
      if name is not None:
        default = self._extra_default_bytes(name)
        return self._cpp2python(self._extra_type(name), default, None, name) if default is not None else None
      return super().get_default_value(key)

    def get_stock_value(self, key):
      name = self._extra_name(key)
      if name is not None:
        stock = _HEADER_KEYS[name]["stock"]
        return self._cpp2python(self._extra_type(name), stock.encode("utf-8"), None, name) if stock is not None else None
      return super().get_stock_value(key)

    def get_key_flag(self, key):
      name = self._extra_name(key)
      if name is not None:
        flag = 0
        for flag_name in _HEADER_KEYS[name]["flags"]:
          flag |= int(getattr(ParamKeyFlag, flag_name, 0))
        return flag
      return super().get_key_flag(key)

    def get_tuning_level(self, key):
      name = self._extra_name(key)
      if name is not None:
        return _HEADER_KEYS[name]["tuning"]
      return super().get_tuning_level(key)

    def cpp2python(self, key, value):
      name = self._extra_name(key)
      if name is not None:
        return self._cpp2python(self._extra_type(name), value, None, name)
      return super().cpp2python(key, value)

    def _extra_names(self) -> list[str]:
      known = {k.decode("utf-8", errors="ignore") if isinstance(k, bytes) else str(k) for k in _Params.all_keys(self)}
      return [name for name in _HEADER_KEYS if name not in known]

    def clear_all(self, tx_flag=ParamKeyFlag.ALL):
      # The C++ clear_all deletes every file whose key it does not know, which
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
      super().clear_all(tx_flag)
      if keep:
        with self._extra_lock():
          for name, raw in keep.items():
            self._extra_write(name, raw, locked=True)

    def all_keys(self):
      keys = list(super().all_keys())
      known = {k.decode("utf-8", errors="ignore") if isinstance(k, bytes) else str(k) for k in keys}
      keys.extend(name.encode("utf-8") for name in _HEADER_KEYS if name not in known)
      return keys

    def put(self, key, dat):
      name = self._extra_name(key)
      if name is not None:
        value_type = self._extra_type(name)
        data = self.python2cpp(type(dat), value_type, dat, name)
        self._extra_write(name, data.encode("utf-8") if isinstance(data, str) else data)
        return
      super().put(key, dat)

    def put_nonblocking(self, key, dat):
      if self._extra_name(key) is not None:
        self.put(key, dat)
        return
      super().put_nonblocking(key, dat)

    def put_bool(self, key, val):
      name = self._extra_name(key)
      if name is not None:
        self._extra_write(name, b"1" if val else b"0")
        return
      super().put_bool(key, val)

    def put_bool_nonblocking(self, key, val):
      if self._extra_name(key) is not None:
        self.put_bool(key, val)
        return
      super().put_bool_nonblocking(key, val)

    def remove(self, key):
      name = self._extra_name(key)
      if name is not None:
        with self._extra_lock():
          with contextlib.suppress(OSError):
            os.remove(self._extra_path(name))
        return
      super().remove(key)

    def get_settings_tier(self, key):
      name = self._extra_name(key)
      if name is not None:
        return _HEADER_KEYS[name]["tier"]
      try:
        return super().get_settings_tier(key)
      except AttributeError:
        return _SETTINGS_TIERS.get(self.check_key(key), SETTINGS_ADVANCED)

    def get(self, key, block=False, return_default=False, encoding=None, default=None):
      name = self._extra_name(key)
      if name is not None:
        value_type = self._extra_type(name)
        use_default = return_default or (getattr(self, "_extra_return_defaults", False) and not block)
        default_bytes = self._extra_default_bytes(name) if use_default else None
        raw = self._extra_read(name)
        value = self._cpp2python(value_type, raw if raw != b"" else default_bytes, default_bytes, name)
        if value is None:
          return default
        if encoding is not None and isinstance(value, bytes):
          try:
            return value.decode(encoding)
          except Exception:
            return value.decode("utf-8", errors="replace")
        return value
      try:
        value = super().get(key, block=block, return_default=return_default)
      except UnknownKeyName:
        return default
      if value is None:
        return default
      if encoding is not None and isinstance(value, bytes):
        try:
          return value.decode(encoding)
        except Exception:
          return value.decode("utf-8", errors="replace")
      return value

    def get_bool(self, key, block=False, default=False):
      result = self.get(key, block=block, return_default=True)
      if result is None:
        return bool(default)
      return bool(result)

    def get_int(self, key, block=False, return_default=False, default=0):
      val = self.get(key, block=block, return_default=return_default, encoding="utf-8")
      if val is None or val == "":
        return default
      try:
        return int(float(val))
      except ValueError:
        return default

    def get_float(self, key, block=False, return_default=False, default=0.0):
      val = self.get(key, block=block, return_default=return_default, encoding="utf-8")
      if val is None or val == "":
        return default
      try:
        return float(val)
      except ValueError:
        return default

    def put_int(self, key, val):
      t = self.get_type(key)
      if t == ParamKeyType.FLOAT:
        self.put(key, float(val))
      elif t == ParamKeyType.INT:
        self.put(key, int(val))
      elif t == ParamKeyType.BOOL:
        self.put(key, bool(val))
      else:
        self.put(key, str(int(val)))

    def put_float(self, key, val):
      t = self.get_type(key)
      if t == ParamKeyType.FLOAT:
        self.put(key, float(val))
      elif t == ParamKeyType.INT:
        self.put(key, int(val))
      elif t == ParamKeyType.BOOL:
        self.put(key, bool(val))
      else:
        self.put(key, str(float(val)))


if __name__ == "__main__":
  import sys

  params = Params()
  key = sys.argv[1]
  assert params.check_key(key), f"unknown param: {key}"

  if len(sys.argv) == 3:
    val = sys.argv[2]
    print(f"SET: {key} = {val}")
    params.put(key, val)
  elif len(sys.argv) == 2:
    print(f"GET: {key} = {params.get(key)}")
