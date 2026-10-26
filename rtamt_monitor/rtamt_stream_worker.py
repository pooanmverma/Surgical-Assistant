#!/usr/bin/env python3
"""Persistent online monitor worker for mined surgical-workflow rules.

Protocol (one JSON object per line):
  stdin : {"frame": int, "time_sec": float, "tools": [seven 0/1 values]}
  stdout: one JSON response per request (see process_message)
  stderr: diagnostics only

Supported rule types (taken from the mined-constraints JSON):

  MutEx_tool_A_tool_B          always (not (A and B))
      Evaluated per frame as the predicate  (A <= 0.5) or (B <= 0.5).
      Backend: RTAMT when it is importable, otherwise an equivalent native
      computation.  With binary inputs the robustness is +0.5 (safe) or
      -0.5 (both active), i.e. effectively Boolean.  The global verdict is the
      running minimum, which equals always[0,inf] of the body.

  Response_tool_A_tool_B       always (A -> eventually[lo,hi] B)
      Evaluated with a native deadline-timer monitor (see
      BoundedResponseMonitor).  It does not depend on RTAMT's bounded-future
      support.  The verdict is Boolean (+1 / -1), and a violation is reported
      at the first frame after the deadline, i.e. T seconds after the trigger.

  CoActivation_tool_A_tool_B   always (A -> B > confidence), same frame
      With 0/1 signals this means: whenever A is active, B must be active.
      Backend: RTAMT when importable, otherwise an equivalent native check.

Other templates (Precedence) are skipped with a warning.

Run `python rtamt_stream_worker.py --selftest` to check the monitors.
"""

import argparse
import json
import re
import sys
import traceback
from collections import deque

try:
    import rtamt  # optional: only used for MutEx
except ImportError:  # pragma: no cover
    rtamt = None


DEFAULT_RULES_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "merged_constraints_mutex_only.json"
)
NUM_TOOLS = 7
EPS = 1e-9
MUTEX_RE = re.compile(r"^MutEx_tool_(\d+)_tool_(\d+)$")
RESPONSE_RE = re.compile(r"^(?:Bounded)?Response_tool_(\d+)_tool_(\d+)$")
COACT_RE = re.compile(r"^CoActivation_tool_(\d+)_tool_(\d+)$")


def log(message):
    print(message, file=sys.stderr, flush=True)


def scalar_value(value):
    if hasattr(value, "item"):
        value = value.item()
    return float(value)


class _RuleBase:
    """Shared bookkeeping: running minimum robustness and latched verdict."""

    rule_type = ""

    def __init__(self, name, description):
        self.name = name
        self.description = description
        self.min_robustness = None
        self.violation_seen = False
        self.first_violation_time = None

    def _record(self, t, robustness):
        violation = robustness < 0.0
        if self.min_robustness is None or robustness < self.min_robustness:
            self.min_robustness = robustness
        if violation and not self.violation_seen:
            self.first_violation_time = t
        self.violation_seen = self.violation_seen or violation
        return {
            "name": self.name,
            "description": self.description,
            "rule_type": self.rule_type,
            "robustness": robustness,
            "frame_status": "VIOLATION" if violation else "SAFE",
            "cumulative_robustness": self.min_robustness,
            "overall_rule_status": "VIOLATION" if self.violation_seen else "SAFE",
            "first_violation_time": self.first_violation_time,
        }


class MutExMonitor(_RuleBase):
    rule_type = "MutEx"

    def __init__(self, name, description, tool_a, tool_b, use_rtamt=True):
        super().__init__(name, description)
        self.tool_a = tool_a
        self.tool_b = tool_b
        self.spec = None
        if use_rtamt and rtamt is not None:
            spec = rtamt.StlDiscreteTimeSpecification()
            spec.declare_var(f"T{tool_a}", "float")
            spec.declare_var(f"T{tool_b}", "float")
            # Safe when at least one of the two tools is inactive.
            spec.spec = f"((T{tool_a} <= 0.5) or (T{tool_b} <= 0.5))"
            spec.parse()
            self.spec = spec
        self.backend = "rtamt" if self.spec is not None else "native"

    def step(self, t, signals):
        a, b = signals[self.tool_a], signals[self.tool_b]
        if self.spec is not None:
            robustness = scalar_value(
                self.spec.update(t, [(f"T{self.tool_a}", a), (f"T{self.tool_b}", b)])
            )
        else:
            # Robustness of (a <= 0.5) or (b <= 0.5) = max(0.5 - a, 0.5 - b).
            robustness = max(0.5 - a, 0.5 - b)
        result = self._record(t, robustness)
        result["backend"] = self.backend
        return result


