from openpilot.selfdrive.ui.sound_devices import describe_devices, output_device_order


def _tizi_devices():
  devices = [{"name": f"sdm845-tavil-snd-card: - (hw:0,{n})", "max_output_channels": 1} for n in range(20)]
  devices.append({"name": "dmix", "max_output_channels": 2})
  devices.append({"name": "plughw:0,0", "max_output_channels": 2})
  devices.extend({"name": f"capture-{n}", "max_output_channels": 0} for n in range(9))
  devices.append({"name": "sysdefault", "max_output_channels": 128})
  devices.append({"name": "default", "max_output_channels": 128})
  devices.append({"name": "pulse", "max_output_channels": 32})
  return devices


def test_default_output_is_tried_first():
  assert output_device_order(_tizi_devices())[0] is None
  assert output_device_order([]) == [None]


def test_raw_tavil_pcms_are_never_tried():
  devices = _tizi_devices()
  order = output_device_order(devices)
  for index in order[1:]:
    name = devices[index]["name"]
    assert "hw:" not in name and "tavil" not in name and name != "dmix"


def test_system_routes_follow_default_in_route_order():
  devices = _tizi_devices()
  names = [devices[index]["name"] for index in output_device_order(devices)[1:]]
  assert names == ["pulse", "default", "sysdefault"]


def test_input_only_routes_are_skipped():
  devices = [{"name": "pulse", "max_output_channels": 0}, {"name": "default", "max_output_channels": 2}]
  assert output_device_order(devices) == [None, 1]


def test_named_card_route_counts_as_system_route():
  devices = [{"name": "sysdefault:CARD=sdm845tavilsndc", "max_output_channels": 2}]
  assert output_device_order(devices) == [None, 0]


def test_describe_devices_lists_every_device():
  text = describe_devices([{"name": "pulse", "max_output_channels": 32, "max_input_channels": 32}])
  assert text == "0:pulse(out=32,in=32)"
