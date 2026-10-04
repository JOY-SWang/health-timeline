#!/usr/bin/env python3
"""Validate a health-timeline database or an incoming batch.

Usage:
  validate_record.py --db PATH                  validate the whole database
  validate_record.py --batch FILE [--db PATH]   validate a batch before appending
  add --json for machine-readable output

PATH may be the timeline.json file or the data directory that contains it.
Exit code: 0 = no errors (warnings allowed), 1 = errors found, 2 = usage / IO error.

The checks encode the skill's core contract (see references/schema.md):
  * facts carry an attributable, non-AI source and at least one evidence reference;
  * anything an AI concluded lives only in `analysis` (or in an ai_analysis event);
  * history is append-only: corrections link with `supersedes`, conflicts with
    `conflicts_with`, and every status change is in the changelog.
"""
import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

SCHEMA_VERSION = "1.0"

EVENT_TYPES = {
    "symptom", "exam_finding", "vital_sign", "lab_result", "imaging", "assessment", "diagnosis", "medication",
    "procedure", "encounter", "plan", "history", "lifestyle", "ai_analysis", "note",
}
SOURCE_TYPES = {
    "patient_report",       # the subject describing their own experience
    "caregiver_report",     # the user describing someone else (family member etc.)
    "clinician_relayed",    # a clinician's words, relayed second-hand by the user
    "clinician_document",   # written by a clinician: 病历, discharge summary, referral, prescription
    "lab_report",           # laboratory result document
    "imaging_report",       # radiology / imaging report document
    "device_measurement",   # home device / wearable reading
    "ai_statement",         # the AI assistant in the conversation
    "other_document",       # pharmacy label, insurance form, etc.
    "other",
}
DEFAULT_SOURCE_LABELS = {
    "patient_report": "Patient report",
    "caregiver_report": "Caregiver report",
    "clinician_relayed": "Doctor (relayed by user)",
    "clinician_document": "Clinical document",
    "lab_report": "Lab report",
    "imaging_report": "Imaging report",
    "device_measurement": "Device measurement",
    "ai_statement": "AI (chat assistant)",
    "other_document": "Document",
    "other": "Other",
}
STATUSES = {"active", "superseded", "retracted", "disputed", "needs_review"}
INITIAL_STATUSES = {"active", "needs_review"}
CONFIDENCE = {"high", "medium", "low"}
DATE_PRECISION = {"day", "month", "year", "approximate", "unknown"}
ANALYSIS_BY = {"ai_in_chat", "recorder"}
EVIDENCE_KINDS = {"file", "chat"}
SOURCE_KINDS = {"image", "pdf", "document", "other"}
SPEAKERS = {"user", "assistant", "other"}

# Allowed manual status transitions (supersede / dispute are set by append_event.py itself).
ALLOWED_TRANSITIONS = {
    "active": {"retracted", "needs_review"},
    "needs_review": {"active", "retracted"},
    "disputed": {"active", "retracted", "needs_review"},
    "retracted": {"active"},
    "superseded": set(),
}

EVENT_ID_RE = re.compile(r"^EV-\d{6}$")
SOURCE_ID_RE = re.compile(r"^SRC-\d{4,}$")
RUN_ID_RE = re.compile(r"^RUN-\d{4,}$")
DATE_RE = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$")
SUBJECT_RE = re.compile(r"^[a-z0-9_\-]+$")

# Words that usually signal an inference. In a fact they are fine only when attributed
# ("医生考虑…", "用户认为可能…"); unattributed they suggest analysis leaked into the fact.
HEDGE_RE = re.compile(
    r"(可能|疑似|考虑为|倾向于|大概率|应该是|估计是|提示.{0,6}(病|症|炎|损伤)|"
    r"\blikely\b|\bprobabl|\bpossibl|\bsuggest|consistent with|\bmay be\b|\bmight\b)",
    re.I,
)
ATTRIBUTION_RE = re.compile(
    r"(用户|患者|本人|医生|医师|大夫|护士|报告|化验单|病历|处方|出院|诊断书|说|称|认为|自述|转述|记载|显示|"
    r"\buser\b|\bpatient\b|\bdoctor\b|\bclinician\b|\breport|\bper\b|\bsays?\b|\bsaid\b|\bstated\b)",
    re.I,
)


