# Integration Guide: Analytics Export MCP

Step-by-step guide to integrate the Analytics Export Service into your Award Nomination app.

---

## Phase 1: Setup (No Code Changes)

### Step 1: Install & Test

```bash
cd Award_Nomination_App/MCP_Servers/Analytics_Export_Service

# Install dependencies
pip install -r requirements.txt

# Start server (test run)
python server.py
```

Should see:
```
INFO:__main__:Starting Analytics Export MCP Server...
INFO:__main__:Export directory: ./exports/active
INFO:__main__:Server started and ready for requests
```

**Press Ctrl+C to stop.**

### Step 2: Test with Sample Data

Create a test script `test_export.py` in the service directory:

```python
import asyncio
import json
from server import export_to_excel, export_to_pdf, export_to_csv

async def test():
    # Test Excel
    result = export_to_excel(
        question="Which departments have highest spending?",
        answer="Engineering: $45K, Sales: $38K",
        data_table=[
            {"dept": "Eng", "spend": 45000},
            {"dept": "Sales", "spend": 38000}
        ]
    )
    print("Excel:", json.dumps(result, indent=2))
    
    # Test PDF
    result = export_to_pdf(
        question="Approval rate trend?",
        answer="Rate improved from 78% to 85%"
    )
    print("PDF:", json.dumps(result, indent=2))
    
    # Check files
    result = list_exported_files(limit=5)
    print("Files:", json.dumps(result, indent=2))

if __name__ == "__main__":
    asyncio.run(test())
```

Run: `python test_export.py`

Verify `./exports/active/` has test files.

---

## Phase 2: Claude Desktop Integration

### Step 3: Add to Claude Config

Edit your **`claude_desktop_config.json`**:

```json
{
  "mcpServers": {
    "analytics-export": {
      "command": "python",
      "args": ["C:/Users/David/source/repos/David64_Award_Nominations/Award_Nomination_App/MCP_Servers/Analytics_Export_Service/server.py"],
      "env": {
        "EXPORT_BASE_PATH": "C:/Users/David/source/repos/David64_Award_Nominations/Award_Nomination_App/MCP_Servers/Analytics_Export_Service/exports",
        "EXPORT_ARCHIVE_DAYS": "7"
      }
    },
    "nominations-sql-agent": {
      "command": "python",
      "args": ["C:/Users/David/source/repos/David64_Award_Nominations/Award_Nomination_App/MCP_Servers/Nominations_SQL_Agent/server.py"],
      ... rest of existing config ...
    }
  }
}
```

**Important**: Use absolute paths on Windows.

### Step 4: Restart Claude Desktop

- Close Claude completely
- Reopen Claude
- Check that both MCPs appear in the tool list

In Claude, try:
```
Can you list available tools in the analytics-export MCP?
```

Should show: `export_to_excel`, `export_to_pdf`, etc.

---

## Phase 3: Manual Testing (Claude)

### Step 5: Test Export from Claude

In Claude, try:

```
Please export this analytics response to Excel:

Question: "Which departments have the highest award spending?"

Answer: "Based on our analysis:
1. Engineering: $45,000 (120 employees)
2. Sales: $38,000 (95 employees)  
3. Operations: $22,000 (60 employees)"

Data:
[
  {"department": "Engineering", "spending": 45000, "headcount": 120},
  {"department": "Sales", "spending": 38000, "headcount": 95},
  {"department": "Operations", "spending": 22000, "headcount": 60}
]

Use the export_to_excel tool with these parameters.
```

Claude will call the tool and return the file path.

---

## Phase 4: Frontend Enhancement (Optional Now, Plan for Next Sprint)

### Step 6: Add Export Button to AnalyticsDashboard

Location: `frontend/src/components/AnalyticsDashboard.tsx`

**Current UI** (around line 426):
```tsx
{aiResponse && (
  <div className="space-y-3 mt-6">
    <div className="bg-blue-50 rounded-lg border border-blue-200 p-4">
      <p className="text-sm font-semibold text-blue-900 mb-2">Your question:</p>
      <p className="text-blue-800">{aiResponse.question}</p>
    </div>
    
    <div className="bg-green-50 rounded-lg border border-green-200 p-4">
      <p className="text-sm font-semibold text-green-900 mb-2">AI Response:</p>
      <div className="text-green-800 whitespace-pre-wrap text-sm leading-relaxed">
        {aiResponse.answer}
      </div>
    </div>
  </div>
)}
```

**Enhanced version** (add export buttons):
```tsx
{aiResponse && (
  <div className="space-y-3 mt-6">
    <div className="bg-blue-50 rounded-lg border border-blue-200 p-4">
      <p className="text-sm font-semibold text-blue-900 mb-2">Your question:</p>
      <p className="text-blue-800">{aiResponse.question}</p>
    </div>
    
    <div className="bg-green-50 rounded-lg border border-green-200 p-4">
      <p className="text-sm font-semibold text-green-900 mb-2">AI Response:</p>
      <div className="text-green-800 whitespace-pre-wrap text-sm leading-relaxed">
        {aiResponse.answer}
      </div>
    </div>

    {/* NEW: Export Buttons */}
    <div className="flex gap-2 mt-4">
      <button
        onClick={() => handleExport('excel')}
        disabled={exportLoading}
        className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:bg-gray-400 flex items-center gap-2"
      >
        📊 Export as Excel
      </button>
      <button
        onClick={() => handleExport('pdf')}
        disabled={exportLoading}
        className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:bg-gray-400 flex items-center gap-2"
      >
        📄 Export as PDF
      </button>
    </div>
  </div>
)}
```

