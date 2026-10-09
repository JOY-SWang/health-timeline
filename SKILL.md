---
name: health-timeline
description: Use when the user wants to record, update, correct, export or summarise a personal health timeline from conversations, medical reports, images, PDFs, or Apple Health export.zip/export.xml. Keeps source-backed observations separate from AI analysis and handles large wearable exports through local daily summaries and selected original measurements. Triggers include 记录一下, 更新时间线, 把化验单整理进表格, import Apple Health, 导入健康数据. It records and organises; it does not diagnose or automatically access a phone or patient portal.
---

# Health Timeline

Turn the medical content of a conversation into a clean, append-only health record:

```
chat text / images / PDFs → atomic claims → validation → timeline.json (canonical) → timeline.xlsx (view)
Apple Health ZIP / XML → streaming summary + selected original measurements → the same validation/append flow
```

This skill records; it does not diagnose. You can still be a helpful assistant in the conversation,
but nothing you conclude goes into the record as a fact.

## The rule everything else serves

**A fact is an attributed assertion with evidence. Anything an AI concluded is analysis.**

- `fact` says *who asserted what*: "医生（用户转述）：考虑半月板损伤", "化验单：CRP 12 mg/L（参考 0–8）".
  It records that something was said or measured, so it stays true even if the content proves wrong.
- `analysis` holds AI-generated content only, labelled `ai_in_chat` (what the assistant said earlier in
  the conversation) or `recorder` (a short mechanical note you add: comparison with a printed reference
  range or an earlier record, or what is unknown). No diagnoses, causes or advice from the recorder.
- Every event cites evidence: an archived file + page/region, or a verbatim chat quote.
- Nothing is overwritten. Mistakes in the base layer — a misremembered dose, an OCR slip, the wrong
  person — are fixed by new events that `supersede` old ones, by `conflicts_with` when sources disagree,
  or by retracting. The old record stays visible, greyed out, with the reason.

Why so strict: this record will be read later by the user, by doctors, and by other AI systems. If
interpretation leaks into the fact layer, nobody can tell what was observed from what was guessed, and
errors compound silently.

## Files in this skill

- `scripts/append_event.py` — the only writer: `--init`, `--batch`, `--list`, `--last-run`, `--retract`.
  Assigns IDs, archives and hashes files, de-duplicates, applies supersedes/conflicts/status changes,
  validates, backs up, writes atomically, then regenerates the Excel file.
- `scripts/validate_record.py` — checks a batch (`--batch`) or the whole database (`--db`).
- `scripts/export_excel.py` — re-exports the workbook (`--csv` if openpyxl is unavailable).
- `scripts/import_apple_health.py` — streams Apple Health ZIP/XML into a coverage report, daily CSV,
  an optional daily/sleep Excel workbook, and optionally a batch of selected original measurements.
  No automatic database writes.
- `references/apple-health.md` — read for Apple Health exports: recognition, source separation,
  sleep/quantity statistics, date filters, coverage limits, and the import/append workflow.
- `references/extraction-rules.md` — **read before your first extraction in a session**: atomicity,
  attribution wording, what may go in analysis, AI statements, dates, images, PDFs, corrections, examples.
- `references/schema.md` — every field, enums, batch format, lifecycle rules. Read when writing a batch.
- `assets/example_batch.json` — a complete, valid batch to copy the shape from.

Scripts need Python 3.8+ and `openpyxl` for Excel. Run them from wherever the data directory is
reachable; if the data lives on another machine than the scripts, copy `timeline.json` in, run, and copy
`timeline.json`, `timeline.xlsx` and new `evidence/` files back — never let two copies drift apart.

## Workflow for each run

### 1. Find the database

Use, in order: a location the user names → `$HEALTH_TIMELINE_DB` → an existing
`health-timeline-data/timeline.json` in the working directory or the user's shared/connected folders
(search a few levels deep). If none exists, ask the user once where to keep it (suggest
`health-timeline-data/` inside their folder) and run `append_event.py --db DIR --init`.
Don't silently start a second database when one may exist elsewhere — a fragmented medical record is
worse than one extra question.

### 2. Decide what is new

Record only what has not been recorded yet:

1. Find the most recent line in this conversation that starts with `[health-timeline checkpoint]`.
   Everything after it is new.
2. No checkpoint line? Run `append_event.py --db DIR --last-run`. If its `last_message_anchor` appears in
   this conversation, start right after that message.
3. Otherwise the conversation has not been recorded: scan all of it.

Within the window, include user and assistant turns and every attached image/PDF. Skip this skill's
own output (summaries, checkpoint lines).

### 3. Read the inputs

- For Apple Health `export.zip` or `export.xml`, read `references/apple-health.md` and use the streaming
  importer. Inspect the ZIP member list to identify its actual format; a filename or preceding portal
  link does not identify the export. Do not load a large XML into the conversation or turn every
  high-frequency sample into a timeline event. Summary-only requests need no database creation.
