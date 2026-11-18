"""Slow, failure-tolerant reads of the Added Controls test toggles.

Every key defaults to off. Safe Mode and any read failure also read as off,
so a missing or unreadable parameter can never enable an experiment.
"""
import time

POLL_SECONDS = 1.0


class AddedToggles:
  def __init__(self, keys, params=None, period=POLL_SECONDS, clock=time.monotonic):
    self.keys = tuple(keys)
    self.params = params
    self.period = period
    self.clock = clock
    self.values = dict.fromkeys(self.keys, False)
    self.last_read = None

  def _read(self):
    try:
      if self.params.get_bool("SafeMode"):
        return dict.fromkeys(self.keys, False)
      return {key: bool(self.params.get_bool(key)) for key in self.keys}
    except Exception:
      return dict.fromkeys(self.keys, False)

  def update(self, allowed=True):
    if self.params is None or not allowed:
      self.values = dict.fromkeys(self.keys, False)
      self.last_read = None
      return self.values
    now = self.clock()
    if self.last_read is None or now - self.last_read >= self.period:
      self.values = self._read()
      self.last_read = now
    return self.values

  def __getitem__(self, key):
    return self.values[key]