# ----------------------------------------------------------------------------- helpers

def resolve_db_path(path):
    """Accept a timeline.json path or a data directory; fall back to $HEALTH_TIMELINE_DB."""
    path = path or os.environ.get("HEALTH_TIMELINE_DB")
    if not path:
        return None
    path = os.path.expanduser(path)
    if os.path.isdir(path) or not path.lower().endswith(".json"):
        return os.path.join(path, "timeline.json")
    return path


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_partial_date(value):
    """Return (ok, granularity) for 'YYYY', 'YYYY-MM' or 'YYYY-MM-DD'."""
    m = DATE_RE.match(value or "")
    if not m:
        return False, None
    y, mo, d = m.group(1), m.group(2), m.group(3)
    try:
        if d:
            date(int(y), int(mo), int(d))
            return True, "day"
        if mo:
            if not 1 <= int(mo) <= 12:
                return False, None
            return True, "month"
        return True, "year"
    except ValueError:
        return False, None


def date_upper_bound(value):
    """Earliest date the partial date could refer to (used for 'is this in the future?')."""
    ok, gran = parse_partial_date(value)
    if not ok:
        return None
    parts = [int(p) for p in value.split("-")]
    if gran == "day":
        return date(*parts)
    if gran == "month":
        return date(parts[0], parts[1], 1)
    return date(parts[0], 1, 1)


def is_blank(v):
    return v is None or (isinstance(v, str) and not v.strip())


class Report:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def err(self, where, msg):
        self.errors.append(f"{where}: {msg}")

    def warn(self, where, msg):
        self.warnings.append(f"{where}: {msg}")

    @property
    def ok(self):
        return not self.errors

    def as_dict(self):
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings}

    def print(self, header=""):
        if header:
            print(header)
        for e in self.errors:
            print(f"  ERROR    {e}")
        for w in self.warnings:
            print(f"  WARNING  {w}")
        print(f"  -> {len(self.errors)} error(s), {len(self.warnings)} warning(s)")


# ----------------------------------------------------------------------------- event checks

