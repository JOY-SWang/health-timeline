# Health Timeline

A ChatGPT Skill for building a **source-attributed, append-only longitudinal health record** from conversation text, medical documents, images, and PDFs.

Health Timeline is designed around one rule:

> **A fact is an attributed assertion with evidence. Anything an AI concluded is analysis.**

Instead of flattening medical records and AI interpretations into one summary, the Skill keeps provenance, corrections, disagreements, and AI-generated analysis explicitly separate. The canonical record is JSON; an Excel workbook is generated as a human-readable view.

## What it does

- Extracts atomic health events from chat, reports, images, and PDFs.
- Attributes each event to its source (patient, relayed clinician statement, lab report, etc.).
- Keeps AI analysis separate from source-grounded facts.
- Archives evidence files and records page/region references when available.
- Preserves history with append-only corrections, superseding, conflicts, and retractions.
- Tracks multiple people with explicit subject IDs.
- Exports a readable Excel timeline and lab-trend sheets.
- Streams Apple Health ZIP/XML exports into source-separated daily summaries and selected original
  measurements, with an input/member coverage report and a separate device-trend sheet.
- Makes no network calls from its bundled Python scripts.

## What it is not

Health Timeline is **not a diagnostic system, medical device, or substitute for professional medical advice**. It records and organizes information. AI-generated interpretations are explicitly labeled and are not promoted into the fact layer.

See [DISCLAIMER.md](DISCLAIMER.md) for the full project disclaimer.

## Repository structure

```text
health-timeline/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── scripts/
│   ├── append_event.py
│   ├── export_excel.py
│   ├── validate_record.py
│   └── import_apple_health.py
├── references/
│   ├── schema.md
│   ├── extraction-rules.md
│   └── apple-health.md
├── assets/
│   ├── example_batch.json
│   └── icon.svg
├── examples/
│   └── README.md
├── README.md
├── DISCLAIMER.md
├── LICENSE
├── requirements.txt
└── .gitignore
```

## How the Skill works

```text
chat / images / PDFs
        ↓
   atomic claims
        ↓
 source attribution
        ↓
    validation
        ↓
 timeline.json  ← canonical, append-only record
        ↓
 timeline.xlsx  ← human-readable view
```

A correction never silently rewrites history. A new event can supersede an older event, mark a conflict, or retract a claim while retaining the earlier record and its provenance.

## Requirements

- Python 3.8+
- `openpyxl` for Excel export

Install the Python dependency:

```bash
python3 -m pip install -r requirements.txt
```

## Quick start for developers

Initialize an empty local timeline:

```bash
python3 scripts/append_event.py --db ./health-timeline-data --init
```

Validate the bundled example batch:

```bash
python3 scripts/validate_record.py --batch assets/example_batch.json
```

Preview an append without writing:

```bash
python3 scripts/append_event.py \
  --batch assets/example_batch.json \
  --db ./health-timeline-data \
  --dry-run
```

Append a validated batch:

```bash
python3 scripts/append_event.py \
  --batch assets/example_batch.json \
  --db ./health-timeline-data
```

Re-export Excel:

```bash
python3 scripts/export_excel.py --db ./health-timeline-data
```

The Skill itself normally performs these steps on the user's behalf; these commands are mainly useful for development, testing, and inspection.

## Example usage

Typical prompts include:

```text
Update my health timeline with today's visit.
```

```text
把这份化验单记录到我的健康时间线里，并保留原始证据。
```

```text
I gave you the wrong dose earlier. Correct it to 20 mg and keep the correction history.
```

```text
Show how my CRP has changed over time.
```

See [`examples/README.md`](examples/README.md) for expected behavior and correction/conflict examples.

## Apple Health exports

Preview an export without modifying the timeline:

```bash
python3 scripts/import_apple_health.py /path/to/export.zip \
  --out /path/to/private-data/apple-health-import-01
```

For selected original measurements, add `--batch`, then validate and append its generated
`apple-health-batch.json` through the existing scripts. Use `--since`, `--until`, `--types` and
`--batch-types` to limit the selection; run `--help` for all options.
Add `--xlsx` for a separate daily/sleep summary workbook without adding calculated values to the
timeline's fact layer.

Daily CSV/JSON statistics stay separate from source-measured timeline facts. Raw additive sums may
include overlapping samples and differ from Apple's displayed totals. Sleep durations merge overlaps
per source; the exporter does not combine different devices. CDA, GPX, ECG, workouts and unsupported
categories are inventoried rather than silently treated as imported. No direct phone/portal access
is added. See [`references/apple-health.md`](references/apple-health.md) for exact coverage and rules.

The importer needs only Python's standard library. Excel export and its integration tests need the
existing `openpyxl` dependency. Run the synthetic test suite with:

```bash
python3 -m unittest discover -s tests -v
```

## Data model and provenance

The main design distinction is:

- **Fact layer** — what a named source asserted or measured, with evidence.
- **Analysis layer** — AI-generated interpretation, explicitly labeled.
- **Lifecycle layer** — supersedes, conflicts, retractions, and review status without destructive edits.

For the complete schema, see [`references/schema.md`](references/schema.md). For extraction and attribution rules, see [`references/extraction-rules.md`](references/extraction-rules.md).

## Privacy and data handling

The bundled scripts do not make network calls. By default, timeline data and archived evidence are written to the local data directory selected by the user. Medical records are sensitive: do not commit real health data, generated `timeline.json`/`timeline.xlsx`, or evidence files to a public repository.

The included `.gitignore` excludes the default local data directory and common generated artifacts, but users remain responsible for checking commits before publishing them.

## Using the Skill in ChatGPT

This repository contains the source of the Skill. The Skill entrypoint is [`SKILL.md`](SKILL.md), with UI metadata in [`agents/openai.yaml`](agents/openai.yaml).

A distributable Skill archive can be produced from this directory using the standard Skill packaging workflow. Keep the source repository and packaged release aligned so users can inspect exactly what they install.

## Contributing

Issues and pull requests are welcome. Changes that affect extraction, provenance, correction semantics, or the schema should include a concrete before/after example and should preserve the core fact-versus-analysis boundary.

Please do not include real patient data, identifiable medical records, or credentials in issues, examples, tests, or pull requests.

## License

Released under the [MIT License](LICENSE).
