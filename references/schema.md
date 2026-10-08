# health-timeline data schema (v1.0)

Contents: 1. Data directory · 2. Database (`timeline.json`) · 3. Event fields · 4. Enums ·
5. Sources · 6. Runs and changelog · 7. Batch format (what you write each run) · 8. Lifecycle rules

## 1. Data directory

```
<data_dir>/
├── timeline.json   canonical database — only append_event.py writes it
├── timeline.xlsx   regenerated view (Timeline, Lab trends, Evidence, Log, Guide)
├── evidence/       archived originals: SRC-0003_lab_report.pdf, SRC-0004_IMG_0012.jpg …
└── backups/        timeline.<timestamp>.json before every write (last 30 kept)
```

## 2. Database

```json
{
  "schema_version": "1.0",
  "created_at": "2026-10-03T15:02:11+08:00",
  "updated_at": "2026-10-03T15:20:40+08:00",
  "subjects":  [{"subject_id": "self", "display_name": "本人", "notes": ""}],
  "sources":   [ ...source records... ],
  "events":    [ ...event records... ],
  "runs":      [ ...one per ingestion... ],
  "changelog": [ ...one per status change... ]
}
```

## 3. Event fields

| field | required | meaning |
|---|---|---|
| `event_id` | auto | `EV-000001`, assigned by append_event.py |
| `subject_id` | yes (default `self`) | whose record this is; must be registered in `subjects` |
| `event_date` | yes* | clinical date: `YYYY-MM-DD`, `YYYY-MM`, `YYYY`; `null` only when `date_precision` is `unknown` |
| `date_precision` | yes | `day` · `month` · `year` · `approximate` · `unknown` |
| `date_note` | when not obvious | how the date was derived ("用户 10-03 说'昨天开始'"; "采样时间; 报告日期 10-04") |
| `event_type` | yes | see enums |
| `fact` | yes, except ai_analysis | one attributed, atomic statement of what the source asserted |
| `verbatim` | recommended | the original wording / printed text, untranslated |
| `values` | labs & vitals | `{"name","value","unit","ref_range","flag"}` exactly as printed; `value` is a number when the source gives a plain number, otherwise a string ("135/88", "阴性", "<0.5") |
| `measurement_id` | optional, device only | `sha256:` plus 64 lowercase hex digits: an exact raw-measurement identity included in deduplication. Apple Health hashes type, source name, original value/unit and original start/end timestamps. Events without this field retain their existing fingerprints. |
| `panel` | optional | groups atomic results from one test sheet, e.g. `"血常规+CRP 2026-10-03"` |
| `source_type` | yes | who/what asserted the fact (see enums) |
| `source_label` | auto-filled | human label for the Excel Source column; default from source_type; override for specificity ("Lab report (仁济医院)") |
| `evidence` | yes, ≥1 | `[{"kind":"file","source_id":"SRC-0003","locator":"p.2, row 7"}]` or `[{"kind":"chat","speaker":"user","quote":"昨天开始右膝疼，大概6分","said_on":"2026-10-03"}]` |
| `analysis` | optional | `[{"text": "...", "by": "ai_in_chat" \| "recorder", "basis": ["EV-…"]}]` — the only place AI-generated content may live |
| `about` | optional | event ids this event comments on (ai_analysis, plan, follow-ups) |
| `status` | yes | see lifecycle rules |
| `confidence` | yes | fidelity of the record to its source: `high` · `medium` · `low` |
| `confidence_note` | when not high | why ("handwritten digit could be 1 or 7") |
| `supersedes` | optional | ids this event corrects/replaces; requires `correction_reason` |
| `superseded_by` | auto | set on the old event when a newer event supersedes it |
| `correction_reason` | with supersedes | e.g. "用户更正：剂量是 200mg 不是 400mg" |
| `conflicts_with` | optional | ids of records that disagree with this one; both become `disputed` |
| `recorded_at`, `run_id`, `fingerprint` | auto | provenance and de-duplication |
| `allow_duplicate` | rare | set true when an identical fact legitimately repeats (same symptom report twice on one day from different occasions) |
| `notes` | optional | anything a human should know that is neither fact nor analysis |