def check_event(ev, where, rep, ctx, in_batch):
    """Validate one event. ctx = dict(event_ids, source_ids, subjects, batch_event_refs, batch_source_refs)."""
    if not isinstance(ev, dict):
        rep.err(where, "event must be an object")
        return

    if in_batch:
        if "event_id" in ev:
            rep.err(where, "batch events must not carry event_id (IDs are assigned by append_event.py); use temp_id")
        status = ev.get("status", "active")
        if status not in INITIAL_STATUSES:
            rep.err(where, f"initial status must be one of {sorted(INITIAL_STATUSES)} "
                           f"(superseded/disputed are set automatically via supersedes/conflicts_with)")
    else:
        if not EVENT_ID_RE.match(str(ev.get("event_id", ""))):
            rep.err(where, f"bad or missing event_id {ev.get('event_id')!r}")
        status = ev.get("status")
        if status not in STATUSES:
            rep.err(where, f"status {status!r} not in {sorted(STATUSES)}")
        for k in ("recorded_at", "run_id", "fingerprint"):
            if is_blank(ev.get(k)):
                rep.err(where, f"missing {k}")
        if not is_blank(ev.get("run_id")) and ev.get("run_id") not in ctx.get("run_ids", set()):
            rep.err(where, f"run_id {ev.get('run_id')} not found in runs")

    subj = ev.get("subject_id", "self")
    if subj not in ctx["subjects"]:
        rep.err(where, f"subject_id {subj!r} is not registered (add it under 'subjects' first)")

    et = ev.get("event_type")
    st = ev.get("source_type")
    if et not in EVENT_TYPES:
        rep.err(where, f"event_type {et!r} not in {sorted(EVENT_TYPES)}")
    if st not in SOURCE_TYPES:
        rep.err(where, f"source_type {st!r} not in {sorted(SOURCE_TYPES)}")

    # --- dates
    prec = ev.get("date_precision")
    edate = ev.get("event_date")
    if prec not in DATE_PRECISION:
        rep.err(where, f"date_precision {prec!r} not in {sorted(DATE_PRECISION)}")
    if prec == "unknown":
        if not is_blank(edate):
            rep.err(where, "date_precision 'unknown' requires event_date to be null")
    else:
        ok, gran = parse_partial_date(edate)
        if not ok:
            rep.err(where, f"event_date {edate!r} must be YYYY-MM-DD, YYYY-MM or YYYY")
        elif prec in ("day", "month", "year") and gran != prec:
            rep.err(where, f"event_date {edate!r} does not match date_precision {prec!r}")
        elif prec == "approximate" and is_blank(ev.get("date_note")):
            rep.warn(where, "approximate dates should explain themselves in date_note")
        if ok and et not in ("plan", "encounter", "medication"):
            lb = date_upper_bound(edate)
            if lb and lb > date.today() + timedelta(days=1):
                rep.warn(where, f"event_date {edate} is in the future for a {et} event")

    # --- confidence
    conf = ev.get("confidence")
    if conf not in CONFIDENCE:
        rep.err(where, f"confidence {conf!r} not in {sorted(CONFIDENCE)}")
    elif conf == "low" and status == "active":
        rep.warn(where, "low-confidence records are usually status 'needs_review' so the user checks them")

    # --- the fact / inference contract
    fact = ev.get("fact")
    analysis = ev.get("analysis") or []
    if not isinstance(analysis, list):
        rep.err(where, "analysis must be a list")
        analysis = []

    if st == "ai_statement" or et == "ai_analysis":
        if not (st == "ai_statement" and et == "ai_analysis"):
            rep.err(where, "AI-originated content must use event_type 'ai_analysis' AND source_type 'ai_statement' "
                           "(an AI statement is never a fact about the patient)")
        if not is_blank(fact):
            rep.err(where, "ai_analysis events must leave 'fact' empty; put the AI's statement in 'analysis'")
        if not analysis:
            rep.err(where, "ai_analysis events need at least one analysis item")
        if analysis and not any(isinstance(a, dict) and a.get("by") == "ai_in_chat" for a in analysis):
            rep.err(where, "an ai_analysis event must contain the assistant's statement as an 'ai_in_chat' analysis item "
                           "(recorder notes may be added alongside it)")
    else:
        if is_blank(fact):
            rep.err(where, "non-AI events need a non-empty 'fact'")
        elif HEDGE_RE.search(fact) and not ATTRIBUTION_RE.search(fact):
            rep.warn(where, f"fact contains inference-like wording without attribution: {fact[:60]!r} "
                            f"- attribute it ('医生考虑…', 'user thinks…') or move it to analysis")
        if et == "diagnosis" and st in ("patient_report", "caregiver_report", "device_measurement"):
            rep.warn(where, "a diagnosis sourced from the user is usually event_type 'history' "
                            "(e.g. '用户自述既往高血压') unless a clinician source is cited")

    for i, a in enumerate(analysis):
        aw = f"{where}.analysis[{i}]"
        if not isinstance(a, dict):
            rep.err(aw, "analysis item must be an object {text, by, basis?}")
            continue
        if is_blank(a.get("text")):
            rep.err(aw, "missing text")
        if a.get("by") not in ANALYSIS_BY:
            rep.err(aw, f"by {a.get('by')!r} not in {sorted(ANALYSIS_BY)}")
        for ref in a.get("basis", []) or []:
            check_event_ref(ref, aw + ".basis", rep, ctx, in_batch)

    # --- evidence
    evidence = ev.get("evidence") or []
    if not isinstance(evidence, list):
        rep.err(where, "evidence must be a list")
        evidence = []
    if not evidence:
        rep.err(where, "every event needs at least one evidence reference (file or chat quote)")
    for i, e in enumerate(evidence):
        ew = f"{where}.evidence[{i}]"
        if not isinstance(e, dict):
            rep.err(ew, "evidence item must be an object")
            continue
        kind = e.get("kind")
        if kind not in EVIDENCE_KINDS:
            rep.err(ew, f"kind {kind!r} not in {sorted(EVIDENCE_KINDS)}")
        elif kind == "file":
            sid = e.get("source_id")
            if is_blank(sid):
                rep.err(ew, "file evidence needs source_id")
            elif sid not in ctx["source_ids"] and sid not in ctx.get("batch_source_refs", set()):
                rep.err(ew, f"source_id {sid!r} not found among sources")
        elif kind == "chat":
            if is_blank(e.get("quote")):
                rep.err(ew, "chat evidence needs a verbatim 'quote'")
            if e.get("speaker") not in SPEAKERS:
                rep.err(ew, f"speaker {e.get('speaker')!r} not in {sorted(SPEAKERS)}")
            if st == "ai_statement" and e.get("speaker") not in ("assistant", None):
                rep.warn(ew, "ai_statement evidence should quote the assistant")

    # --- values (structured measurements)
    vals = ev.get("values")
    if vals is not None:
        if not isinstance(vals, dict):
            rep.err(where, "values must be an object {name, value, unit, ref_range?, flag?}")
        elif is_blank(vals.get("name")) or "value" not in vals:
            rep.err(where, "values needs at least name and value")
    if et == "lab_result":
        if not isinstance(vals, dict):
            rep.warn(where, "lab_result events should carry structured values {name, value, unit, ref_range, flag}")
        elif is_blank(vals.get("unit")) and not isinstance(vals.get("value"), str):
            rep.warn(where, "numeric lab value without a unit - record the unit, or note that the report omits it")

    # --- links
    for key in ("supersedes", "conflicts_with", "about"):
        refs = ev.get(key) or []
        if not isinstance(refs, list):
            rep.err(where, f"{key} must be a list")
            continue
        for ref in refs:
            check_event_ref(ref, f"{where}.{key}", rep, ctx, in_batch)
    if (ev.get("supersedes") or []) and is_blank(ev.get("correction_reason")):
        rep.err(where, "an event that supersedes another must state correction_reason")