- Look at every image yourself. For PDFs extract text per page (`pdftotext -layout -f N -l N`, or
  pdfplumber) so you can cite pages; render scanned pages to images and read them.
- Note the date each message was sent — relative dates ("昨天") resolve against it.

### 4. Extract claims into a batch

Follow `references/extraction-rules.md`. The short version:

- one checkable assertion per event (each lab analyte separately, grouped by `panel`);
- Apple Health batches contain selected raw measurements, with original timestamps in their assertions
  and ZIP member/Record locators. Daily calculated summaries stay in the summary files, outside the fact
  layer. Review coverage and skipped types before appending; do not call a partial import complete.
- `source_type` = who asserted it; keep the source's certainty ("考虑", "?") exactly;
- copy values, units, ranges and flags exactly as printed;
- `confidence` = how faithfully the record matches its source (not whether it is medically true);
  low-confidence transcriptions get `status: "needs_review"`;
- case-specific AI interpretations from the chat → `analysis` (`ai_in_chat`) or a standalone
  `ai_analysis` event; generic explanations are not recorded;
- new people (e.g. 妈妈) → add under `subjects` and use their `subject_id`.

Before finalising, look for related records — `append_event.py --db DIR --list --search "CRP"` — and
decide per claim: duplicate (skip), change over time (new event), correction (`supersedes` +
`correction_reason`), better source (`supersedes`), wrong person (new event under the right subject with
`supersedes`), disagreement (`conflicts_with`), withdrawn with no replacement (`status_changes` →
retracted). Corrections keep the clinical date of what they correct. Never settle a disagreement by
choosing a source yourself; record both and ask. Never edit an existing record's content or analysis —
not even a note that a correction made stale (the export flags those automatically).

Write manually extracted batches to a temporary file outside the data directory (shape:
`assets/example_batch.json`; details: `references/schema.md` §7). Generated Apple Health batches can
stay with their private import outputs. Delete batch files and rendered page images once append
succeeds; retain Apple Health coverage reports and daily summaries, which are separate derived views.
Fill `run.last_message_anchor` with a short quote of the last message you processed, prefixed by the
speaker, e.g. `"user: 好的，那我下周去复查"`. For files that exist on disk (uploads), pass `file_path`
so they are archived into `evidence/` with a hash.

### 5. Validate and append

```bash
python3 SKILL_DIR/scripts/validate_record.py --batch /tmp/batch.json --db DATA_DIR
python3 SKILL_DIR/scripts/append_event.py    --batch /tmp/batch.json --db DATA_DIR
```

Fix every error and re-run; read the warnings — most point at an inference leaking into a fact, a
missing unit, or a date that needs a note. Never edit `timeline.json` by hand. `--dry-run` previews.
The append step regenerates `timeline.xlsx` and prints a checkpoint line.

### 6. Report back

Keep it short:

- a compact table of what was added: date · fact · source (· analysis if any);
- **anything that needs the user**: `needs_review` items (what is unclear), `disputed` pairs (state the
  disagreement and ask which is right), approximate or unknown dates, new subjects, skipped duplicates if
  that might surprise them;
- where the files are (`timeline.json`, `timeline.xlsx`);
- end with the checkpoint line exactly as the script printed it. The next run in this conversation uses
  it to know where to resume, so it must appear verbatim in your reply.

If the window contained nothing worth recording, say so and don't write an empty run.

## Other requests

- **"Show / summarise my timeline", "what was my CRP over time"** — read with `--list` (filters:
  `--subject --type --status --since --search`, `--full` for JSON) and answer from the records, citing
  event IDs. Keep the same separation in your answer: say what the records show, then, clearly marked,
  any interpretation.
- **"That record is wrong" / "that was my mum's, not mine"** — correction flow from step 4 (see
  extraction-rules §10 and examples E–G); for a quick withdrawal with no replacement:
  `append_event.py --db DIR --retract EV-000012 --reason "…"`.
- **"Export again" / "only my mum's records"** — `export_excel.py --db DIR [--subject mother] [--csv]`.

## Excel layout

Timeline sheet columns: `Date | Fact | Source | Analysis / Inference | Image / Evidence | Status | Event ID`
(plus `Subject` after Date when more than one person is tracked). Analysis is purple italics; standalone
AI rows are lavender; needs-review rows yellow; disputed rows orange; superseded/retracted rows grey and
struck through. Date and Fact cells carry hover notes (date derivation, verbatim text); evidence cells
link to the archived file. Further sheets: Lab trends (analyte × date), Evidence, Log (runs and status
changes), Guide. Device trends shows original device measurements by date, source, and unit; they are
never relabelled as lab results. Large Apple Health daily summaries are a separate CSV/JSON view.

## Boundaries

- Record what the user shares, without judgement or commentary.
- The data stays in the local data directory; the scripts make no network calls. Don't copy health
  details into other places (memory, other files) unless the user asks.
- The workbook's Guide sheet states that analysis is AI-generated and not medical advice; don't add
  disclaimers to individual records.