class CoActivationMonitor(_RuleBase):
    """always (primary -> required > confidence), evaluated on the same frame.

    This is the mined CoActivation template: whenever `primary` is active,
    `required` must be present.  The tool signals are binary 0/1, so the
    mined "confidence > 0.9" means required == 1.  Per-frame robustness is
    max(0.5 - primary, required - confidence): +0.5 when primary is inactive,
    1 - confidence (0.1 for 0.9) when both are active, and negative when
    primary is active and required is not.  RTAMT computes it when importable.
    """

    rule_type = "CoActivation"

    def __init__(self, name, description, primary, required, confidence=0.9, use_rtamt=True):
        super().__init__(name, description)
        if not 0.0 <= confidence < 1.0:
            raise ValueError(
                f"CoActivation {name!r}: confidence must be in [0, 1) for 0/1 signals, got {confidence}"
            )
        self.primary = primary
        self.required = required
        self.confidence = float(confidence)
        self.spec = None
        if use_rtamt and rtamt is not None:
            spec = rtamt.StlDiscreteTimeSpecification()
            spec.declare_var(f"T{primary}", "float")
            spec.declare_var(f"T{required}", "float")
            spec.spec = f"((T{primary} <= 0.5) or (T{required} > {self.confidence}))"
            spec.parse()
            self.spec = spec
        self.backend = "rtamt" if self.spec is not None else "native"

    def step(self, t, signals):
        a, b = signals[self.primary], signals[self.required]
        if self.spec is not None:
            robustness = scalar_value(
                self.spec.update(t, [(f"T{self.primary}", a), (f"T{self.required}", b)])
            )
        else:
            robustness = max(0.5 - a, b - self.confidence)
        result = self._record(t, robustness)
        result["backend"] = self.backend
        return result


class BoundedResponseMonitor(_RuleBase):
    """Online monitor for  always (trigger -> eventually[lower, upper] response).

    Every frame t where `trigger` holds opens an obligation: `response` must
    hold at some frame in [t + lower, t + upper].  A response before
    t + lower does not count.  An obligation is violated at the first frame
    after t + upper without a response in its window.  Obligations are kept
    in a queue ordered by start time, so a response frame r discharges
    exactly those with start <= r (their end has not passed, expired ones
    are removed first).  With lower = 0 this is the plain deadline monitor.
    """

    rule_type = "BoundedResponse"
    backend = "native"

    def __init__(self, name, description, trigger, response, deadline, lower=0.0):
        super().__init__(name, description)
        if lower < 0 or deadline < lower:
            raise ValueError(f"Invalid interval [{lower}, {deadline}] in rule {name!r}")
        self.trigger = trigger
        self.response = response
        self.lower = float(lower)
        self.deadline = float(deadline)
        self.pending = deque()  # (window_start, window_end)

    def step(self, t, signals):
        trig = signals[self.trigger] > 0.5
        resp = signals[self.response] > 0.5

        violation = False
        while self.pending and self.pending[0][1] + EPS < t:
            self.pending.popleft()
            violation = True
        if trig:
            self.pending.append((t + self.lower, t + self.deadline))
        if resp:
            while self.pending and self.pending[0][0] <= t + EPS:
                self.pending.popleft()

        result = self._record(t, -1.0 if violation else 1.0)
        result["backend"] = self.backend
        result["lower"] = self.lower
        result["deadline"] = self.deadline
        result["pending"] = len(self.pending)
        result["time_to_deadline"] = (
            self.pending[0][1] - t if self.pending else None
        )
        return result


def _deadline_of(rule):
    deadline = (rule.get("parameters") or {}).get("deadline")
    if deadline is None:
        raise ValueError(f"Response rule {rule.get('name')!r} has no 'deadline' parameter.")
    return float(deadline)


def prepare_rules(rules, dedupe="max"):
    """Collapse repeated names.  Returns a list of (display_name, rule, lower, upper).

    The merged file can hold the same Response rule several times with
    different deadlines (one per mined video).  dedupe:
      max      : one rule, "within d" with d = loosest deadline: [0, d]
                 (holds on every mined video; default)
      min      : one rule, [0, tightest deadline]
      all      : keep every deadline as [0, d]; extra copies are named
                 <name>@<d>s (the tightest one dominates)
      interval : one rule, window [min deadline, max deadline]; this changes
                 the meaning (an early response no longer counts)
    Repeated MutEx / other rules are identical, so one copy is kept.
    """
    groups = {}
    for rule in rules:
        groups.setdefault(rule.get("name", ""), []).append(rule)

    out = []
    for name, items in groups.items():
        if not RESPONSE_RE.match(name):
            out.append((name, items[0], 0.0, 0.0))
            continue
        deadlines = sorted({_deadline_of(r) for r in items})
        if dedupe == "interval":
            lower = deadlines[0] if len(deadlines) > 1 else 0.0
            out.append((name, items[0], lower, deadlines[-1]))
            continue
        chosen = {"max": deadlines[-1:], "min": deadlines[:1]}.get(dedupe, deadlines)
        for d in chosen:
            display = name if len(chosen) == 1 else f"{name}@{d:g}s"
            out.append((display, items[0], 0.0, d))
    return out


