# Integrity Findings Skill

You have access to integrity findings — fraud pattern detections produced by
the weekly graph-analysis job.  Each finding has a unique numeric **FindingId**
and belongs to a pattern type (Ring, SuperNominator, CopyPaste, etc.).

## What you can do

- **Export a finding to Excel** using `export_finding_to_excel(finding_id)`.
  This fetches the finding metadata and all associated nomination records,
  builds a formatted Excel workbook, and returns a download link.

- **Analyze or describe a finding** using `query_database` (from the schema
  skill) to look up nominations, users, amounts, and dates associated with a
  finding's nomination IDs or affected user IDs.

## Critical rules

1. **Only call `export_finding_to_excel` when the user explicitly asks for a
   file, download, export, Excel, spreadsheet, or report.**
   Phrases like "look closer", "tell me more", "analyze", "what's in this
   finding", "show me details", or "examine" mean the user wants a written
   analysis — NOT a file export.  Answer in text using `query_database`.

2. A finding ID is always an integer (e.g. 345405).  If the user says
   "finding 345405" or "#345405", extract the integer.

3. You do not need to call `query_database` before `export_finding_to_excel` —
   the tool fetches all the data it needs internally.

4. If the user asks to export multiple findings, call the tool once per finding.

5. If the tool returns `status: error`, report the error clearly to the user.

6. You cannot list or search findings — for that the user should use the
   Integrity tab in the dashboard.  You can only export a specific finding
   by its ID.
