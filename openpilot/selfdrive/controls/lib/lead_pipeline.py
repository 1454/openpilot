"""Optional batmobile vision lead stages for radard.

Off by default. The tracker, confidence filter and transition recovery are the
base stage; the future-position and responsive stages each need the base stage
and can be switched separately. A failure in any stage returns the leads radard
already selected and turns that stage off until the next process start.
"""
import math

from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.controls.lib.lead_future_motion import LeadFutureMotion
from openpilot.selfdrive.controls.lib.lead_reacquisition import LeadReacquisitionShadow
from openpilot.selfdrive.controls.lib.lead_responsive_motion import LeadResponsiveMotion
from openpilot.selfdrive.controls.lib.lead_transition import LeadTransitionRecovery
from openpilot.selfdrive.controls.lib.vision_lead_confidence import LeadUncertainty, VisionLeadConfidenceFilter
from openpilot.selfdrive.controls.lib.vision_lead_tracker import VisionLeadObservation, VisionLeadTracker

RADAR_TO_CAMERA = 1.52
TRACKER_KEY = "BatmobileLeadTracker"
FUTURE_KEY = "LeadFutureMotion"
RESPONSIVE_KEY = "LeadResponsiveMotion"
LEAD_TOGGLE_KEYS = (TRACKER_KEY, FUTURE_KEY, RESPONSIVE_KEY)


def _first(values):
  return float(values[0]) if len(values) else float('nan')


class LeadPipeline:
  def __init__(self):
    self.tracker = VisionLeadTracker()
    self.confidence = VisionLeadConfidenceFilter()
    self.transition = LeadTransitionRecovery()
    self.future = LeadFutureMotion()
    self.responsive = LeadResponsiveMotion()
    self.shadow = LeadReacquisitionShadow()
    self.failed = False
    self.future_failed = False
    self.responsive_failed = False
    self.was_enabled = False
    self.was_future = False
    self.was_responsive = False
    self.last_log = 0.0

  def reset(self):
    self.tracker.reset()
    self.confidence.reset()
    self.transition.reset()
    self.future.reset()
    self.responsive.reset()
    if self.shadow is not None:
      self.shadow.reset('missing_model')
    self.was_enabled = self.was_future = self.was_responsive = False

  def update(self, model_leads, raw_leads, timestamp, v_ego, yaw_rate, valid, *,
             enabled, future_enabled=False, responsive_enabled=False):
    """Return the two lead dictionaries to publish. `raw_leads` is never modified."""
    if not enabled or self.failed or len(model_leads) < 2 or len(raw_leads) != 2:
      if self.was_enabled:
        self.reset()
      return raw_leads
    try:
      return self._update(model_leads, raw_leads, timestamp, v_ego, yaw_rate, valid,
                          future_enabled and not self.future_failed,
                          responsive_enabled and not self.responsive_failed)
    except Exception:
      cloudlog.exception("batmobile lead tracker disabled after failure")
      self.failed = True
      return raw_leads

  def _update(self, model_leads, raw_leads, timestamp, v_ego, yaw_rate, valid, future_enabled, responsive_enabled):
    self.was_enabled = True
    # Every stage must see the same two slots, even when the model publishes more.
    model_leads = [model_leads[i] for i in range(2)]
    observations, uncertainties = [], []
    for lead_msg in model_leads:
      observations.append(VisionLeadObservation(float(lead_msg.x[0] - RADAR_TO_CAMERA), float(-lead_msg.y[0]),
                                                _first(lead_msg.xStd), float(lead_msg.prob)))
      uncertainties.append(LeadUncertainty(float(lead_msg.prob), observations[-1].std,
                                           _first(lead_msg.vStd), _first(lead_msg.aStd)))
    baseline = self.tracker.update(observations, raw_leads, timestamp, v_ego, valid, yaw_rate=yaw_rate)
    leads = self.confidence.update(raw_leads, baseline, uncertainties, self.tracker.slots, timestamp, v_ego, valid)
    leads = self.transition.update(observations, raw_leads, leads, [u.speed_std for u in uncertainties],
                                   timestamp, v_ego, yaw_rate, valid)

    if future_enabled:
      try:
        leads = self.future.update(model_leads, raw_leads, leads, self.tracker.slots, timestamp, v_ego, valid)
      except Exception:
        cloudlog.exception("future lead position stage disabled after failure")
        self.future_failed = True
    elif self.was_future:
      self.future.reset()
    self.was_future = future_enabled and not self.future_failed

    if responsive_enabled:
      try:
        leads = self.responsive.update(model_leads, observations, raw_leads, leads, timestamp, v_ego, yaw_rate, valid)
      except Exception:
        # A failed stage must leave the already accepted lead output intact.
        cloudlog.exception("responsive lead stage disabled after failure")
        self.responsive_failed = True
    elif self.was_responsive:
      self.responsive.reset()
    self.was_responsive = responsive_enabled and not self.responsive_failed

    # Read-only observer: its candidates never replace `leads`.
    if self.shadow is not None:
      try:
        self.shadow.update(observations, raw_leads, leads, self.tracker.slots, timestamp, v_ego, yaw_rate, valid)
      except Exception:
        cloudlog.exception("lead reacquisition observer disabled after failure")
        self.shadow = None

    if math.isfinite(timestamp) and timestamp - self.last_log >= 1.0:
      self.last_log = timestamp
      for i in range(2):
        track = self.tracker.slots[i]
        future = self.future.status[i]
        cloudlog.info(" ".join((
          f"lead-pre: slot={i} mode={track.mode if track else 'fallback'}",
          f"age={track.age if track else 0.0:.2f} ready={int(track.ready) if track else 0}",
          f"v={leads[i].get('vLead', 0.0):.2f} raw_v={raw_leads[i].get('vLead', 0.0):.2f}",
          f"confidence={self.confidence.status[i].reason}",
          f"lead_transition={self.transition.reason} dv={self.transition.correction:.2f}",
          f"future_motion={future.reason if self.was_future else 'off'} future_dv={future.delta_speed:.3f} future_dr={future.delta_distance:.3f}",
          f"responsive={self.responsive.reason if self.was_responsive else 'off'}",
          f"responsive_dv={self.responsive.delta if self.was_responsive else 0.0:.3f}",
        )))
    return leads