def build_monitors(rules_file, use_rtamt=True, dedupe="max"):
    with open(rules_file, "r", encoding="utf-8") as handle:
        rules = json.load(handle)
    if not isinstance(rules, list):
        raise ValueError("Rules file must contain a JSON list.")

    monitors, seen, skipped = [], set(), []
    for display_name, rule, lower, upper in prepare_rules(rules, dedupe):
        name = rule.get("name", "")
        description = rule.get("description", "")
        if display_name in seen:
            raise ValueError(f"Duplicate rule name: {display_name!r}")
        seen.add(display_name)

        mutex = MUTEX_RE.match(name)
        response = RESPONSE_RE.match(name)
        coact = COACT_RE.match(name)
        if coact:
            a, b = map(int, coact.groups())
            _check_tools(name, a, b)
            conf = float((rule.get("parameters") or {}).get("confidence", 0.9))
            monitors.append(
                CoActivationMonitor(display_name, description, a, b, conf, use_rtamt)
            )
        elif mutex:
            a, b = map(int, mutex.groups())
            _check_tools(name, a, b)
            monitors.append(MutExMonitor(display_name, description, a, b, use_rtamt))
        elif response:
            a, b = map(int, response.groups())
            _check_tools(name, a, b)
            window = f"[{lower:g}, {upper:g}]" if lower > 0 else f"{upper:g}"
            description = re.sub(r"within [\d.]+s", f"within {window} s", description)
            monitors.append(
                BoundedResponseMonitor(display_name, description, a, b, upper, lower)
            )
        else:
            skipped.append(display_name)

    if not monitors:
        raise RuntimeError(f"No supported rules found in {rules_file}")

    for m in monitors:
        extra = (f" window=[{m.lower:g}, {m.deadline:g}]s" if m.rule_type == "BoundedResponse"
                 else f" confidence>{m.confidence:g}" if m.rule_type == "CoActivation" else "")
        log(f"  [{m.rule_type:<15}] {m.name}{extra} ({m.backend}) | {m.description}")
    if skipped:
        log(f"Skipped {len(skipped)} unsupported rules: {skipped}")
    log(f"Loaded {len(monitors)} monitors from {rules_file}.")
    return monitors


def _check_tools(name, a, b):
    if not (0 <= a < NUM_TOOLS and 0 <= b < NUM_TOOLS):
        raise ValueError(f"Tool id outside 0..{NUM_TOOLS - 1} in rule {name!r}")
    if a == b:
        raise ValueError(f"Rule refers to the same tool twice: {name!r}")


def process_message(msg, monitors):
    frame = int(msg["frame"])
    t = float(msg["time_sec"])
    raw = msg["tools"]
    if not isinstance(raw, list) or len(raw) != NUM_TOOLS:
        raise ValueError(f"Expected exactly {NUM_TOOLS} tool-presence values.")
    signals = [float(v) for v in raw]
    if any(v not in (0.0, 1.0) for v in signals):
        raise ValueError(f"Tool signals must be binary 0/1; received {signals!r}.")

    results = [m.step(t, signals) for m in monitors]
    frame_violation = any(r["frame_status"] == "VIOLATION" for r in results)
    overall_violation = any(r["overall_rule_status"] == "VIOLATION" for r in results)
    return {
        "frame": frame,
        "time_sec": t,
        "tools": signals,
        "frame_status": "VIOLATION" if frame_violation else "SAFE",
        "overall_status": "VIOLATION" if overall_violation else "SAFE",
        "rules": results,
    }


def serve(monitors):
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = None
        try:
            msg = json.loads(line)
            response = process_message(msg, monitors)
        except Exception as exc:  # report to the client, keep serving
            traceback.print_exc(file=sys.stderr)
            response = {
                "error": str(exc),
                "frame": msg.get("frame") if isinstance(msg, dict) else None,
            }
        print(json.dumps(response), flush=True)


# ---------------------------------------------------------------- self-test
def _tools(*active):
    return [1.0 if i in active else 0.0 for i in range(NUM_TOOLS)]


def _violation_times(monitor, sequence, dt=0.04):
    out = []
    for i, tools in enumerate(sequence):
        res = monitor.step(i * dt, tools)
        if res["frame_status"] == "VIOLATION":
            out.append(round(i * dt, 3))
    return out


