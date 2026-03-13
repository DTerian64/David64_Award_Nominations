# Analytics Export Service — MCP Server

Export "Ask Analytics AI" responses to professional Excel, PDF, and CSV reports.

## Features

✅ **Export Formats**
- Excel (.xlsx) with formatting, borders, headers
- PDF with professional layout, timestamps, pagination
- CSV for data-heavy responses

✅ **Built-in Styling**
- Blue header rows (matching your brand)
- Auto-fitted columns (Excel)
- Proper typography and spacing (PDF)
- Timestamps on all exports

✅ **Data Management**
- Automatic file cleanup after N days
- Archive old files separately
- List and track all exports

✅ **Integration-Ready**
- Standalone MCP server
- Works with Claude Desktop
- Compatible with your existing SQL Agent MCP

---

## Quick Start

### 1. Prerequisites
- Python 3.10+
- pip

### 2. Install Dependencies

```bash
cd Analytics_Export_Service
pip install -r requirements.txt
```

### 3. Configure (Optional)

```bash
cp .env.example .env
# Edit .env if you want custom paths or styling
```

### 4. Start Server

```bash
python server.py
```

You'll see:
```
INFO:__main__:Starting Analytics Export MCP Server...
INFO:__main__:Export directory: ./exports/active
INFO:__main__:Server started and ready for requests
```

---

## Wire into Claude Desktop

Edit `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "analytics-export": {
      "command": "python",
      "args": ["C:/path/to/Analytics_Export_Service/server.py"],
      "env": {
        "EXPORT_BASE_PATH": "./exports",
        "EXPORT_ARCHIVE_DAYS": "7"
      }
    },
    "nominations-sql-agent": {
      "command": "python",
      "args": ["C:/path/to/Nominations_SQL_Agent/server.py"],
      ... existing config ...
    }
  }
}
```

**On Windows**: Use the full path to your Python executable if needed:
```json
"command": "C:/Users/David/AppData/Local/Programs/Python/Python311/python.exe"
```

---

## Usage Examples

### Example 1: Excel Export

**Input:**
```json
{
  "tool": "export_to_excel",
  "question": "Which departments have the highest award spending?",
  "answer": "Based on the analytics data:\n\n1. Engineering: $45,000\n2. Sales: $38,000\n3. Operations: $22,000",
  "data_table": [
    {"department": "Engineering", "spending": 45000, "headcount": 120},
    {"department": "Sales", "spending": 38000, "headcount": 95},
    {"department": "Operations", "spending": 22000, "headcount": 60}
  ]
}
```

**Output:**
```json
{
  "status": "success",
  "file_path": "C:\\Users\\David\\...\\exports\\active\\analytics_export_20260222_143052_xlsx.xlsx",
  "file_size_bytes": 8192,
  "rows_exported": 3
}
```

**Result**: Opens in Excel with:
- Summary sheet (question + answer)
- Data sheet (formatted table with headers)
- Auto-fitted columns, blue headers

---

### Example 2: PDF Export

**Input:**
```json
{
  "tool": "export_to_pdf",
  "question": "How has our approval rate changed recently?",
  "answer": "Approval rates have improved significantly:\n\n- Last month: 78%\n- This month: 85%\n- Trend: +7 percentage points\n\nKey factors: Improved process efficiency and faster reviews.",
  "include_timestamp": true
}
```

**Output:**
```json
{
  "status": "success",
  "file_path": "C:\\Users\\David\\...\\exports\\active\\analytics_export_20260222_143105_pdf.pdf",
  "file_size_bytes": 15420,
  "pages": 1
}
```

**Result**: PDF with:
- Title and timestamp
- Formatted question section
- Multi-line answer with proper wrapping
- Professional layout (A4, margins, fonts)

---

### Example 3: CSV Export

**Input:**
```json
{
  "tool": "export_to_csv",
  "data_table": [
    {"region": "East", "sales": 125000, "awards": 12},
    {"region": "West", "sales": 98000, "awards": 9},
    {"region": "Central", "sales": 76000, "awards": 7}
  ],
  "filename": "regional_summary"
}
```

**Output:**
```json
{
  "status": "success",
  "file_path": "C:\\Users\\David\\...\\exports\\active\\regional_summary.csv",
  "file_size_bytes": 256,
  "rows_exported": 3
}
```

---

### Example 4: List Exported Files

**Input:**
```json
{
  "tool": "list_exported_files",
  "limit": 10,
  "format_filter": "xlsx"
}
```

**Output:**
```json
{
  "status": "success",
  "files": [
    {
      "filename": "analytics_export_20260222_143052_xlsx.xlsx",
      "format": "xlsx",
      "created_at": "2026-02-22T14:30:52.123456",
      "size_bytes": 8192,
      "file_path": "C:\\Users\\David\\...\\exports\\active\\..."
    }
  ],
  "total": 1
}
```

---

## Workflow: Full Example

**User asks analytics question in your app:**

```
Question: "What patterns do you see in our fraud alerts?"
```

