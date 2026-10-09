# Apple Health exports

Read this reference when the input is an Apple Health export or the user wants wearable data
organised. The importer uses Python's standard library; Excel export still needs `openpyxl`.

## Identify the input and the requested outcome

Apple's **Health → profile → Export All Health Data** produces a ZIP containing `export.xml`, often
alongside `export_cda.xml`, workout GPX files and ECG CSV files. Inspect member names before choosing a
parser. A ZIP from an Athena or other patient portal may be a different format. Medical PDFs/images
continue through the ordinary report-extraction workflow; a portal URL alone does not grant a login
session or provide the reports.

For an existing timeline, find its data directory and confirm the subject as in `SKILL.md`.
For a request to inspect or summarise an export, generate the local summary without creating or
modifying a timeline. When recording is requested, use the default low-frequency selection unless
the user identifies other types or a date range. Keep generated files in the chosen private data
directory, outside source control. Use a fresh output subdirectory for each run.

## Stream and preview

```bash
python3 SKILL_DIR/scripts/import_apple_health.py /path/to/export.zip \
  --out DATA_DIR/apple-health-import-01
```

The script opens the XML inside the ZIP without extracting it. It also accepts a standalone
`export.xml`. It produces:

- `apple-health-summary.json`: input SHA-256, member inventory, processed/skipped coverage, type counts,
  warnings, date bounds, selection, daily statistics and their calculation rules.
- `apple-health-daily.csv`: the same daily rows, readable in a spreadsheet. Read it as UTF-8 with BOM
  (`utf-8-sig` in Python). Spreadsheet-formula prefixes in text fields are escaped.
- `apple-health-batch.json`: only when `--batch` is requested; selected **original** measurements in
  the existing timeline batch schema, ready for validation and append.
- `apple-health-summary.xlsx`: only with `--xlsx` (requires `openpyxl`); separate Daily quantities,
  Sleep, Coverage and Guide sheets. When the user requests Excel trends including sleep, add this
  option and deliver this workbook alongside any canonical timeline export. Calculations are kept
  separate from the original-measurement fact layer. JSON retains authoritative decimal strings;
  numeric spreadsheet cells use Excel's usual limited precision.

Optional inclusive local-date and type filters:

```bash
python3 SKILL_DIR/scripts/import_apple_health.py /path/to/export.zip \
  --out DATA_DIR/apple-health-import-02 \
  --since 2026-01-01 --until 2026-03-31 \
  --types BodyMass,RestingHeartRate,HeartRateVariabilitySDNN,StepCount,SleepAnalysis
```

Type names may be their full `HKQuantityTypeIdentifier…` / `HKCategoryTypeIdentifier…` identifiers or
short names; `SleepAnalysis` expands to its category identifier. Quantity types not in a predefined
catalog are still summarised if their values and timestamps are valid.

Parsing memory holds day/type/source/unit buckets, selected batch events and sleep intervals, rather
than the full XML tree. Progress is printed every 250,000 Records; `--progress-every 0` disables it.
Dates/types may be interleaved, so even a narrow selection scans the entire XML to verify coverage.
The output directory appears only after parsing succeeds; failures leave no partial result files.
Existing output directories are never overwritten. There is no resume cursor: retry a failed parse
with the same input and a new output path.

## What daily statistics mean

These are **calculated descriptions of exported samples**, not diagnoses, lab results, or new
source-measured facts. Keep them in the summary files; never paste calculated daily values into the
timeline's fact layer as if a device measured them directly.

| Data | Calculation and day boundary |
|---|---|
| Quantities | Count, minimum, maximum and sample mean, grouped by type, source name, original unit and local **start date**. The mean is not time weighted. |
| Additive quantities | `recorded_sum` for steps, selected distances/energy, flights, exercise/stand time and daylight. This is the sum of raw exported values; duplicates and overlapping intervals can inflate it. It is **not** Apple's source-prioritised daily total. |
| Sleep | Recognised InBed, Awake and asleep stage intervals are grouped by source and local **end date** (wake date). Each entire interval belongs to that day. Overlaps and exact duplicates are merged within each stage. |
| Total asleep | Union of all asleep-stage intervals for that source/day. InBed and Awake do not contribute. Stage durations must not be added to the total again. |

