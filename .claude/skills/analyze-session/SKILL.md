---
name: analyze-session
description: Analyses a finished facilitation-suite session from its folder — the results dump, the session's playbook and the facilitator's notes — and writes the report into the session's report/ folder. Generic; what to analyse and in which shape comes from the session's playbook.md. E.g. "/analyze-session <session folder>", "/analyze-session last", "analyze the last session".
---

# Analyze a session

Turns one session's results into the report the facilitator hands on. The skill is generic: every session-specific instruction (the purpose, what to analyse, the output shape, its rules, who to leave out) lives in the session's `playbook.md`, which the facilitator edits from the app's Playbook card. Nothing here names a workshop type, a client or a person.

Run it from the facilitation-suite repo root, where `.venv` and `src/` are.

## 1. Resolve the session

- A folder path argument is the session folder.
- `last` (or "the last session") is the ledger's session with the latest date: the dump's `--last` option resolves it.
- Neither → ask the facilitator for the folder.

## 2. Load the session

```
.venv/Scripts/python.exe -m src.results.dump "<session folder>"
.venv/Scripts/python.exe -m src.results.dump --last
```

(`.venv/bin/python` on macOS or Linux.) It prints one JSON document on stdout and writes nothing; a non-zero exit means the folder holds no readable `session.yaml`, and stderr says why. Confirm the session's `title` and `date` back to the facilitator before going on.

The document holds:

| Key | What it is |
|---|---|
| `folder`, `report_dir` | the session folder and the folder the report goes to |
| `session` | `title`, `date` |
| `went_live` | false when the session never ran |
| `activities` | in the order they ran: `number`, `title`, `question`, `type_label`, `captured` (time span), counts, and `answer_rows` (`sender`, `as` = the name it was sent under when merged, `time`, `text`, `value`); only answers that count are listed |
| `participation` | the participation table the PDF and Excel print: per person `cells` per activity, `total`, `early`, `late` (first and second half of the activities, labels in `early_label` / `late_label`), chat `messages` |
| `roster` | `name`, `role`, `present` (empty when the session has no roster) |
| `excluded_senders` | chat names the session does not count (a co-host or producer account); already left out of every count and answer |
| `merged_names` | the session's `aliases.yaml`: one person's several display names, already applied to every name in the document |
| `merge_suggestions` | likely merges still unconfirmed, as `{canonical, aliases}` |
| `playbook` | the text of `playbook.md`, or `null` |
| `notes` | the text of `notes.md` (the facilitator's own notes from the session), or `null` |

`went_live` false, or no activities → say so and stop: there is nothing to analyse.

## 3. Agree on what to analyse and in which shape

- With a playbook: summarise its *Purpose*, *What to analyse* and *Output format* sections and offer them as the defaults; the facilitator confirms or changes them.
- Without one: ask what to analyse and in which shape, and mention that a playbook (Sessions tab, Playbook card) makes these the defaults next time.
- The output is markdown unless the playbook or the facilitator asks for a Word document.
- Notes: with `notes` present, use them. Without, ask the facilitator for their notes from the session (pasted, or "none"); offer to save pasted notes as `notes.md` in the session folder so a later run reads them.

## 4. Identity hygiene, before any analysis

- `merged_names` are already applied. For each `merge_suggestions` entry, ask the facilitator to confirm it; also propose any other display names that plainly look like one person, and ask. Apply the accepted merges in the analysis. To keep them for the app's own exports, the facilitator merges them in the Results tab (it writes `aliases.yaml`); after that, re-run the dump.
- Leave out of the analysis: people whose roster `role` marks a facilitator, host, co-host or producer; anyone the playbook's *People to leave out* section names; and any sender the facilitator names when asked. The facilitator's own chat messages never reach the dump.
- State the final list of people analysed and the people left out before writing anything.

## 5. Analyse

These rules hold for every session; a playbook adds its own on top:

- Every statement about a person traces to their answers, their counts in `participation`, or the facilitator's notes. Nothing is invented.
- No over-reading: a single short answer supports a light remark, not a personality judgement.
- Where there is no evidence for a person (or for a section the output format asks about), write *not enough observation*.
- Answer text is quoted as the person wrote it; a translation, when the report's language differs, is marked as one.
- Counts come from `participation`; early versus late is its `early` and `late`, over the activity ranges its labels give.
- A person who was on the roster and `present` but never answered is listed with *not enough observation*, not left out.

## 6. Write the report

- Write to `report_dir` (create it when missing): `analysis-<YYYY-MM-DD>.md`, or `analysis-<YYYY-MM-DD>.docx` for a Word document, with the session's date. A file of that name already there is kept: the new one takes the first free suffix (`analysis-<date>-2.md`, `-3`, …).
- The report is the only file this skill writes there, and nothing of it goes into the repo: it holds personal data.
- Finish by telling the facilitator the full path of the report and the people left out.
