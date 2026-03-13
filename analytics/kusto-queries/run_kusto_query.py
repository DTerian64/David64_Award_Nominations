#!/usr/bin/env python3
"""
run_kusto_query.py
Description: Run Kusto queries programmatically using Azure SDK
Usage: python run_kusto_query.py <query-file.kql>
"""

import sys
import json
from pathlib import Path
from azure.identity import DefaultAzureCredential
from azure.monitor.query import LogsQueryClient, LogsQueryStatus
from datetime import timedelta

# Configuration
WORKSPACE_ID = "290020d5-7bb8-4faa-a901-b5da4ad250d7"  # Your workspace ID

def run_kusto_query(query_file: str, output_format: str = "table"):
    """
    Run a Kusto query from a file
    
    Args:
        query_file: Path to .kql file
        output_format: Output format (table, json, csv)
    """
    
    # Read query from file
    query_path = Path(query_file)
    if not query_path.exists():
        print(f"❌ Error: Query file not found: {query_file}")
        sys.exit(1)
    
    with open(query_path, 'r') as f:
        query = f.read()
    
    # Remove comment lines for cleaner output
    query_lines = [line for line in query.split('\n') 
                   if not line.strip().startswith('//')]
    query = '\n'.join(query_lines)
    
    print(f"📊 Running query: {query_path.name}")
    print(f"🏢 Workspace ID: {WORKSPACE_ID}")
    print()
    
    # Authenticate
    credential = DefaultAzureCredential()
    client = LogsQueryClient(credential)
    
    try:
        # Execute query
        response = client.query_workspace(
            workspace_id=WORKSPACE_ID,
            query=query,
            timespan=timedelta(days=7)  # Maximum timespan
        )
        
        if response.status == LogsQueryStatus.SUCCESS:
            # Process results
            if output_format == "json":
                print_json(response)
            elif output_format == "csv":
                print_csv(response)
            else:
                print_table(response)
            
            print(f"\n✅ Query completed successfully")
            print(f"📊 Rows returned: {len(response.tables[0].rows)}")
            
        else:
            print(f"❌ Query failed with status: {response.status}")
            if response.partial_error:
                print(f"Error: {response.partial_error}")
            sys.exit(1)
    
    except Exception as e:
        print(f"❌ Error executing query: {e}")
        sys.exit(1)


def print_table(response):
    """Print results in table format"""
    table = response.tables[0]
    
    # Print header
    headers = [col.name for col in table.columns]
    header_line = " | ".join(headers)
    separator = "-" * len(header_line)
    
    print(header_line)
    print(separator)
    
    # Print rows
    for row in table.rows:
        row_values = [str(val) if val is not None else "" for val in row]
        print(" | ".join(row_values))


def print_json(response):
    """Print results in JSON format"""
    table = response.tables[0]
    headers = [col.name for col in table.columns]
    
    results = []
    for row in table.rows:
        row_dict = dict(zip(headers, row))
        results.append(row_dict)
    
    print(json.dumps(results, indent=2, default=str))


def print_csv(response):
    """Print results in CSV format"""
    table = response.tables[0]
    
    # Print header
    headers = [col.name for col in table.columns]
    print(",".join(headers))
    
    # Print rows
    for row in table.rows:
        row_values = [f'"{val}"' if val is not None else "" for val in row]
        print(",".join(row_values))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python run_kusto_query.py <query-file.kql> [output-format]")
        print("Output formats: table (default), json, csv")
        print()
        print("Examples:")
        print("  python run_kusto_query.py kusto-queries/errors/recent-errors.kql")
        print("  python run_kusto_query.py kusto-queries/fraud/high-risk.kql json")
        sys.exit(1)
    
    query_file = sys.argv[1]
    output_format = sys.argv[2] if len(sys.argv) > 2 else "table"
    
    run_kusto_query(query_file, output_format)
