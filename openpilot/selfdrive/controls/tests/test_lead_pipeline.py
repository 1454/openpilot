import unittest
from unittest.mock import patch

from openpilot.cereal import log
from openpilot.selfdrive.controls.lib.added_toggles import AddedToggles
from openpilot.selfdrive.controls.lib.lead_pipeline import LEAD_TOGGLE_KEYS, RADAR_TO_CAMERA, LeadPipeline


def vision_lead(lead_msg, v_ego, model_v_ego):
  """The dictionary radard builds for a vision-only lead."""
  v_rel = lead_msg.v[0] - model_v_ego
  return {
    "dRel": float(lead_msg.x[0] - RADAR_TO_CAMERA), "yRel": float(-lead_msg.y[0]), "vRel": float(v_rel),
    "vLead": float(v_ego + v_rel), "vLeadK": float(v_ego + v_rel), "aLeadK": float(lead_msg.a[0]),
    "aLeadTau": 0.3, "modelProb": float(lead_msg.prob), "present": True, "radar": False, "radarTrackId": -1,
  }


def closing_model(frame, count=3, ego=33.):
  t = frame * .05
  braking_time = max(0., t - 2.)
  distance = 140. - 3. * t - .7 * braking_time**2
  speed = 30. - 1.4 * braking_time
  model = log.ModelDataV2.new_message()
  model.velocity.x = [ego]
  model.orientationRate.z = [0.]
  for i, lead in enumerate(model.init('leadsV3', count)):
    times = [0., 2., 4., 6., 8., 10.]
    lead.t = times
    lead.prob = .99
    lead.x = [distance + 1.52 + speed * dt - .7 * dt**2 for dt in times]
    lead.xStd = [2.] * 6
    lead.y, lead.yStd = [0.] * 6, [1.] * 6
    lead.v, lead.vStd = [32.] * 6, [1.] * 6
    lead.a, lead.aStd = [0.] * 6, [1.] * 6
    if i == 2:
      # This extra prediction is not one of radard's two accepted lead slots.
      lead.x, lead.y, lead.v = [10.] * 6, [3.] * 6, [0.] * 6
  return model


