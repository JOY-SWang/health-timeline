# Extraction rules: from conversation to attributed events

Contents: 1. Mindset · 2. Atomic claims · 3. Attribution & fact wording · 4. The analysis column ·
5. AI statements in the chat · 6. Dates · 7. Images · 8. PDFs and lab sheets · 9. What not to record ·
10. Corrections, conflicts, duplicates · 11. Worked examples

## 1. Mindset

Work like a meticulous medical scribe, not a clinician. Every record answers four questions:
**who asserted what, about whom, when — and where is the evidence?** Whether the assertion is true is
not the scribe's call. That is the whole point: later readers (the user, a doctor, another AI) must be
able to tell observation from interpretation, and trace any line back to its origin. A timeline that
quietly mixes the two is worse than no timeline, because errors in the base layer become invisible.

## 2. Atomic claims

One event = one checkable assertion.

- Lab sheet → one event per analyte, grouped with a shared `panel` — one panel per report, even if the
  sheet has several sections (血常规 + 炎症指标).
- One symptom with its attributes (site, severity, onset, triggers) → one event. Two symptoms → two events.
- A doctor visit described in one message → `encounter` (the visit) + one event each for what the
  doctor found on examination (`exam_finding`), the relayed diagnosis/impression, each medication, each
  test ordered or plan. If the results of an ordered test are recorded in the same run, the result events
  are enough — don't add a separate "test ordered" event.
- A doctor's opinion that is not a diagnosis ("CRP 稍微高一点，问题不大", "恢复得不错") → `assessment`.
- Several readings from one measurement (BP and pulse from one cuff reading) → one event per quantity.
- A medication: one event per drug, with dose/route/frequency as stated. A dose change later is a new
  event (a change over time), not a correction — unless someone says the earlier record was wrong.

## 3. Attribution and fact wording

Write the fact so it stays true even if the content turns out wrong — it records the *assertion*.
Use the language of the conversation; keep the original text in `verbatim`.

| source_type | fact pattern |
|---|---|
| patient_report | 用户报告右膝疼痛 6/10，上楼加重 · User reports right knee pain 6/10 |
| caregiver_report | 用户报告母亲头痛 3 天 |
| clinician_relayed | 医生（用户转述）：考虑半月板损伤，建议 MRI |
| clinician_document | 门诊病历：初步诊断 右膝半月板损伤？ |
| lab_report | 化验单：CRP 12 mg/L（参考 0–8，↑） |
| imaging_report | MRI 报告印象：右膝内侧半月板后角 II 度信号 |
| clinician_document vs other_document | a prescription (处方笺) or 病历 written by the clinician → `clinician_document`; a pharmacy dispensing label, package insert or receipt → `other_document` |
| device_measurement | 家用血压计（用户报告）：血压 135/88 mmHg |

Rules that keep facts clean:

- **Never change certainty.** "考虑…" stays "考虑…", "?" stays "?", "rule out" stays "rule out". Do not turn
  a doctor's impression into a diagnosis or a diagnosis into an impression.
- **Never add a cause, interpretation or severity** the source did not state. "膝盖疼" is not "膝关节炎症状".
- **The patient's own beliefs are facts about what they said**: "用户认为可能是跑步扭伤所致" is fine —
  attributed. Unattributed "可能是扭伤" in a fact is not (the validator warns about this).
- **Second-hand is second-hand.** A lab value the user types from memory is `patient_report`, with an
  analysis note "未见原始化验单" — not `lab_report`.
- **Copy numbers exactly as given**, including units, ranges and flags. Do not convert units, round, or
  fix apparent typos; if something looks off, record it as is and lower `confidence` with a note.
  If the source gives no unit, don't supply one — leave `unit` empty (a recorder note "单位未说明" helps
  for lab values).
- `source_label` may be more specific than the default ("Lab report (仁济医院检验科)", "Dr. Lim, SGH ortho (relayed)").

## 4. The analysis column

Analysis is where AI-generated content goes, labelled by origin:

- `by: "ai_in_chat"` — something the assistant said earlier in the conversation (see §5).
- `by: "recorder"` — a note you add while recording. Keep these short and mechanical. Allowed:
  - comparison with the reference range **printed on the same document** ("高于报告参考范围 0–8");
  - comparison with earlier records of the same thing, citing them ("较 EV-000003（09-20：8 mg/L）上升");
  - what is unknown or missing ("原因未知", "报告未注明单位", "未见原始化验单", "日期为推算");
  - pointing out a correction or conflict;
  - a neutral description of a photo, explicitly marked non-clinical (see §7).

  Not allowed: diagnoses, likely causes, prognosis, treatment suggestions, urgency judgments, or
  severity beyond what the source flagged. The recorder's notes will sit inside the medical record
  for years; anything speculative there would be read as part of the record.

An empty analysis cell is completely fine. Do not generate analysis to fill space.

## 5. AI statements in the chat

Earlier assistant turns often interpret the user's situation. Those interpretations are worth keeping
(later you can see what the AI suggested and whether it held up) — but they are never facts.

**Record** case-specific interpretation, differentials, risk statements and recommendations:
"你描述的症状更像髌腱炎", "CRP 轻度升高提示可能存在炎症", "建议一周内骨科就诊".
**Skip** generic education ("CRP 是肝脏合成的一种蛋白"), restatements of what the user said, disclaimers,
small talk, and anything this skill itself output (summaries, checkpoint lines).