def check_event_ref(ref, where, rep, ctx, in_batch):
    if ref in ctx["event_ids"]:
        return
    if in_batch and ref in ctx.get("batch_event_refs", set()):
        return
    rep.err(where, f"reference {ref!r} does not match any event")


def check_source(src, where, rep, in_batch):
    if not isinstance(src, dict):
        rep.err(where, "source must be an object")
        return
    if in_batch:
        if is_blank(src.get("temp_id")):
            rep.err(where, "batch sources need a temp_id (e.g. 'S1') that events reference as source_id")
        elif SOURCE_ID_RE.match(src["temp_id"]):
            rep.err(where, "temp_id must not look like a real SRC- id")
        fp = src.get("file_path")
        if not is_blank(fp) and not os.path.exists(os.path.expanduser(fp)):
            rep.warn(where, f"file_path {fp!r} does not exist here - the source will be registered without an archived copy")
    else:
        if not SOURCE_ID_RE.match(str(src.get("source_id", ""))):
            rep.err(where, f"bad or missing source_id {src.get('source_id')!r}")
    if src.get("kind") not in SOURCE_KINDS:
        rep.err(where, f"kind {src.get('kind')!r} not in {sorted(SOURCE_KINDS)}")
    if is_blank(src.get("filename")) and is_blank(src.get("title")):
        rep.err(where, "a source needs a filename or a title so humans can find it")


# ----------------------------------------------------------------------------- db / batch

def subjects_of(db):
    return {s.get("subject_id") for s in (db or {}).get("subjects", []) if isinstance(s, dict)}