class TestLeadPipeline(unittest.TestCase):
  def setUp(self):
    self.logging = patch('openpilot.selfdrive.controls.lib.lead_pipeline.cloudlog')
    self.logging.start()
    self.addCleanup(self.logging.stop)

  def update(self, pipeline, model, frame, ego=33., valid=True, **flags):
    # Exercise the serialized model shape received by the real caller.
    payload = model.to_bytes()
    model.clear_write_flag()
    with log.ModelDataV2.from_bytes(payload) as decoded:
      leads_v3 = decoded.leadsV3
      raw = [vision_lead(leads_v3[i], ego, decoded.velocity.x[0]) for i in range(2)] if len(leads_v3) > 1 else [{'present': False}] * 2
      before = [dict(lead) for lead in raw]
      flags = {'enabled': True, 'future_enabled': True, 'responsive_enabled': True, **flags}
      out = pipeline.update(leads_v3, raw, 1. + frame * .05, ego, 0., valid, **flags)
      self.assertEqual(raw, before)
      return [dict(lead) for lead in out], raw

  def warm(self, pipeline, count=3, **flags):
    peak = 0.
    for frame in range(180):
      self.update(pipeline, closing_model(frame, count), frame, **flags)
      peak = max(peak, -pipeline.responsive.delta)
    return peak

  def test_disabled_returns_radard_leads_untouched(self):
    pipeline = LeadPipeline()
    for frame in range(60):
      out, raw = self.update(pipeline, closing_model(frame), frame, enabled=False)
      self.assertEqual(out, raw)
    self.assertFalse(pipeline.was_enabled)
    self.assertEqual(pipeline.tracker.slots, [None, None])

  def test_three_model_predictions_reach_responsive_stage(self):
    two, three, tracker_only = LeadPipeline(), LeadPipeline(), LeadPipeline()
    peak = 0.
    for frame in range(180):
      actual, _ = self.update(three, closing_model(frame), frame)
      expected, _ = self.update(two, closing_model(frame, 2), frame)
      prior, _ = self.update(tracker_only, closing_model(frame), frame, future_enabled=True, responsive_enabled=False)
      self.assertEqual(actual, expected)
      self.assertNotEqual(three.responsive.reason, 'unsupported')
      for before, after in zip(prior, actual, strict=True):
        for field in ('present', 'dRel', 'yRel', 'aLeadK', 'aLeadTau'):
          self.assertEqual(before[field], after[field])
        self.assertLessEqual(after['vLead'], before['vLead'])
        peak = max(peak, before['vLead'] - after['vLead'])
    self.assertGreater(peak, .1)
    self.assertFalse(three.responsive_failed)

  def test_stage_toggles_are_independent(self):
    base, future, responsive = LeadPipeline(), LeadPipeline(), LeadPipeline()
    future_changed = responsive_changed = False
    for frame in range(180):
      plain, _ = self.update(base, closing_model(frame), frame, future_enabled=False, responsive_enabled=False)
      with_future, _ = self.update(future, closing_model(frame), frame, responsive_enabled=False)
      with_responsive, _ = self.update(responsive, closing_model(frame), frame, future_enabled=False)
      future_changed |= with_future != plain
      responsive_changed |= with_responsive != plain
    self.assertEqual(base.future.status[0].reason, 'reset')
    self.assertEqual(base.responsive.delta, 0.)
    self.assertTrue(future_changed or future.future.status[0].reason != 'reset')
    self.assertTrue(responsive_changed)

  def test_turning_a_stage_off_drops_its_correction(self):
    pipeline = LeadPipeline()
    self.assertGreater(self.warm(pipeline), .1)
    self.update(pipeline, closing_model(180), 180, responsive_enabled=False, future_enabled=False)
    self.assertEqual(pipeline.responsive.delta, 0.)
    self.assertIsNone(pipeline.responsive.motion.kalman)
    self.update(pipeline, closing_model(181), 181, enabled=False)
    self.assertEqual(pipeline.tracker.slots, [None, None])

  def test_low_speed_and_invalid_messages_revoke_active_correction(self):
    for ego, valid in ((14., True), (33., False)):
      with self.subTest(ego=ego, valid=valid):
        pipeline = LeadPipeline()
        self.assertGreater(self.warm(pipeline), .1)
        self.update(pipeline, closing_model(180, ego=ego), 180, ego=ego, valid=valid)
        self.assertEqual(pipeline.responsive.reason, 'unsupported')
        self.assertEqual(pipeline.responsive.delta, 0.)
        self.assertIsNone(pipeline.responsive.motion.kalman)

  def test_stage_failure_keeps_accepted_leads(self):
    pipeline = LeadPipeline()
    self.warm(pipeline)
    with patch.object(pipeline.responsive, 'update', side_effect=RuntimeError):
      out, _ = self.update(pipeline, closing_model(180), 180)
    self.assertTrue(pipeline.responsive_failed and not pipeline.failed)
    self.assertTrue(out[0]['present'])
    with patch.object(pipeline.tracker, 'update', side_effect=RuntimeError):
      out, raw = self.update(pipeline, closing_model(181), 181)
    self.assertTrue(pipeline.failed)
    self.assertEqual(out, raw)


class FakeParams:
  def __init__(self, **values):
    self.values = values
    self.fail = False

  def get_bool(self, key):
    if self.fail:
      raise OSError
    return self.values.get(key, False)


class TestAddedToggles(unittest.TestCase):
  def test_defaults_safe_mode_and_failures_read_off(self):
    now = [0.]
    params = FakeParams(BatmobileLeadTracker=True)
    toggles = AddedToggles(LEAD_TOGGLE_KEYS, params, clock=lambda: now[0])
    self.assertEqual(toggles.update(), {'BatmobileLeadTracker': True, 'LeadFutureMotion': False, 'LeadResponsiveMotion': False})
    params.values['SafeMode'] = True
    self.assertTrue(toggles.update()['BatmobileLeadTracker'])  # not yet re-read
    now[0] = 1.
    self.assertFalse(any(toggles.update().values()))
    params.values['SafeMode'] = False
    params.fail = True
    now[0] = 2.
    self.assertFalse(any(toggles.update().values()))
    self.assertFalse(any(AddedToggles(LEAD_TOGGLE_KEYS, None).update().values()))
    params.fail = False
    now[0] = 3.
    self.assertTrue(toggles.update()['BatmobileLeadTracker'])
    self.assertFalse(any(toggles.update(allowed=False).values()))


if __name__ == '__main__':
  unittest.main()