**Add state & handler**:
```tsx
const [exportLoading, setExportLoading] = useState(false);

const handleExport = async (format: 'excel' | 'pdf') => {
  if (!aiResponse) return;
  
  try {
    setExportLoading(true);
    const response = await fetch(`${API_BASE_URL}/api/admin/analytics/export/${format}`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${await getAccessToken()}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        question: aiResponse.question,
        answer: aiResponse.answer
      })
    });
    
    if (!response.ok) throw new Error('Export failed');
    
    const data = await response.json();
    // Trigger download or show success message
    console.log('Export successful:', data.file_path);
  } catch (err) {
    console.error('Export error:', err);
  } finally {
    setExportLoading(false);
  }
};
```

---

## Phase 5: Backend Integration (Optional, Plan for Later)

### Step 7: Add Export Endpoint to FastAPI Backend

Location: `backend/main.py` (around line 1100, after `ask_analytics_question`)

```python
@app.post("/api/admin/analytics/export/{format}")
async def export_analytics(
    format: str,
    req: ExportRequest,  # question, answer
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin"))
):
    """Export analytics response to Excel or PDF"""
    
    if format not in ("excel", "pdf"):
        raise HTTPException(status_code=400, detail="Format must be 'excel' or 'pdf'")
    
    try:
        # Call export MCP (would need HTTP wrapper around MCP server)
        # For now, this is deferred to next sprint
        
        logger.info(f"Analytics export requested: {format}")
        
        # Placeholder: return success
        return {
            "status": "success",
            "file_path": "/path/to/export.xlsx",
            "format": format
        }
    except Exception as e:
        logger.error(f"Export failed: {e}")
        raise HTTPException(status_code=500, detail="Export failed")
```

Add model:
```python
class ExportRequest(BaseModel):
    question: str
    answer: str
```

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────┐
│ Frontend (React)                                         │
│ AnalyticsDashboard.tsx                                  │
│ - Ask question → Button → [Export as Excel/PDF] ←─┐   │
└────────────────┬────────────────────────────────────┼───┘
                 │                                    │
                 v                                    │
┌────────────────────────────────────────────────────────┐│
│ Backend (FastAPI)                                      ││
│ main.py                                                ││
│ /api/admin/analytics/ask → Azure OpenAI              ││
│ /api/admin/analytics/export/{format} ← export MCP ←──┘│
└────────────────┬────────────────────────────────────────┘
                 │
                 v
┌────────────────────────────────────────────────────────┐
│ Analytics Export MCP Server (New)                      │
│ server.py                                              │
│                                                        │
│ Tools:                                                 │
│  • export_to_excel()                                  │
│  • export_to_pdf()                                    │
│  • export_to_csv()                                    │
│  • list_exported_files()                              │
└────────────────┬────────────────────────────────────────┘
                 │
                 v
┌────────────────────────────────────────────────────────┐
│ File System                                            │
│ exports/active/   ← Generated files                    │
│ exports/archive/  ← Old files (>7 days)                │
└────────────────────────────────────────────────────────┘
```

---

## Deployment Checklist

- [ ] **Local Testing**: Run MCP server locally, test exports
- [ ] **Claude Desktop**: Add to config, verify MCP appears
- [ ] **Manual Testing**: Export sample analytics in Claude
- [ ] **Frontend Update**: Add export buttons (optional now)
- [ ] **Backend Integration**: Add export endpoint (later sprint)
- [ ] **Production Deployment**: Deploy MCP server to prod machine
- [ ] **Monitor**: Check export directory for file generation

---

## Configuration for Production

On your production deployment machine:

```bash
# Create production export directory
mkdir -p /opt/award-analytics/exports/{active,archive}
chmod 755 /opt/award-analytics/exports

# Clone MCP server
cp -r Analytics_Export_Service /opt/award-analytics/

# Create .env for production
cat > /opt/award-analytics/Analytics_Export_Service/.env << EOF
EXPORT_BASE_PATH=/opt/award-analytics/exports
EXPORT_ARCHIVE_DAYS=7
EOF

# Start as service (using systemd, Docker, or your deployment method)
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| MCP not appearing in Claude | Restart Claude Desktop, check path in config |
| Files not generating | Check export directory permissions, disk space |
| PDF looks wrong | Adjust font size in .env |
| Missing data in Excel | Check data_table format (must be list of dicts) |

---

## Summary

**No code changes required to start:**
1. Install requirements
2. Add to Claude Desktop config
3. Test with Claude

**When ready for UI integration (next sprint):**
1. Add export buttons to AnalyticsDashboard
2. Add backend endpoint (optional wrapper)
3. Deploy MCP to production

