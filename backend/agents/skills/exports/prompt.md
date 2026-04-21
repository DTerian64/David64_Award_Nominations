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

1. Call `query_database` with an appropriate SELECT.
2. Call the export tool, passing `question`, `answer`, and the exact `rows`
   array returned in step 1.
3. In your answer, confirm what the export contains — do NOT include the
   download URL or any HTML links.
