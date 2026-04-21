# Nominations SQL Agent — Python + Azure OpenAI

MCP server written in Python that converts natural language questions into T-SQL queries,
powered by Azure OpenAI.

## Setup

### 1. Python version
Requires Python 3.10+. Check with: python --version

### 2. Install dependencies

    pip install -r requirements.txt

### 3. Configure credentials

    cp .env.example .env
    # Edit .env with your Azure OpenAI values

### 4. Load .env in server (add to top of server.py if not already there)

    from dotenv import load_dotenv
    load_dotenv()

### 5. Start the server

    python server.py

---

## Wire into Claude Desktop (claude_desktop_config.json)

    {
      "mcpServers": {
        "nominations-sql-agent": {
          "command": "python",
          "args": ["C:/path/to/nominations-agent-python/server.py"],
          "env": {
            "AZURE_OPENAI_ENDPOINT": "https://your-resource.openai.azure.com",
            "AZURE_OPENAI_API_KEY": "your-key-here",
            "AZURE_OPENAI_DEPLOYMENT": "gpt-4o",
            "AZURE_OPENAI_API_VERSION": "2024-08-01-preview"
          }
        }
      }
    }

On Windows, use the full path to python.exe if needed:
    "command": "C:/Users/you/AppData/Local/Programs/Python/Python311/python.exe"

---

## MCP Tools (identical to JS version)

    generate_sql          — single NL question → T-SQL
    generate_sql_batch    — multiple questions → multiple T-SQL queries
    get_template_queries  — pre-built queries for common patterns
    validate_sql_intent   — sanity-check a query vs the original question

---

## JavaScript vs Python — what's different

| Aspect            | JavaScript version         | Python version              |
|-------------------|----------------------------|-----------------------------|
| Runtime           | Node.js 18+                | Python 3.10+                |
| Package manager   | npm                        | pip                         |
| MCP library       | @modelcontextprotocol/sdk  | mcp                         |
| Azure OpenAI      | openai npm package         | openai pip package          |
| Async model       | async/await + Promise      | asyncio + async/await       |
| Config file       | package.json               | requirements.txt            |
| Start command     | node server.js             | python server.py            |

The AI behavior, tools, and SQL output are identical between both versions.
