# Integrity Findings Skill

You have access to integrity findings — fraud pattern detections produced by
the weekly graph-analysis job.  Each finding has a unique numeric **FindingId**
and belongs to a pattern type (Ring, SuperNominator, CopyPaste, etc.).

## What you can do

- **Export a finding to Excel** using `export_finding_to_excel(finding_id)`.
  This fetches the finding metadata and all associated nomination records,
  builds a formatted Excel workbook, and returns a download link.

## Rules

1. A finding ID is always an integer (e.g. 345405).  If the user says
   "finding 345405" or "#345405", extract the integer and call the tool.
2. You do not need to call `query_database` before `export_finding_to_excel` —
   the tool fetches all the data it needs internally.
3. If the user asks to export multiple findings, call the tool once per finding.
4. If the tool returns `status: error`, report the error clearly to the user.
5. You cannot list or search findings — for that the user should use the
   Integrity tab in the dashboard.  You can only export a specific finding
   by its ID.