**Your FastAPI backend:**

1. Calls `/api/admin/analytics/ask` → gets LLM response
2. Response includes structured data: `{question, answer, pattern_data[]}`

3. (*Next sprint*) Backend calls analytics-export MCP:
   ```python
   # Option A: Via Claude
   result = claude.call_tool(
       "export_to_excel",
       {
           "question": question,
           "answer": answer,
           "data_table": pattern_data
       }
   )
   ```

4. MCP returns file path
5. Backend returns download link to frontend
6. User downloads professional report

---

## File Structure

```
exports/
├── active/
│   ├── analytics_export_20260222_143052_xlsx.xlsx
│   ├── analytics_export_20260222_143105_pdf.pdf
│   └── analytics_export_20260222_150022_csv.csv
└── archive/
    ├── analytics_export_20260215_100000_xlsx.xlsx   (>7 days old)
    └── ...
```

**Auto-cleanup:** Files > 7 days old automatically move to `archive/`
(Configurable via `EXPORT_ARCHIVE_DAYS` env var)

---

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `EXPORT_BASE_PATH` | `./exports` | Where to store files |
| `EXPORT_ARCHIVE_DAYS` | `7` | Days before archiving |
| `PDF_PAGE_WIDTH` | `210` | Page width in mm (A4) |
| `PDF_PAGE_HEIGHT` | `297` | Page height in mm (A4) |
| `PDF_FONT_NAME` | `Helvetica` | PDF font |
| `PDF_FONT_SIZE` | `11` | PDF font size (pt) |
| `EXCEL_HEADER_COLOR` | `4472C4` | Hex color for Excel headers |
| `EXCEL_AUTO_FIT` | `true` | Auto-fit Excel columns |

Create `.env` file:
```bash
cp .env.example .env
# Edit as needed
```

---

## Integration with Your Analytics Flow

### Current Pipeline
```
Frontend (AnalyticsDashboard)
  ↓
Backend (/api/admin/analytics/ask)
  ↓
Azure OpenAI (creates answer)
  ↓
Response: {question, answer}
```

### Enhanced Pipeline (Next Sprint)
```
Frontend (AnalyticsDashboard)
  ↓
Backend (/api/admin/analytics/ask)
  ↓
Azure OpenAI (creates answer)
  ↓
[NEW] Analytics Export MCP
  ├→ export_to_excel()
  ├→ export_to_pdf()
  └→ export_to_csv()
  ↓
Response: {question, answer, export_url: "/downloads/file.xlsx"}
  ↓
Frontend (Add Download Button)
```

### Backend Code Change (Minor - Next Sprint)

In **`backend/main.py`** after analytics response:

```python
@app.post("/api/admin/analytics/ask")
async def ask_analytics_question(req: AnalyticsQuestion, ...):
    # ... existing code ...
    
    response = {
        "question": req.question,
        "answer": response.choices[0].message.content,
        # NEW: Add export capability
        "export_formats": {
            "excel": "/api/admin/analytics/export/xlsx",
            "pdf": "/api/admin/analytics/export/pdf"
        }
    }
    
    return response
```

Or store response + let user click "Export" afterwards (cleaner UX).

---

## Troubleshooting

### Server won't start

1. Check Python version: `python --version` (need 3.10+)
2. Check dependencies: `pip list | findstr openpyxl reportlab pandas`
3. Check permissions: Export directory must be writable

### MCP not appearing in Claude Desktop

1. Verify path in `claude_desktop_config.json` is correct
2. Restart Claude Desktop
3. Check logs: Look for errors in Claude output panel

### Files not generating

1. Check export directory exists: `ls exports/active`
2. Check disk space
3. Check logs for error messages
4. Verify data_table format is valid

### PDF looks wrong

- Adjust `PDF_FONT_SIZE`, `PDF_PAGE_WIDTH` in `.env`
- Answer text with long lines? Check `Paragraph.wrap_text`

---

## Advanced: Calling from Backend

If you want your backend to directly invoke the export MCP:

```python
# backend/main.py addition

import subprocess
import json

def call_export_mcp(tool_name: str, args: dict) -> dict:
    """Call analytics-export MCP directly."""
    # This invokes the MCP server in-process
    # Requires running MCP server separately
    
    # Alternative: Use HTTP interface if you add FastAPI wrapper
    # (More complex, not needed for initial sprint)
    pass
```

For now, **recommend:** Keep MCP standalone, add optional "Export" button in frontend.

---

## Next Steps

1. **Install & test locally**: Run `python server.py`, verify exports work
2. **Wire to Claude Desktop**: Add to config, test with sample questions
3. **Plan frontend UI**: Add "Export" button to AnalyticsDashboard
4. **Plan backend integration**: Decide if backend calls MCP or frontend does
5. **Deploy**: Configure path on production server, update config

---

## Support

- **MCP Specification**: See `SPECIFICATION.md`
- **Logs**: Check console output or redirect to file
- **File Issues**: All exports land in `./exports/active/` for inspection

