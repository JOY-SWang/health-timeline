# Examples

These examples describe expected Skill behavior without using real patient data.

## 1. Add a patient-reported symptom

**User**

> Since yesterday my right knee has been painful, around 6/10, and stairs make it worse.

**Expected recording behavior**

- Create an atomic `symptom` event.
- Attribute it to `patient_report`.
- Preserve a short verbatim chat quote as evidence.
- Resolve “yesterday” relative to the message date and record how the date was derived.
- Do not infer a diagnosis into the fact field.

## 2. Preserve a clinician's uncertainty

**User**

> The orthopedist said it might be a meniscus injury.

**Expected recording behavior**

Record the clinician statement as relayed by the user and preserve “might be” / “considering” uncertainty. Do not rewrite it as a confirmed diagnosis.

## 3. Correct an earlier record without overwriting it

**User**

> I said 10 mg earlier, but the prescription actually says 20 mg once daily.

**Expected recording behavior**

- Add a new medication event with the corrected dose and its evidence.
- Link it to the earlier event with `supersedes` and a correction reason.
- Keep the earlier event in history with `superseded` status.

## 4. Keep conflicting sources visible

Suppose the user recalls a lab value as 12 mg/L but a later uploaded report shows 21 mg/L.

**Expected recording behavior**

Do not silently choose one value. Record the document-grounded event and represent the relationship according to the correction/conflict rules. If it is not clear whether the user's earlier statement was a mistake or genuinely reflects another measurement, mark the disagreement and ask rather than resolving it by inference.

## 5. Keep AI analysis separate

If an assistant previously suggested possible causes for knee pain, that content may be preserved as `ai_analysis` when the conversation is being archived. It must not appear as a patient fact or clinician diagnosis.

## Bundled machine-readable example

[`../assets/example_batch.json`](../assets/example_batch.json) demonstrates the complete batch shape, including chat evidence, a relayed clinician statement, AI analysis, and document-derived laboratory events. Its document path is illustrative; when using a real uploaded file, provide the actual local `file_path` so the writer can archive and hash the evidence.