Where to put it:
- **Inline** (that event's `analysis`, `by: "ai_in_chat"`) only when the statement interprets exactly one
  fact you are recording in this same run — e.g. the assistant's reading of a single lab value. This keeps
  the interpretation next to the fact in Excel.
- **Standalone** in every other case — differentials, overall assessments, recommendations, anything that
  spans several facts or refers to earlier records: `event_type: "ai_analysis"`, `source_type:
  "ai_statement"`, `fact: null`, `about: [ids it refers to]`, evidence = a quote of the assistant, dated
  the day the assistant said it. Most chat interpretations land here (see example D).

Condense faithfully into one or two sentences, keep the hedges, and keep recommendations distinct from
interpretations ("更像髌腱炎；建议一周内骨科就诊"). The Excel export already prefixes these with
"AI (chat):", so don't start the text with "AI 认为". Do not upgrade or soften what the AI said.

Special case — the assistant transcribed a document earlier (e.g. read values off a photo) and the
original is no longer available to you: record the values with the document's `source_type`, evidence
= quote of the assistant's transcription, `confidence: "medium"`, `status: "needs_review"`,
`confidence_note: "transcribed by the assistant earlier in chat; original not re-checked"`. If the
original *is* available, transcribe from it yourself and cite it.

## 6. Dates

- `event_date` is when it happened clinically, not when it was recorded or discussed.
- Resolve relative expressions ("昨天", "上周三", "三周前开始") against the date the message was sent.
  If messages are undated, use today's date as the anchor and say so in `date_note`.
- Lab results: specimen collection time (采样/采集时间) first; else report date — say which in `date_note`.
- Ongoing things ("一直在吃二甲双胍"): date = when it was reported, `date_note: "ongoing as of …"`.
- Plans and appointments may be in the future.
- Corrections and reassignments keep the clinical date of the thing they correct, not the date of the
  correction (example E).
- Date uncertainty lives in `date_precision` and `date_note`, not in `confidence`: "昨天" resolved against
  a dated message is an exact `day`; "两周后" is `approximate`.
- Vague ("去年夏天", "小时候") → coarsest honest precision (`year`, `approximate`) with a note.
  Genuinely unknown → `event_date: null`, `date_precision: "unknown"`. Never invent a precise date.

## 7. Images

Look at every image yourself. Then:

- **Photographed documents** (化验单, prescriptions, discharge notes, screenshots of reports): transcribe
  as printed. `confidence` follows legibility; anything you are not sure you read correctly gets
  `low` + `needs_review` + a note on what is unclear. Locator: region or row ("右上角 / 第 3 行").
- **Device screens / app screenshots**: `device_measurement`, values as displayed, with the time shown on screen.
- **Photos of the body** (rash, swelling, wound): the fact is what the user said plus that the photo
  exists ("用户上传右膝照片，称肿胀两天"). If useful, a recorder note may describe what is visible in
  neutral terms, labelled: "照片可见右膝前侧较左侧饱满（目测描述，非临床评估）". No interpretation.
- Register every image as a source; pass `file_path` when the file exists on disk so it gets archived
  and hashed. If it only exists inline in the chat, register it by filename/description anyway.

## 8. PDFs and lab sheets

- Extract text page by page so you can cite pages: `pdftotext -layout -f N -l N file.pdf -` or pdfplumber.
  If a page has no text layer (scanned), render it (`pdftoppm -r 150 -png`) and read the image.
- Header facts — hospital, department, specimen, collection/report times, ordering doctor — go into the
  source record (`issuer`, `document_date`) and `date_note`, not into separate events.
- One event per analyte: `values = {name, value, unit, ref_range, flag}` exactly as printed, with
  `locator` "p.N". Record every analyte on the sheet, not just abnormal ones — normal results matter
  for trends, and picking "interesting" ones is itself an interpretation.
- Narrative reports (imaging, discharge summaries): one event per finding/impression/diagnosis/plan, quoting
  the report's own words in `verbatim`.

## 9. What not to record

- Hypotheticals and questions with no assertion ("如果明天还疼怎么办？", "CRP 高是什么意思？").
  But extract the fact hidden inside a question: "我 CRP 12 高吗？" → 用户报告 CRP 12 (unit not stated).
- Generic medical education, the assistant's disclaimers, the skill's own outputs.
- Things already in the database (check first — see §10).
- Stories about people who are not tracked subjects ("我同事也膝盖疼").
- When the user explicitly says not to record something, don't.

## 10. Corrections, conflicts, duplicates

Before writing a batch, look up related records:
`append_event.py --db DIR --list --search "CRP"` (or `--type medication`, `--since 2026-09-01`).

Then classify each new claim:

| new claim vs existing record | action |
|---|---|
| same claim, same source type, same date | skip (the script also drops exact duplicates) |
| same thing at a different time (pain 6/10 → 3/10; dose changed by the doctor) | new event; optionally a recorder note comparing them — this is a change, not a correction |
| user says the earlier record was wrong | new event with `supersedes` + `correction_reason` quoting the user |
| original document arrives for a value previously only reported | new document-sourced event with `supersedes`; reason states whether values matched |
| incompatible claims about the same thing at the same time, nobody has said which is right | new event with `conflicts_with`; both become disputed; tell the user and ask |
| record belongs to a different person | new event under the right `subject_id` (add the subject if new) with `supersedes` + reason ("用户说明：是母亲的情况，不是本人") — the old record is greyed out and points to its replacement |
| record is simply wrong and has no replacement | `status_changes` → `retracted`, reason quoting the user |
| user confirms a needs_review / disputed record | `status_changes` → `active`, reason quoting the user |

Never resolve a conflict by picking the more authoritative source silently. Higher authority is a
good reason to *ask* — the lab report may be for a different date, the doctor may have changed the dose.

Older analysis notes are never rewritten. If a recorder or AI note was based on a record that later gets
superseded or retracted (e.g. "较 6/10 下降" after 6/10 is corrected to 7/10), cite the basis in
`analysis[].basis` when you write it; the Excel export then flags it automatically
("⚠ basis changed"). Mention the affected note in your reply if it matters.

## 11. Worked examples

**A. Symptom with relative date and the patient's own theory** (message sent 2026-10-03)
> 用户：昨天开始右膝疼，大概6分，上楼更疼，我觉得是周末跑步扭到了

```json
{"temp_id": "e1", "event_date": "2026-10-02", "date_precision": "day",
 "date_note": "用户 2026-10-03 说'昨天开始'", "event_type": "symptom",
 "fact": "用户报告右膝疼痛约 6/10，上楼加重；用户认为与周末跑步扭伤有关",
 "verbatim": "昨天开始右膝疼，大概6分，上楼更疼，我觉得是周末跑步扭到了",
 "source_type": "patient_report",
 "evidence": [{"kind": "chat", "speaker": "user", "quote": "昨天开始右膝疼，大概6分，上楼更疼", "said_on": "2026-10-03"}],
 "analysis": [{"text": "疼痛原因未经医生评估", "by": "recorder"}], "confidence": "high"}
```

**B. One message, several clinician claims** (sent 2026-10-08)
> 用户：今天看了骨科，医生说考虑半月板损伤，开了塞来昔布 200mg 每天一次，让我两周后做 MRI

→ four events, all with the same chat quote as evidence:
`encounter` "用户报告 2026-10-08 骨科门诊就诊" (patient_report) ·
`diagnosis` "医生（用户转述）：考虑半月板损伤" (clinician_relayed — "考虑" kept) ·
`medication` "医生（用户转述）开具塞来昔布 200 mg 每日一次" (clinician_relayed) ·
`plan` event_date 2026-10-22, precision `approximate`, "医生（用户转述）：两周后行 MRI" (clinician_relayed).

**C. Lab sheet analyte** — see the batch example in schema.md §7 (CRP with page locator and a
recorder note against the printed reference range).

**D. Assistant interpretation in an earlier turn**
> 助手：结合你说的上楼痛和跑步史，髌股疼痛综合征或髌腱炎都有可能，建议先减少跑量，如果一周不缓解去骨科。

```json
{"temp_id": "e5", "event_date": "2026-10-03", "date_precision": "day", "event_type": "ai_analysis",
 "fact": null, "source_type": "ai_statement", "about": ["e1"],
 "evidence": [{"kind": "chat", "speaker": "assistant", "quote": "髌股疼痛综合征或髌腱炎都有可能，建议先减少跑量"}],
 "analysis": [{"text": "髌股疼痛综合征或髌腱炎都有可能；建议减少跑量，一周不缓解则骨科就诊", "by": "ai_in_chat", "basis": ["e1"]}],
 "confidence": "high"}
```

**E. Correction**
> 用户：等下，我之前说布洛芬 400mg 一天三次是错的，其实是 200mg 一天两次

Find the earlier record (`--search 布洛芬` → EV-000006), then:
```json
{"temp_id": "e7", "event_date": "2026-10-01", "date_precision": "day", "event_type": "medication",
 "fact": "用户报告服用布洛芬 200 mg 每日两次", "source_type": "patient_report",
 "supersedes": ["EV-000006"], "correction_reason": "用户更正：剂量是 200mg 每日两次，不是 400mg 每日三次",
 "evidence": [{"kind": "chat", "speaker": "user", "quote": "其实是 200mg 一天两次"}], "confidence": "high"}
```
(The date is that of the original medication record, because the correction is about that period.)

**F. Conflict**
The database has "医生（用户转述）开具塞来昔布 200 mg 每日一次" (EV-000009). A photo of the doctor's
prescription (处方笺) now shows "塞来昔布胶囊 0.2g bid". Record it as `clinician_document` (a pharmacy
label would be `other_document`) with
`conflicts_with: ["EV-000009"]`, and tell the user: the two records disagree on frequency (qd vs bid);
which is right? Do not pick one.

**G. Wrong person**
> 用户：之前说的过敏性鼻炎其实是我妈的，不是我的。她用这个喷剂好几年了。

Add `{"subject_id": "mother", "display_name": "妈妈"}` under `subjects`, then for each affected record
a new `caregiver_report` event for `mother` with `supersedes: ["EV-000004"]`, `correction_reason:
"用户说明：是母亲的情况，不是本人"`, keeping the original clinical date and adding what the correction
newly says ("已用好几年") to the fact. Details the correction doesn't restate (e.g. "每日一次" said
about the wrong person) carry over with `confidence: "medium"` and a note, and are worth confirming.
