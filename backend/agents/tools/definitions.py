from openai.types.chat import ChatCompletionToolParam
"""
agents/tools/definitions.py
────────────────────────────
OpenAI tool/function schemas passed to the chat completions API.

Each entry in TOOLS matches a callable in registry.py.
The LLM decides which tools to call; the agent loop executes them.
"""

TOOLS: list[ChatCompletionToolParam] = [
   {
    "type": "function",
    "function": {
        "name": "query_database",
        "description": (
            "Execute a T-SQL SELECT query against the award nomination database and return the results. "
            "You must write the T-SQL yourself based on the schema provided in your instructions. "
            "Only use SELECT — never INSERT, UPDATE, DELETE, DROP, ALTER, EXEC, TRUNCATE, or MERGE."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "A valid T-SQL SELECT query to execute. No semicolons. No markdown."
                }
            },
            "required": ["sql"]
        }
    }
},
    {
        "type": "function",
        "function": {
            "name": "get_analytics_overview",
            "description": (
                "Retrieve a broad analytics summary: totals, approval rates, diversity metrics, "
                "top recipients/nominators, and department breakdown. "
                "Use this for open-ended or high-level questions that don't map cleanly to a "
                "single SQL query, or as supplementary context."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "export_to_excel",
            "description": (
                "Generate an Excel (.xlsx) file containing the query results and analysis, "
                "upload it to blob storage, and return a download URL. "
                "Use this when the user explicitly asks for an Excel file, spreadsheet, or workbook."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The original user question (written into the export header)."
                    },
                    "answer": {
                        "type": "string",
                        "description": "The analysis/answer text to include in the export."
                    },
                    "rows": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Data rows as list of dicts. Use the rows from query_database if available."
                    },
                    "filename": {
                        "type": "string",
                        "description": "Optional custom filename without extension."
                    }
                },
                "required": ["question", "answer", "rows"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "export_to_pdf",
            "description": (
                 "Generate a PDF report and upload to blob storage. "
                 "You MUST pass the rows returned from query_database into this call, if any, so they can be included in the PDF. "
                 "Never call this tool without first calling query_database and passing its rows here."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The original user question (written into the report header)."
                    },
                    "answer": {
                        "type": "string",
                        "description": "The analysis/answer text to include in the report."
                    },
                    "rows": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Optional data rows to include as a table in the PDF."
                    },
                    "filename": {
                        "type": "string",
                        "description": "Optional custom filename without extension."
                    }
                },
                "required": ["question", "answer", "rows"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "export_to_csv",
            "description": (
                "Generate a CSV file from query results, upload to blob storage, "
                "and return a download URL. "
                "Use this when the user asks for a CSV, raw data download, or comma-separated file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "rows": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Data rows as list of dicts. Must have run query_database first."
                    },
                    "filename": {
                        "type": "string",
                        "description": "Optional custom filename without extension."
                    }
                },
                "required": ["rows"]
            }
        }
    }
]