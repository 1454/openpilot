"""Playback device choice for soundd. Pure functions so they test without PortAudio."""

# ALSA PCMs that go through the OS configured route, the same one PortAudio's default output uses.
# Raw hw:/plughw: PCMs on the tavil card are frontends that play nowhere without mixer routing,
# and nothing in this tree says which one feeds the MAX98089, so they are never tried.
SYSTEM_ROUTE_NAMES = ("pulse", "pipewire", "default", "sysdefault")


def _route_rank(name: str) -> int | None:
  base = name.strip().lower().split(":", 1)[0]
  if base in SYSTEM_ROUTE_NAMES:
    return SYSTEM_ROUTE_NAMES.index(base)
  return None


def output_device_order(devices) -> list[int | None]:
  """Stock soundd opens the default output with no device argument. Try that first, then only system routes."""
  ranked = []
  for index, dev in enumerate(devices):
    if int(dev.get("max_output_channels", 0) or 0) <= 0:
      continue
    rank = _route_rank(str(dev.get("name", "")))
    if rank is not None:
      ranked.append((rank, index))
  ranked.sort()
  return [None, *(index for _, index in ranked)]


def describe_devices(devices) -> str:
  return ", ".join(
    f"{index}:{dev.get('name', '')}(out={dev.get('max_output_channels', 0)},in={dev.get('max_input_channels', 0)})"
    for index, dev in enumerate(devices)
  )