Sources and units are never summed together or silently converted. Recorded UTC offsets determine
dates; the computer's timezone is not used. Sleep elapsed times respect offsets, including DST
changes. Quantity intervals crossing midnight are not split; sleep intervals are not split either.
Six decimal places are used for derived means/durations, with unchanged original values in batches.
Numeric values must be finite and within the parser's working range: absolute value ≤ `1e18`,
at most 28 coefficient digits, decimal exponent between -30 and 18, and at most 128 input characters.
These are arithmetic/resource limits, not clinical thresholds. Rejected values are counted, not
repaired or interpreted.

Report missing sources/units and unsupported records. The member list explicitly distinguishes
processed `export.xml` from **unprocessed** CDA, GPX and ECG files. Workouts, ActivitySummary and other
categories are counted but not imported. Menstrual, symptom and other category values need their own
extraction workflow. A successful parse does not mean the whole ZIP was imported.

## Append selected original measurements

```bash
python3 SKILL_DIR/scripts/import_apple_health.py /path/to/export.zip \
  --out DATA_DIR/apple-health-import-03 --batch --subject self \
  --batch-types BodyMass,RestingHeartRate \
  --anchor 'user: 导入这份 Apple Health 数据'
python3 SKILL_DIR/scripts/validate_record.py \
  --batch DATA_DIR/apple-health-import-03/apple-health-batch.json --db DATA_DIR
python3 SKILL_DIR/scripts/append_event.py \
  --batch DATA_DIR/apple-health-import-03/apple-health-batch.json --db DATA_DIR --dry-run
python3 SKILL_DIR/scripts/append_event.py \
  --batch DATA_DIR/apple-health-import-03/apple-health-batch.json --db DATA_DIR
```

Set `--anchor` to an actual short quote of the last processed user message, not the example above.
Without it the importer writes a file/hash anchor, which cannot locate a message in chat history.
`--subject` must name an existing subject; the importer does not infer the subject from demographics.
Keep the input file at the recorded path until append completes: the original ZIP/XML is archived
and hashed by the existing writer. The importer itself does not copy or alter the original archive.
This evidence copy may contain GPX/ECG data even though those members were not parsed.

The default batch selection is BodyMass, BodyMassIndex, Height, RestingHeartRate, VO2Max,
BloodPressureSystolic, BloodPressureDiastolic and BloodGlucose. High-frequency measurements remain
in daily summaries by default. Other original quantity types require explicit `--batch-types`.
`--types` filters both summaries and the pool of available batch records.

The default batch limit is 10,000 events. Exceeding it fails the whole import without output; narrow
the dates/types before increasing `--max-events`. The timeline writer loads and rewrites JSON and
Excel, so retaining millions of events is inappropriate even though XML parsing is streamed.

Each event preserves type, exact value/unit, source and original timestamps. An exact `measurement_id`
digest distinguishes decimal values, source punctuation and UTC offset signs during deduplication.
This opt-in field leaves older event fingerprints unchanged.
Evidence locators name the XML member and the 1-based Record ordinal across the document, including
filtered/skipped Records. Selected events are `device_measurement` / `vital_sign`, not `lab_result`.
Excel's **Device trends** groups them by source and unit; **Lab trends** remains for labs.
Re-appending the same batch skips duplicate events and reuses the archived source by hash.

After a requested append, report its checkpoint as usual. Also state the total Records seen,
processed/filtered/invalid/unsupported counts, selected event count and unprocessed member types.
Delete the temporary batch after a successful append; retain the summary JSON, CSV and optional XLSX.
Link these local outputs for detailed trends. Any medical interpretation belongs in the
separate AI analysis layer, with its evidence and uncertainty; the importer generates none.
