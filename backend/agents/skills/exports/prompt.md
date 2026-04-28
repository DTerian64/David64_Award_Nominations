# Exports Skill — Excel, PDF, and CSV Tool Guidance

Use the export tools when the user explicitly asks for a file download,
spreadsheet, report, or CSV. Never export unless the user requests it.

## Tool: export_to_excel
Generate an Excel (.xlsx) workbook from query results. Use when the user asks
for a spreadsheet, Excel file, or workbook. Always call `query_database` first
and pass its rows into this tool.

## Tool: export_to_pdf
Generate a PDF report. Use when the user asks for a PDF, printable report, or
document. Always call `query_database` first and pass its rows. Never call with
an empty rows array.

## Tool: export_to_csv
Generate a raw CSV file. Use when the user asks for a CSV, data download, or
comma-separated file. Pass the rows from `query_database`.

## Workflow

1. Call `query_database` with an appropriate SELECT to fetch the data rows.
2. Call the export tool, passing `question`, `answer`, and the exact `rows`
   array returned in step 1.
3. In your answer, confirm what the export contains — do NOT include the
   download URL or any HTML links.

## Critical Rules

### Always re-query before exporting
Even if the data was already displayed in a previous response (e.g. shown as
a table in the chat), you MUST call `query_database` again before calling any
export tool. Do NOT rely on previously fetched data or data returned by other
tools such as fraud analysis — re-run the same SQL query to get a fresh `rows`
array. Skipping this step results in an empty export.

### Never export with empty rows
Never call `export_to_excel`, `export_to_pdf`, or `export_to_csv` with an
empty or missing `rows` array. If `query_database` returns no rows, tell the
user there is no data to export rather than producing an empty file.

### rows must contain actual records
The `rows` parameter must be the raw list of record objects from
`query_database`. It is NOT a summary, count, or description — it is the
complete set of data records that will form the spreadsheet/PDF/CSV body.
The `answer` field is only a natural-language summary shown alongside the data;
it does not replace `rows`.