def run_selftest(use_rtamt=True):
    """Deadline T = 1.0 s at 25 FPS (dt = 0.04 s); trigger = tool 4, response = tool 0."""
    def mon():
        return BoundedResponseMonitor("r", "", trigger=4, response=0, deadline=1.0)

    n = 60
    # 1. Response before the deadline -> no violation.
    seq = [_tools(4) if i == 0 else (_tools(0) if i == 20 else _tools()) for i in range(n)]
    assert _violation_times(mon(), seq) == [], "response at 0.8 s must satisfy T=1.0"

    # 2. Response exactly at the deadline (t = 1.0 s) -> still satisfied.
    seq = [_tools(4) if i == 0 else (_tools(0) if i == 25 else _tools()) for i in range(n)]
    assert _violation_times(mon(), seq) == [], "response at t = T must satisfy [0, T]"

    # 3. No response -> violation at the first frame after the deadline (1.04 s).
    seq = [_tools(4) if i == 0 else _tools() for i in range(n)]
    assert _violation_times(mon(), seq) == [1.04], "violation expected at 1.04 s"

    # 4. Trigger and response in the same frame -> no obligation.
    seq = [_tools(4, 0) if i == 0 else _tools() for i in range(n)]
    assert _violation_times(mon(), seq) == [], "simultaneous response is allowed"

    # 5. Trigger held for the whole trace, response never -> repeated violations,
    #    each deadline renews at the frame where the previous one expired.
    seq = [_tools(4) for _ in range(n)]
    v = _violation_times(mon(), seq)
    assert v and v[0] == 1.04 and all(b > a for a, b in zip(v, v[1:])), v

    # 6. A response resets the obligation; a later trigger starts a new one.
    #    Second trigger at frame 30 (t = 1.2 s) has deadline 2.2 s, so the
    #    violation is flagged at frame 56 (t = 2.24 s).
    seq = [_tools() for _ in range(n)]
    seq[0], seq[10], seq[30] = _tools(4), _tools(0), _tools(4)
    assert _violation_times(mon(), seq) == [2.24], "second obligation must expire at 2.24 s"

    # 7. Interval [0.5, 1.0]: response must fall inside the window.
    def imon():
        return BoundedResponseMonitor("i", "", trigger=4, response=0, deadline=1.0, lower=0.5)
    seq = [_tools(4) if i == 0 else (_tools(0) if i == 5 else _tools()) for i in range(n)]
    assert _violation_times(imon(), seq) == [1.04], "response at 0.2 s is too early"
    seq = [_tools(4) if i == 0 else (_tools(0) if i in (5, 15) else _tools()) for i in range(n)]
    assert _violation_times(imon(), seq) == [], "second response at 0.6 s is inside the window"
    seq = [_tools(4) if i == 0 else (_tools(0) if i == 12 else _tools()) for i in range(n)]
    assert _violation_times(imon(), seq) == [1.04], "response at 0.48 s is before the lower bound"
    seq = [_tools(4) if i == 0 else (_tools(0) if i == 13 else _tools()) for i in range(n)]
    assert _violation_times(imon(), seq) == [], "response at 0.52 s is inside [0.5, 1.0]"

    # CoActivation: primary active without required -> violation; with it -> safe.
    ca = CoActivationMonitor("c", "", 5, 0, 0.9, use_rtamt=use_rtamt)
    assert ca.step(0.0, _tools())["frame_status"] == "SAFE"
    assert ca.step(0.04, _tools(0))["frame_status"] == "SAFE"          # required alone is fine
    r = ca.step(0.08, _tools(5, 0))
    assert r["frame_status"] == "SAFE" and abs(r["robustness"] - 0.1) < 1e-9, r
    assert ca.step(0.12, _tools(5))["frame_status"] == "VIOLATION"
    assert ca.first_violation_time == 0.12

    # MutEx: both tools active -> negative robustness; otherwise positive.
    mx = MutExMonitor("m", "", 1, 4, use_rtamt=use_rtamt)
    assert mx.step(0.0, _tools(1))["frame_status"] == "SAFE"
    assert mx.step(0.04, _tools(1, 4))["frame_status"] == "VIOLATION"
    assert abs(mx.step(0.08, _tools(1, 4))["robustness"] + 0.5) < 1e-9
    print(f"selftest OK (MutEx backend: {mx.backend})", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--rules", default=DEFAULT_RULES_FILE, help="mined constraints JSON")
    parser.add_argument("--no-rtamt", action="store_true", help="use native MutEx evaluation")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--dedupe", choices=["max", "all", "min", "interval"], default="max",
                        help="repeated Response rules: max = [0, loosest deadline] (default), all / min deadline, or interval [min, max]")
    args = parser.parse_args()

    if args.selftest:
        run_selftest(use_rtamt=not args.no_rtamt)
        return
    serve(build_monitors(args.rules, use_rtamt=not args.no_rtamt, dedupe=args.dedupe))


if __name__ == "__main__":
    main()
