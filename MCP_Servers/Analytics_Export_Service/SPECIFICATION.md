# Analytics Export MCP Server — Specification

## Overview
This MCP server provides tools to export analytics data (from "Ask Analytics AI") into structured Excel or PDF formats with professional formatting, charts, and styling.

## Architecture
- **Runtime**: Python 3.10+
- **MCP Framework**: mcp (Python SDK)
- **Export Libraries**: openpyxl (Excel), reportlab (PDF), pandas (data processing)
- **Purpose**: Convert LLM-generated analytics responses into downloadable reports

---

## MCP Tools

### 1. `export_to_excel`
Exports analytics data to a formatted Excel workbook.

**Parameters**:
```json
{
  "question": "string",           // User's analytics question
  "answer": "string",             // LLM-generated response
  "data_table": "array[object]",  // Optional: structured data rows
  "filename": "string"            // Optional: output filename (default: auto-generated)
}
```

**Response**:
```json
{
  "status": "success",
  "file_path": "string",          // Absolute path to generated .xlsx
  "file_size_bytes": "number",
  "rows_exported": "number"
}
```

**Features**:
- Blue header row with white text
- Auto-fitted column widths
- Data table with borders (if provided)
- Timestamp footer (generation time)
- Summary sheet with question and answer
- Data sheet (if applicable)

---

### 2. `export_to_pdf`
Exports analytics data to a formatted PDF report.

**Parameters**:
```json
{
  "question": "string",           // User's analytics question
  "answer": "string",             // LLM-generated response
  "data_table": "array[object]",  // Optional: structured data rows
  "filename": "string",           // Optional: output filename (default: auto-generated)
  "include_timestamp": "boolean"  // Optional: include generation timestamp (default: true)
}
```

**Response**:
```json
{
  "status": "success",
  "file_path": "string",          // Absolute path to generated .pdf
  "file_size_bytes": "number",
  "pages": "number"
}
```

**Features**:
- Professional header with title and timestamp
- Formatted question and answer sections
- Data table rendering (if provided)
- Page breaks for large datasets
- Standard margins and typography

---

### 3. `export_to_csv`
Lightweight CSV export for data-heavy responses.

**Parameters**:
```json
{
  "data_table": "array[object]",  // Structured data rows (required)
  "filename": "string"            // Optional: output filename
}
```

**Response**:
```json
{
  "status": "success",
  "file_path": "string",
  "file_size_bytes": "number",
  "rows_exported": "number"
}
```

---

### 4. `list_exported_files`
List previously generated export files.

**Parameters**:
```json
{
  "limit": "integer",             // Max results (default: 20)
  "format_filter": "string|null"  // Filter by 'xlsx', 'pdf', or null for all
}
```

**Response**:
```json
{
  "status": "success",
  "files": [
    {
      "filename": "string",
      "format": "string",
      "created_at": "ISO timestamp",
      "size_bytes": "number",
      "file_path": "string"
    }
  ],
  "total": "number"
}
```

---

## File Structure

Generated files are stored in:
```
<export_service_root>/
  exports/
    archive/          # Old files (>7 days)
    active/           # Current files
```

File naming convention:
```
analytics_export_<timestamp>_<format>.<ext>
# Example: analytics_export_20260222_143052_xlsx.xlsx
```

---

## Configuration (via .env)

```ini
# Export Destination
EXPORT_BASE_PATH=./exports
EXPORT_ARCHIVE_DAYS=7

# PDF Styling
PDF_PAGE_WIDTH=210      # mm (A4)
PDF_PAGE_HEIGHT=297     # mm (A4)
PDF_FONT_NAME=Helvetica
PDF_FONT_SIZE=11

# Excel Styling
EXCEL_HEADER_COLOR=4472C4      # Hex color code
EXCEL_AUTO_FIT=true
```

---

## Usage Example (Claude Desktop Integration)

```json
// claude_desktop_config.json
{
  "mcpServers": {
    "analytics-export": {
      "command": "python",
      "args": ["C:/path/to/Analytics_Export_Service/server.py"],
      "env": {
        "EXPORT_BASE_PATH": "./exports",
        "EXPORT_ARCHIVE_DAYS": "7"
      }
    }
  }
}
```

---

## Error Handling

All endpoints return structured error responses:

```json
{
  "status": "error",
  "error_code": "INVALID_DATA|IO_ERROR|PERMISSION_DENIED|etc",
  "message": "Human-readable error description"
}
```

---

## Security Considerations

1. Files are stored in isolated `exports/` directory
2. Automatic cleanup of files >7 days old
3. No direct access to source analytics data
4. All paths are validated and sanitized

---