def validate_db(db):
    rep = Report()
    if not isinstance(db, dict):
        rep.err("db", "root must be an object")
        return rep
    for key in ("schema_version", "subjects", "sources", "events", "runs", "changelog"):
        if key not in db:
            rep.err("db", f"missing top-level key {key!r}")
    if rep.errors:
        return rep
    if db["schema_version"] != SCHEMA_VERSION:
        rep.warn("db", f"schema_version {db['schema_version']} (validator expects {SCHEMA_VERSION})")

    subjects = set()
    for i, s in enumerate(db["subjects"]):
        sid = s.get("subject_id") if isinstance(s, dict) else None
        if not sid or not SUBJECT_RE.match(sid):
            rep.err(f"subjects[{i}]", f"subject_id {sid!r} must be lowercase letters/digits/_/-")
        elif sid in subjects:
            rep.err(f"subjects[{i}]", f"duplicate subject_id {sid}")
        subjects.add(sid)

    source_ids = set()
    for i, s in enumerate(db["sources"]):
        check_source(s, f"sources[{i}]", rep, in_batch=False)
        sid = s.get("source_id") if isinstance(s, dict) else None
        if sid in source_ids:
            rep.err(f"sources[{i}]", f"duplicate source_id {sid}")
        source_ids.add(sid)

    run_ids = {r.get("run_id") for r in db["runs"] if isinstance(r, dict)}
    for i, r in enumerate(db["runs"]):
        if not RUN_ID_RE.match(str(r.get("run_id", ""))):
            rep.err(f"runs[{i}]", f"bad run_id {r.get('run_id')!r}")

    events = db["events"]
    by_id = {}
    for i, ev in enumerate(events):
        eid = ev.get("event_id") if isinstance(ev, dict) else None
        if eid in by_id:
            rep.err(f"events[{i}]", f"duplicate event_id {eid}")
        by_id[eid] = ev

    ctx = {"event_ids": set(by_id), "source_ids": source_ids, "subjects": subjects, "run_ids": run_ids}
    fingerprints = {}
    for i, ev in enumerate(events):
        where = f"events[{i}]({ev.get('event_id', '?')})"
        check_event(ev, where, rep, ctx, in_batch=False)
        fp = ev.get("fingerprint")
        if fp and fp in fingerprints and not ev.get("allow_duplicate"):
            rep.warn(where, f"same fingerprint as {fingerprints[fp]} (possible duplicate)")
        fingerprints.setdefault(fp, ev.get("event_id"))

        # supersession graph consistency
        if ev.get("status") == "superseded":
            nxt = ev.get("superseded_by")
            if not nxt or nxt not in by_id:
                rep.err(where, "status 'superseded' needs superseded_by pointing at an existing event")
            elif ev.get("event_id") not in (by_id[nxt].get("supersedes") or []):
                rep.err(where, f"{nxt} does not list {ev.get('event_id')} in its supersedes")
        elif ev.get("superseded_by"):
            rep.err(where, "superseded_by is set but status is not 'superseded'")
        for old in ev.get("supersedes") or []:
            if old in by_id and by_id[old].get("superseded_by") != ev.get("event_id"):
                rep.err(where, f"supersedes {old} but {old}.superseded_by is {by_id[old].get('superseded_by')!r}")
        if ev.get("status") == "disputed" and not ev.get("conflicts_with"):
            rep.err(where, "status 'disputed' needs conflicts_with")
        for other in ev.get("conflicts_with") or []:
            if other in by_id and ev.get("event_id") not in (by_id[other].get("conflicts_with") or []):
                rep.warn(where, f"conflict with {other} is not reciprocal")

    # cycles in supersession
    for eid in by_id:
        seen, cur = set(), eid
        while cur and cur in by_id:
            if cur in seen:
                rep.err(f"event {eid}", "supersession cycle detected")
                break
            seen.add(cur)
            cur = by_id[cur].get("superseded_by")

    for i, c in enumerate(db["changelog"]):
        if not isinstance(c, dict) or c.get("event_id") not in by_id:
            rep.err(f"changelog[{i}]", "entry must reference an existing event_id")
        elif is_blank(c.get("reason")):
            rep.err(f"changelog[{i}]", "status changes need a reason")
    return rep