### Why `confidence` is about fidelity, not truth

A perfectly transcribed patient recollection ("我记得 CRP 是 12") is a *high-confidence record* of a
*lower-authority source*. Authority is already captured by `source_type`; mixing the two into one
number is how errors in the base layer become invisible. So:

- `high` — typed text or clearly legible print, copied exactly.
- `medium` — some interpretation was needed: OCR of a photo, units taken from the report's header rather
  than the row, paraphrase of a long statement, a detail carried over from an earlier statement.
  (Date uncertainty is expressed by `date_precision` / `date_note`, not here.)
- `low` — partly illegible, ambiguous, or reconstructed. Set `status: "needs_review"` so the user checks it.

## 4. Enums

**event_type**: `symptom` (what the subject feels) · `exam_finding` (what a clinician observed on examination) · `vital_sign` · `lab_result` · `imaging` · `diagnosis` · `medication` ·
`procedure` · `assessment` (a clinician's opinion that isn't a diagnosis: "问题不大", "恢复良好") ·
`encounter` (visit, admission, call) · `plan` (advice, follow-up, referral, scheduled test) ·
`history` (past history, allergies, family history as stated) · `lifestyle` · `ai_analysis` · `note`

**source_type** → default Excel label

| source_type | label | use when |
|---|---|---|
| `patient_report` | Patient report | the subject describes their own experience / measurements without a document |
| `caregiver_report` | Caregiver report | the user describes another subject (e.g. 妈妈) |
| `clinician_relayed` | Doctor (relayed by user) | "医生说…" — a clinician's words passed on by the user |
| `clinician_document` | Clinical document | 门诊病历, discharge summary, referral, prescription, 诊断证明 |
| `lab_report` | Lab report | laboratory result sheet |
| `imaging_report` | Imaging report | radiology / ultrasound / ECG report text |
| `device_measurement` | Device measurement | a reading from a home device or wearable (BP cuff, glucometer, watch) — typed by the user or shown in a photo/export; the evidence shows which |
| `ai_statement` | AI (chat assistant) | the assistant in the conversation (only with `ai_analysis`) |
| `other_document` | Document | pharmacy dispensing label, package insert, receipt, insurance form (a prescription written by the doctor is `clinician_document`) |
| `other` | Other | anything else — explain in `notes` |

**status**: `active` · `needs_review` · `disputed` · `superseded` · `retracted`

## 5. Sources (evidence files)

```json
{"source_id": "SRC-0003", "kind": "pdf", "title": "血常规+CRP 化验单", "filename": "lab_1003.pdf",
 "stored_path": "evidence/SRC-0003_lab_1003.pdf", "sha256": "9f2c…", "pages": 2,
 "origin": "chat upload", "document_date": "2026-10-03", "issuer": "XX医院检验科",
 "added_at": "…", "run_id": "RUN-0002"}
```

`kind`: `image` · `pdf` · `document` · `other`. Optional `notes` holds header details that are not
events (sample number, ordering doctor). Files are archived and hashed only when a real
`file_path` is given in the batch; identical files (same hash) are registered once and reused.
Chat text is not a source record — it is quoted directly in the event's `evidence`.

## 6. Runs and changelog

```json
{"run_id": "RUN-0002", "run_at": "…", "scope": "since checkpoint RUN-0001",
 "conversation_hint": "右膝疼痛随访 / 化验单", "last_message_anchor": "user: 好的，那我下周去复查",
 "events_added": ["EV-000007", "…"], "sources_added": ["SRC-0003"], "status_changes": 1,
 "duplicates_skipped": 0, "notes": ""}
```

`last_message_anchor` is a short verbatim snippet of the last message you processed, prefixed with the
speaker. It is how a later run in the same conversation knows where to resume if the checkpoint line
is not visible.

Changelog entries: `{"at", "run_id", "event_id", "from", "to", "reason"}` — written automatically for
every status change, including the ones implied by `supersedes` and `conflicts_with`.

## 7. Batch format

