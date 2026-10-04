#!/usr/bin/env python3
"""Append extracted health events to the canonical JSON timeline (and refresh the Excel export).

Usage:
  append_event.py --db DIR --init [--self-name 本人]      create an empty database
  append_event.py --db DIR --batch batch.json [--dry-run] [--no-export]
  append_event.py --db DIR --list [--subject S] [--type T] [--status S] [--since YYYY-MM-DD]
                                  [--search TEXT] [--limit N] [--full]
  append_event.py --db DIR --last-run                     show the latest run (resume point)
  append_event.py --db DIR --retract EV-000012 --reason "…"   quick retraction without a batch

DIR is the data directory (or its timeline.json); defaults to $HEALTH_TIMELINE_DB.
Data directory layout:
  timeline.json   canonical database (never edit by hand; always go through this script)
  timeline.xlsx   human-readable export, regenerated after every write
  evidence/       archived copies of images / PDFs, named SRC-xxxx_<original name>
  backups/        snapshot of timeline.json taken before every write (last 30 kept)

The batch format is documented in references/schema.md. In short: the batch never
contains real IDs for new things - sources and events get a temp_id, and other
fields may reference either temp_ids or existing IDs; this script assigns the real
IDs, archives files, de-duplicates, applies supersedes / conflicts / status changes,
validates the result and writes atomically.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate_record import (  # noqa: E402
    DEFAULT_SOURCE_LABELS, SCHEMA_VERSION, resolve_db_path, validate_batch, validate_db,
)

EVENT_KEY_ORDER = [
    "event_id", "subject_id", "event_date", "date_precision", "date_note", "event_type",
    "fact", "verbatim", "values", "panel", "source_type", "source_label", "evidence",
    "analysis", "about", "status", "confidence", "confidence_note", "supersedes",
    "superseded_by", "correction_reason", "conflicts_with", "recorded_at", "run_id",
    "fingerprint", "allow_duplicate", "notes",
]
MAX_BACKUPS = 30
CHECKPOINT_TAG = "[health-timeline checkpoint]"


# ----------------------------------------------------------------------------- utils

def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def die(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)


def load_db(db_path):
    if not os.path.exists(db_path):
        die(f"No database at {db_path}. Create one with --init (or pass the right --db).", 2)
    with open(db_path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_db(db, db_path):
    data_dir = os.path.dirname(db_path)
    os.makedirs(os.path.join(data_dir, "backups"), exist_ok=True)
    if os.path.exists(db_path):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        shutil.copy2(db_path, os.path.join(data_dir, "backups", f"timeline.{stamp}.json"))
        backups = sorted(p for p in os.listdir(os.path.join(data_dir, "backups")) if p.startswith("timeline."))
        for old in backups[:-MAX_BACKUPS]:
            os.remove(os.path.join(data_dir, "backups", old))
    tmp = db_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, db_path)


def norm_text(s):
    return re.sub(r"[\s\W_]+", "", (s or "").lower())


def fingerprint(ev):
    body = ev.get("fact") or " ".join(a.get("text", "") for a in ev.get("analysis") or [] if isinstance(a, dict))
    key = "|".join([
        ev.get("subject_id", "self"), ev.get("event_date") or "", ev.get("event_type", ""),
        ev.get("source_type", ""), norm_text(body),
    ])
    return "sha256:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def next_id(prefix, existing_ids, width):
    nums = [int(i.split("-")[1]) for i in existing_ids if isinstance(i, str) and i.startswith(prefix + "-")]
    n = max(nums, default=0)
    while True:
        n += 1
        yield f"{prefix}-{n:0{width}d}"


def safe_name(name):
    name = os.path.basename(name or "file")
    name = re.sub(r"[^\w.\-]+", "_", name, flags=re.UNICODE).strip("._") or "file"
    return name[:80]


def ordered_event(ev):
    out = {k: ev[k] for k in EVENT_KEY_ORDER if k in ev}
    out.update({k: v for k, v in ev.items() if k not in out})  # keep unknown keys rather than lose data
    return out


def short(text, n=70):
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def event_summary_text(ev):
    if ev.get("fact"):
        return ev["fact"]
    return " / ".join(a.get("text", "") for a in ev.get("analysis") or [])


# ----------------------------------------------------------------------------- commands

def cmd_init(db_path, self_name):
    if os.path.exists(db_path):
        die(f"Database already exists at {db_path} - not overwriting.", 1)
    data_dir = os.path.dirname(db_path)
    for sub in ("", "evidence", "backups"):
        os.makedirs(os.path.join(data_dir, sub), exist_ok=True)
    ts = now_iso()
    db = {
        "schema_version": SCHEMA_VERSION,
        "created_at": ts,
        "updated_at": ts,
        "subjects": [{"subject_id": "self", "display_name": self_name, "notes": ""}],
        "sources": [],
        "events": [],
        "runs": [],
        "changelog": [],
    }
    write_db(db, db_path)
    print(f"Created empty health timeline at {db_path}")
    return 0


def apply_batch(db, batch, data_dir, dry_run=False):
    """Mutates db in place (callers pass a copy for dry runs). Returns a summary dict."""
    ts = now_iso()
    run_id = next(next_id("RUN", [r["run_id"] for r in db["runs"]], 4))
    summary = {"run_id": run_id, "subjects_added": [], "sources_added": [], "sources_reused": [],
               "events_added": [], "duplicates_skipped": [], "status_changes": [], "warnings": []}

    # --- subjects
    known_subjects = {s["subject_id"] for s in db["subjects"]}
    for s in batch.get("subjects", []) or []:
        if s["subject_id"] in known_subjects:
            continue
        db["subjects"].append({"subject_id": s["subject_id"], "display_name": s.get("display_name", s["subject_id"]),
                               "notes": s.get("notes", "")})
        known_subjects.add(s["subject_id"])
        summary["subjects_added"].append(s["subject_id"])

    # --- sources (archive files, de-duplicate by content hash)
    src_map = {}
    by_hash = {s.get("sha256"): s["source_id"] for s in db["sources"] if s.get("sha256")}
    src_ids = next_id("SRC", [s["source_id"] for s in db["sources"]], 4)
    for s in batch.get("sources", []) or []:
        fp = os.path.expanduser(s.get("file_path") or "")
        digest = sha256_file(fp) if fp and os.path.isfile(fp) else None
        if digest and digest in by_hash:
            src_map[s["temp_id"]] = by_hash[digest]
            summary["sources_reused"].append(by_hash[digest])
            continue
        sid = next(src_ids)
        stored = None
        if digest:
            stored = f"evidence/{sid}_{safe_name(s.get('filename') or fp)}"
            if not dry_run:
                os.makedirs(os.path.join(data_dir, "evidence"), exist_ok=True)
                dest = os.path.join(data_dir, stored)
                shutil.copyfile(fp, dest)  # content only: don't inherit read-only bits from the upload
                os.chmod(dest, 0o644)
        elif fp:
            summary["warnings"].append(f"{s['temp_id']}: file {fp} not found - registered without archived copy")
        rec = {
            "source_id": sid,
            "kind": s.get("kind"),
            "title": s.get("title", ""),
            "filename": s.get("filename") or (os.path.basename(fp) if fp else ""),
            "stored_path": stored,
            "sha256": digest,
            "pages": s.get("pages"),
            "origin": s.get("origin", ""),
            "document_date": s.get("document_date"),
            "issuer": s.get("issuer", ""),
            "notes": s.get("notes", ""),
            "added_at": ts,
            "run_id": run_id,
        }
        db["sources"].append({k: v for k, v in rec.items() if v not in (None, "")})
        if digest:
            by_hash[digest] = sid
        src_map[s["temp_id"]] = sid
        summary["sources_added"].append(sid)

    # --- events: pass 1 assign ids + dedupe
    existing_fp = {e.get("fingerprint"): e["event_id"] for e in db["events"]}
    ev_ids = next_id("EV", [e["event_id"] for e in db["events"]], 6)
    ev_map, new_events = {}, []
    for raw in batch.get("events", []) or []:
        ev = dict(raw)
        ev.setdefault("subject_id", "self")
        ev.setdefault("status", "active")
        fpr = fingerprint(ev)
        if fpr in existing_fp and not ev.get("allow_duplicate"):
            if ev.get("temp_id"):
                ev_map[ev["temp_id"]] = existing_fp[fpr]
            summary["duplicates_skipped"].append({"temp_id": ev.get("temp_id"), "same_as": existing_fp[fpr],
                                                  "text": short(event_summary_text(ev))})
            continue
        eid = next(ev_ids)
        if ev.get("temp_id"):
            ev_map[ev["temp_id"]] = eid
        ev.pop("temp_id", None)
        ev.update({"event_id": eid, "fingerprint": fpr, "recorded_at": ts, "run_id": run_id})
        ev.setdefault("superseded_by", None)
        if not ev.get("source_label"):
            ev["source_label"] = DEFAULT_SOURCE_LABELS.get(ev.get("source_type"), "")
        existing_fp[fpr] = eid
        new_events.append(ev)

    # --- pass 2 resolve references
    def r_ev(x):
        return ev_map.get(x, x)

    for ev in new_events:
        for key in ("supersedes", "conflicts_with", "about"):
            if ev.get(key):
                ev[key] = [r_ev(x) for x in ev[key]]
        for a in ev.get("analysis") or []:
            if a.get("basis"):
                a["basis"] = [r_ev(x) for x in a["basis"]]
        for e in ev.get("evidence") or []:
            if e.get("kind") == "file":
                e["source_id"] = src_map.get(e.get("source_id"), e.get("source_id"))

    db["events"].extend(ordered_event(e) for e in new_events)
    by_id = {e["event_id"]: e for e in db["events"]}
    new_events = [by_id[e["event_id"]] for e in new_events]  # the stored copies from here on

    def log_change(eid, old, new, reason):
        db["changelog"].append({"at": ts, "run_id": run_id, "event_id": eid, "from": old, "to": new, "reason": reason})
        summary["status_changes"].append(f"{eid}: {old} -> {new}")

    # --- supersedes / conflicts
    for ev in new_events:
        ev.setdefault("supersedes", [])
        for old_id in ev.get("supersedes") or []:
            old = by_id[old_id]
            prev = old.get("status")
            old["status"], old["superseded_by"] = "superseded", ev["event_id"]
            log_change(old_id, prev, "superseded", f"superseded by {ev['event_id']}: {ev.get('correction_reason', '')}")
        for other_id in ev.get("conflicts_with") or []:
            other = by_id[other_id]
            other.setdefault("conflicts_with", [])
            if ev["event_id"] not in other["conflicts_with"]:
                other["conflicts_with"].append(ev["event_id"])
            if other.get("status") in ("active", "needs_review"):
                prev = other["status"]
                other["status"] = "disputed"
                log_change(other_id, prev, "disputed", f"conflicts with {ev['event_id']}")
            ev["status"] = "disputed"

    # --- explicit status changes
    for ch in batch.get("status_changes", []) or []:
        ev = by_id[ch["event_id"]]
        prev = ev.get("status")
        ev["status"] = ch["new_status"]
        log_change(ch["event_id"], prev, ch["new_status"], ch["reason"])

    # --- run record
    run = dict(batch.get("run") or {})
    run.update({
        "run_id": run_id,
        "run_at": ts,
        "events_added": [e["event_id"] for e in new_events],
        "sources_added": summary["sources_added"],
        "status_changes": len(summary["status_changes"]),
        "duplicates_skipped": len(summary["duplicates_skipped"]),
    })
    db["runs"].append(run)
    db["updated_at"] = ts
    summary["run"] = run
    summary["new_events"] = new_events
    return summary


def checkpoint_line(summary):
    run = summary["run"]
    ids = run["events_added"]
    span = f"{ids[0]}..{ids[-1]}" if len(ids) > 1 else (ids[0] if ids else "none")
    anchor = short(run.get("last_message_anchor", ""), 40)
    when = run["run_at"][:16].replace("T", " ")
    line = (f"{CHECKPOINT_TAG} {run['run_id']} | {when} | +{len(ids)} events ({span}) | "
            f"{run['status_changes']} status changes")
    return line + (f" | last: \"{anchor}\"" if anchor else "")


def cmd_batch(db_path, batch_path, dry_run, no_export):
    db = load_db(db_path)
    with open(batch_path, "r", encoding="utf-8") as f:
        batch = json.load(f)

    pre = validate_batch(batch, db)
    if not pre.ok:
        pre.print(f"Batch rejected ({batch_path}) - fix these and re-run:")
        return 1
    if pre.warnings:
        pre.print("Batch warnings (not blocking):")

    work = json.loads(json.dumps(db))  # deep copy; only replace the original after the result validates
    summary = apply_batch(work, batch, os.path.dirname(db_path), dry_run=dry_run)
    post = validate_db(work)
    if not post.ok:
        post.print("Resulting database would be invalid - nothing written:")
        return 1

    print(("DRY RUN - nothing written. " if dry_run else "") + f"{summary['run_id']}:")
    for ev in summary["new_events"]:
        print(f"  + {ev['event_id']}  {ev.get('event_date') or '????-??-??':10}  {ev.get('subject_id'):8} "
              f"{ev.get('event_type'):12} {ev.get('status'):12} {short(event_summary_text(ev), 60)}  "
              f"[{ev.get('source_label')}]")
    for d in summary["duplicates_skipped"]:
        print(f"  = skipped duplicate of {d['same_as']}: {d['text']}")
    for s in summary["status_changes"]:
        print(f"  ~ {s}")
    for s in summary["subjects_added"]:
        print(f"  + subject {s}")
    for s in summary["sources_added"]:
        print(f"  + source {s}")
    for s in summary["sources_reused"]:
        print(f"  = reused existing source {s} (same file hash)")
    for w in summary["warnings"]:
        print(f"  ! {w}")

    if dry_run:
        return 0
    write_db(work, db_path)
    print(f"Saved {db_path}")
    if not no_export:
        try:
            from export_excel import export_xlsx
            out = export_xlsx(work, os.path.join(os.path.dirname(db_path), "timeline.xlsx"))
            print(f"Exported {out}")
        except ImportError as e:
            print(f"! Excel export skipped ({e}). Install openpyxl or run export_excel.py --csv.")
    print()
    print(checkpoint_line(summary))
    return 0


def cmd_list(db_path, args):
    db = load_db(db_path)
    evs = db["events"]
    if args.subject:
        evs = [e for e in evs if e.get("subject_id") == args.subject]
    if args.type:
        evs = [e for e in evs if e.get("event_type") == args.type]
    if args.status:
        evs = [e for e in evs if e.get("status") == args.status]
    if args.since:
        evs = [e for e in evs if (e.get("event_date") or "") >= args.since]
    if args.search:
        q = args.search.lower()
        evs = [e for e in evs if q in json.dumps(e, ensure_ascii=False).lower()]
    evs = sorted(evs, key=lambda e: ((e.get("event_date") or "9999"), e["event_id"]))
    if args.limit:
        evs = evs[-args.limit:]
    if args.full:
        print(json.dumps(evs, ensure_ascii=False, indent=2))
        return 0
    print(f"{len(evs)} event(s)")
    for e in evs:
        links = []
        if e.get("superseded_by"):
            links.append(f"->{e['superseded_by']}")
        if e.get("conflicts_with"):
            links.append("<>" + ",".join(e["conflicts_with"]))
        print(f"{e['event_id']}  {e.get('event_date') or '?':10}  {e.get('subject_id'):8} {e.get('event_type'):12} "
              f"{e.get('status'):12} {short(event_summary_text(e), 70)}  [{e.get('source_label', '')}] {' '.join(links)}")
    return 0


def cmd_last_run(db_path):
    db = load_db(db_path)
    if not db["runs"]:
        print("No runs yet - this database is empty; scan the whole conversation.")
        return 0
    last = dict(db["runs"][-1])
    # manual retractions have no anchor; point at the latest run that recorded where it stopped
    anchored = [r for r in db["runs"] if (r.get("last_message_anchor") or "").strip()]
    if anchored:
        last["resume_after"] = {"run_id": anchored[-1]["run_id"], "run_at": anchored[-1].get("run_at"),
                                "last_message_anchor": anchored[-1]["last_message_anchor"],
                                "conversation_hint": anchored[-1].get("conversation_hint", "")}
    print(json.dumps(last, ensure_ascii=False, indent=2))
    return 0


def cmd_retract(db_path, event_id, reason, no_export):
    if not reason:
        die("--retract needs --reason", 2)
    batch = {"run": {"scope": "manual retraction", "last_message_anchor": "", "notes": reason},
             "events": [], "status_changes": [{"event_id": event_id, "new_status": "retracted", "reason": reason}]}
    tmp = db_path + ".retract.json"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(batch, f, ensure_ascii=False)
    try:
        return cmd_batch(db_path, tmp, dry_run=False, no_export=no_export)
    finally:
        os.remove(tmp)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", help="data directory or timeline.json (default: $HEALTH_TIMELINE_DB)")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--init", action="store_true")
    mode.add_argument("--batch")
    mode.add_argument("--list", action="store_true")
    mode.add_argument("--last-run", action="store_true")
    mode.add_argument("--retract", metavar="EVENT_ID")
    ap.add_argument("--self-name", default="本人", help="display name for subject 'self' (with --init)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-export", action="store_true", help="skip regenerating timeline.xlsx")
    ap.add_argument("--reason")
    ap.add_argument("--subject")
    ap.add_argument("--type")
    ap.add_argument("--status")
    ap.add_argument("--since")
    ap.add_argument("--search")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--full", action="store_true", help="--list prints full JSON events")
    args = ap.parse_args(argv)

    db_path = resolve_db_path(args.db)
    if not db_path:
        die("Where is the timeline? Pass --db DIR or set HEALTH_TIMELINE_DB.", 2)

    if args.init:
        return cmd_init(db_path, args.self_name)
    if args.batch:
        return cmd_batch(db_path, args.batch, args.dry_run, args.no_export)
    if args.list:
        return cmd_list(db_path, args)
    if args.last_run:
        return cmd_last_run(db_path)
    if args.retract:
        return cmd_retract(db_path, args.retract, args.reason, args.no_export)
    return 2


if __name__ == "__main__":
    sys.exit(main())