def validate_batch(batch, db=None):
    rep = Report()
    if not isinstance(batch, dict):
        rep.err("batch", "root must be an object with keys run, events, sources?, subjects?, status_changes?")
        return rep
    db = db or {"subjects": [{"subject_id": "self"}], "sources": [], "events": []}
    if not isinstance(batch.get("run"), dict):
        rep.err("batch.run", "missing run block {scope, conversation_hint, last_message_anchor}")
    else:
        for k in ("scope", "last_message_anchor"):
            if is_blank(batch["run"].get(k)):
                rep.warn("batch.run", f"missing {k} - the next run uses it to find where to resume")

    subjects = subjects_of(db)
    for i, s in enumerate(batch.get("subjects", []) or []):
        sid = s.get("subject_id") if isinstance(s, dict) else None
        if not sid or not SUBJECT_RE.match(sid):
            rep.err(f"batch.subjects[{i}]", f"subject_id {sid!r} must be lowercase letters/digits/_/-")
        else:
            subjects.add(sid)

    batch_sources = batch.get("sources", []) or []
    for i, s in enumerate(batch_sources):
        check_source(s, f"batch.sources[{i}]", rep, in_batch=True)
    temp_src = [s.get("temp_id") for s in batch_sources if isinstance(s, dict)]
    if len(temp_src) != len(set(temp_src)):
        rep.err("batch.sources", "duplicate temp_id")

    events = batch.get("events", []) or []
    temp_ev = [e.get("temp_id") for e in events if isinstance(e, dict) and e.get("temp_id")]
    if len(temp_ev) != len(set(temp_ev)):
        rep.err("batch.events", "duplicate temp_id")
    ctx = {
        "event_ids": {e.get("event_id") for e in db.get("events", [])},
        "source_ids": {s.get("source_id") for s in db.get("sources", [])},
        "subjects": subjects,
        "batch_event_refs": set(temp_ev),
        "batch_source_refs": set(temp_src),
    }
    db_events = {e.get("event_id"): e for e in db.get("events", [])}
    for i, ev in enumerate(events):
        where = f"batch.events[{i}]" + (f"({ev.get('temp_id')})" if isinstance(ev, dict) and ev.get("temp_id") else "")
        check_event(ev, where, rep, ctx, in_batch=True)
        for old in (ev.get("supersedes") or []) if isinstance(ev, dict) else []:
            if old in db_events and db_events[old].get("status") == "superseded":
                rep.err(where, f"{old} is already superseded by {db_events[old].get('superseded_by')} - supersede that one instead")

    for i, ch in enumerate(batch.get("status_changes", []) or []):
        w = f"batch.status_changes[{i}]"
        if not isinstance(ch, dict):
            rep.err(w, "must be an object {event_id, new_status, reason}")
            continue
        eid, new = ch.get("event_id"), ch.get("new_status")
        if eid not in db_events:
            rep.err(w, f"event_id {eid!r} not in database")
            continue
        cur = db_events[eid].get("status")
        if new not in ALLOWED_TRANSITIONS.get(cur, set()):
            rep.err(w, f"cannot change {eid} from {cur!r} to {new!r} "
                       f"(allowed: {sorted(ALLOWED_TRANSITIONS.get(cur, set())) or 'none'}; "
                       f"corrections of content use a new event with supersedes)")
        if is_blank(ch.get("reason")):
            rep.err(w, "a reason is required")

    if not events and not batch.get("status_changes"):
        rep.warn("batch", "nothing to append (no events, no status changes)")
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", help="timeline.json or its data directory (default: $HEALTH_TIMELINE_DB)")
    ap.add_argument("--batch", help="batch JSON to validate against the database")
    ap.add_argument("--json", action="store_true", help="print machine-readable result")
    args = ap.parse_args(argv)

    db_path = resolve_db_path(args.db)
    db = None
    if db_path and os.path.exists(db_path):
        try:
            db = load_json(db_path)
        except (OSError, json.JSONDecodeError) as e:
            print(f"cannot read database {db_path}: {e}", file=sys.stderr)
            return 2

    if args.batch:
        try:
            batch = load_json(args.batch)
        except (OSError, json.JSONDecodeError) as e:
            print(f"cannot read batch {args.batch}: {e}", file=sys.stderr)
            return 2
        rep = validate_batch(batch, db)
        header = f"Batch {args.batch}" + (f" against {db_path}" if db else " (no database found - checked standalone)")
    else:
        if not db:
            print("nothing to validate: pass --db PATH (existing timeline.json) or --batch FILE", file=sys.stderr)
            return 2
        rep = validate_db(db)
        header = f"Database {db_path}: {len(db.get('events', []))} events, {len(db.get('sources', []))} sources"

    if args.json:
        print(json.dumps(rep.as_dict(), ensure_ascii=False, indent=2))
    else:
        rep.print(header)
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