Write this to a temporary file and pass it to `append_event.py --batch`. Do not include `event_id`,
`fingerprint`, `recorded_at`, `run_id` or `superseded_by` — the script assigns them.

```json
{
  "run": {
    "scope": "since checkpoint RUN-0001",
    "conversation_hint": "右膝疼痛 + 化验单",
    "last_message_anchor": "user: 好的，那我下周去复查",
    "notes": ""
  },
  "subjects": [{"subject_id": "mother", "display_name": "妈妈"}],
  "sources": [
    {"temp_id": "S1", "kind": "pdf", "file_path": "/mnt/user-data/uploads/lab_1003.pdf",
     "filename": "lab_1003.pdf", "title": "血常规+CRP 化验单", "pages": 2,
     "document_date": "2026-10-03", "issuer": "XX医院检验科", "origin": "chat upload"}
  ],
  "events": [
    {"temp_id": "e1", "subject_id": "self", "event_date": "2026-10-03", "date_precision": "day",
     "date_note": "采样时间 2026-10-03 08:12", "event_type": "lab_result",
     "fact": "化验单：CRP 12 mg/L（参考 0–8）", "verbatim": "C反应蛋白 CRP 12.0 mg/L ↑ 0-8",
     "values": {"name": "CRP", "value": 12.0, "unit": "mg/L", "ref_range": "0-8", "flag": "↑"},
     "panel": "血常规+CRP 2026-10-03", "source_type": "lab_report",
     "evidence": [{"kind": "file", "source_id": "S1", "locator": "p.2"}],
     "analysis": [{"text": "高于报告参考范围（0–8 mg/L）", "by": "recorder"}],
     "confidence": "high"},
    {"temp_id": "e2", "event_date": "2026-10-03", "date_precision": "day", "event_type": "ai_analysis",
     "fact": null, "source_type": "ai_statement", "about": ["e1"],
     "evidence": [{"kind": "chat", "speaker": "assistant", "quote": "CRP 轻度升高，提示可能存在炎症…"}],
     "analysis": [{"text": "CRP 轻度升高提示可能存在炎症，建议结合症状由医生判断（推测，未经医生确认）",
                   "by": "ai_in_chat", "basis": ["e1"]}],
     "confidence": "high"}
  ],
  "status_changes": [
    {"event_id": "EV-000004", "new_status": "retracted", "reason": "用户说明：这条头痛是妈妈的，不是本人"}
  ]
}
```

References (`evidence[].source_id`, `supersedes`, `conflicts_with`, `about`, `analysis[].basis`) may
point at temp_ids from the same batch or at existing IDs in the database.

## 8. Lifecycle rules (how the "base layer may be wrong" problem is handled)

History is append-only. Content of a recorded event — including its analysis notes — is never edited;
only its status and links move. Analysis whose `basis` was later superseded or retracted is flagged in
the Excel export automatically.

| situation | do this | result |
|---|---|---|
| user corrects something they said ("剂量说错了") | new event with `supersedes: [old]` + `correction_reason` | old → `superseded`, linked both ways |
| better source for the same claim arrives (the actual report for a value the user quoted) | new event with the document as source, `supersedes: [old]`, reason "以原始化验单为准" (values match or differ — say which) | old → `superseded` |
| two sources disagree and nobody has said which is right | new event with `conflicts_with: [old]` | both → `disputed`; ask the user |
| record belongs to another person | new event under the right `subject_id`, `supersedes: [old]`, reason | old → `superseded`, points to the reassigned record |
| a record turns out to be wrong and has no replacement | `status_changes` → `retracted` | old greyed out, kept |
| user confirms a `needs_review` or `disputed` record | `status_changes` → `active` with the reason | changelog keeps the story |
| re-transcription of the same document fixes a misread digit | new event, same source, `supersedes`, reason "重新核对原件：…" | old → `superseded` |

Allowed manual transitions: active → retracted / needs_review; needs_review → active / retracted;
disputed → active / retracted / needs_review; retracted → active. A superseded event is final — to
change the outcome again, supersede its successor.
