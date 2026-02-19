"""
Azure Container Apps Log Analysis Script
Downloads and analyzes logs to check for errors, failures, and performance issues
"""

import subprocess
import json
import re
import os
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from dotenv import load_dotenv
import sys

# Load environment variables
load_dotenv()

def get_container_app_logs(app_name, resource_group, hours_back=24, tail=10000):
    """
    Download logs from Azure Container App
    
    Args:
        app_name: Name of the container app
        resource_group: Resource group name
        hours_back: How many hours of logs to fetch
        tail: Max number of log lines to fetch
    
    Returns:
        str: Log content
    """
    print(f"📥 Downloading logs from {app_name}...")
    print(f"   Time range: Last {hours_back} hours")
    print(f"   Max lines: {tail}")
    
    # Find Azure CLI executable (handle Windows vs Unix)
    import platform
    import shutil
    
    # Try to find az command
    az_cmd = shutil.which("az")
    
    if not az_cmd:
        # On Windows, try common installation paths
        if platform.system() == "Windows":
            possible_paths = [
                r"C:\Program Files (x86)\Microsoft SDKs\Azure\CLI2\wbin\az.cmd",
                r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd",
                os.path.expanduser(r"~\AppData\Local\Programs\Microsoft\Azure CLI\wbin\az.cmd"),
            ]
            for path in possible_paths:
                if os.path.exists(path):
                    az_cmd = path
                    break
    
    if not az_cmd:
        print("❌ Azure CLI not found.")
        print()
        print("Please install Azure CLI:")
        print("  Windows: https://aka.ms/installazurecliwindows")
        print("  Mac:     brew install azure-cli")
        print("  Linux:   curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash")
        print()
        print("After installing, you may need to:")
        print("  1. Close and reopen your terminal/command prompt")
        print("  2. Run: az login")
        return None
    
    print(f"   Using Azure CLI: {az_cmd}")
    
    # Build Azure CLI command
    cmd = [
        az_cmd, "containerapp", "logs", "show",
        "--name", app_name,
        "--resource-group", resource_group,
        "--tail", str(tail),
        "--follow", "false"
    ]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode != 0:
            print(f"❌ Error downloading logs:")
            print(f"   {result.stderr}")
            
            # Check if it's an authentication issue
            if "az login" in result.stderr.lower() or "not logged in" in result.stderr.lower():
                print()
                print("💡 You need to login to Azure:")
                print("   Run: az login")
                print("   Then try again")
            
            return None
        
        logs = result.stdout
        print(f"✅ Downloaded {len(logs.splitlines())} log lines\n")
        return logs
        
    except subprocess.TimeoutExpired:
        print("❌ Timeout while downloading logs (took >60 seconds)")
        print("   Try reducing the --tail value or checking your network connection")
        return None
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return None


def analyze_logs(logs):
    """
    Analyze logs for errors, failures, and performance metrics
    
    Args:
        logs: String containing log content
    
    Returns:
        dict: Analysis results
    """
    print("="*70)
    print("LOG ANALYSIS")
    print("="*70)
    
    lines = logs.splitlines()
    
    # Metrics
    metrics = {
        "total_lines": len(lines),
        "http_requests": defaultdict(int),  # By status code
        "endpoints": defaultdict(int),
        "errors": [],
        "warnings": [],
        "slow_requests": [],
        "status_codes": Counter(),
        "methods": Counter(),
        "response_times": [],
        "timestamps": [],
    }
    
    # Patterns to match
    patterns = {
        # HTTP requests: "GET /api/users 200 125ms"
        "http": re.compile(r'(GET|POST|PUT|DELETE|PATCH)\s+(/[^\s]+)\s+(\d{3})\s+(\d+)ms'),
        
        # Timestamp: "2026-02-16 14:01:59"
        "timestamp": re.compile(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})'),
        
        # Error patterns
        "error": re.compile(r'(ERROR|error|Error|Exception|exception|Failed|failed|FAILED)', re.IGNORECASE),
        
        # Warning patterns
        "warning": re.compile(r'(WARNING|warning|Warning|WARN|warn)', re.IGNORECASE),
        
        # Status codes in text
        "status_code": re.compile(r'\b(2\d{2}|3\d{2}|4\d{2}|5\d{2})\b'),
        
        # Response times
        "response_time": re.compile(r'(\d+)ms'),
    }
    
    print(f"\n📊 Processing {len(lines)} log lines...\n")
    
    for i, line in enumerate(lines):
        # Extract timestamp
        ts_match = patterns["timestamp"].search(line)
        if ts_match:
            metrics["timestamps"].append(ts_match.group(1))
        
        # Parse HTTP requests
        http_match = patterns["http"].search(line)
        if http_match:
            method, endpoint, status, duration = http_match.groups()
            
            status = int(status)
            duration = int(duration)
            
            metrics["methods"][method] += 1
            metrics["status_codes"][status] += 1
            metrics["endpoints"][endpoint] += 1
            metrics["response_times"].append(duration)
            metrics["http_requests"][status] += 1
            
            # Flag slow requests (> 1000ms)
            if duration > 1000:
                metrics["slow_requests"].append({
                    "line": i + 1,
                    "method": method,
                    "endpoint": endpoint,
                    "duration": duration,
                    "text": line.strip()
                })
        
        # Check for errors
        if patterns["error"].search(line):
            metrics["errors"].append({
                "line": i + 1,
                "text": line.strip()
            })
        
        # Check for warnings
        if patterns["warning"].search(line):
            metrics["warnings"].append({
                "line": i + 1,
                "text": line.strip()
            })
    
    return metrics


def print_analysis(metrics):
    """Print formatted analysis results"""
    
    print("="*70)
    print("SUMMARY")
    print("="*70)
    
    # Time range
    if metrics["timestamps"]:
        print(f"\n⏰ Time Range:")
        print(f"   Start: {metrics['timestamps'][0]}")
        print(f"   End:   {metrics['timestamps'][-1]}")
    
    # HTTP Status Codes
    print(f"\n📊 HTTP Status Codes:")
    total_requests = sum(metrics["status_codes"].values())
    
    if total_requests > 0:
        print(f"   Total Requests: {total_requests}")
        print()
        
        # Group by category
        success_2xx = sum(count for code, count in metrics["status_codes"].items() if 200 <= code < 300)
        redirect_3xx = sum(count for code, count in metrics["status_codes"].items() if 300 <= code < 400)
        client_4xx = sum(count for code, count in metrics["status_codes"].items() if 400 <= code < 500)
        server_5xx = sum(count for code, count in metrics["status_codes"].items() if 500 <= code < 600)
        
        if success_2xx:
            pct = (success_2xx / total_requests) * 100
            print(f"   ✅ 2xx Success:     {success_2xx:5d} ({pct:5.1f}%)")
        
        if redirect_3xx:
            pct = (redirect_3xx / total_requests) * 100
            print(f"   ↪️  3xx Redirect:    {redirect_3xx:5d} ({pct:5.1f}%)")
        
        if client_4xx:
            pct = (client_4xx / total_requests) * 100
            print(f"   ⚠️  4xx Client Err:  {client_4xx:5d} ({pct:5.1f}%)")
        
        if server_5xx:
            pct = (server_5xx / total_requests) * 100
            print(f"   ❌ 5xx Server Err:  {server_5xx:5d} ({pct:5.1f}%)")
        
        # Detailed breakdown
        print(f"\n   Detailed Status Codes:")
        for code in sorted(metrics["status_codes"].keys()):
            count = metrics["status_codes"][code]
            pct = (count / total_requests) * 100
            icon = "✅" if 200 <= code < 300 else "⚠️" if 400 <= code < 500 else "❌" if code >= 500 else "ℹ️"
            print(f"      {icon} {code}: {count:5d} ({pct:5.1f}%)")
    else:
        print(f"   No HTTP requests found in logs")
    
    # HTTP Methods
    if metrics["methods"]:
        print(f"\n🔧 HTTP Methods:")
        for method, count in metrics["methods"].most_common():
            print(f"   {method:7s}: {count:5d}")
    
    # Top Endpoints
    if metrics["endpoints"]:
        print(f"\n🔗 Top 10 Endpoints:")
        for endpoint, count in sorted(metrics["endpoints"].items(), key=lambda x: x[1], reverse=True)[:10]:
            print(f"   {endpoint:50s}: {count:5d}")
    
    # Response Times
    if metrics["response_times"]:
        import statistics
        
        print(f"\n⏱️  Response Times:")
        print(f"   Count:      {len(metrics['response_times']):7d}")
        print(f"   Mean:       {statistics.mean(metrics['response_times']):7.1f}ms")
        print(f"   Median:     {statistics.median(metrics['response_times']):7.1f}ms")
        print(f"   Min:        {min(metrics['response_times']):7.1f}ms")
        print(f"   Max:        {max(metrics['response_times']):7.1f}ms")
        
        if len(metrics['response_times']) >= 20:
            print(f"   P95:        {statistics.quantiles(metrics['response_times'], n=20)[18]:7.1f}ms")
        if len(metrics['response_times']) >= 100:
            print(f"   P99:        {statistics.quantiles(metrics['response_times'], n=100)[98]:7.1f}ms")
    
    # Errors
    print(f"\n❌ Errors: {len(metrics['errors'])}")
    if metrics["errors"]:
        print(f"   Showing first 10 errors:")
        for error in metrics["errors"][:10]:
            print(f"   Line {error['line']:5d}: {error['text'][:100]}")
        
        if len(metrics["errors"]) > 10:
            print(f"   ... and {len(metrics['errors']) - 10} more errors")
    
    # Warnings
    print(f"\n⚠️  Warnings: {len(metrics['warnings'])}")
    if metrics["warnings"]:
        print(f"   Showing first 10 warnings:")
        for warning in metrics["warnings"][:10]:
            print(f"   Line {warning['line']:5d}: {warning['text'][:100]}")
        
        if len(metrics["warnings"]) > 10:
            print(f"   ... and {len(metrics['warnings']) - 10} more warnings")
    
    # Slow Requests
    print(f"\n🐌 Slow Requests (>1000ms): {len(metrics['slow_requests'])}")
    if metrics["slow_requests"]:
        print(f"   Showing first 10 slow requests:")
        for req in metrics["slow_requests"][:10]:
            print(f"   {req['method']:6s} {req['endpoint']:40s} - {req['duration']:5d}ms")
        
        if len(metrics["slow_requests"]) > 10:
            print(f"   ... and {len(metrics['slow_requests']) - 10} more slow requests")
    
    # Success Rate
    if total_requests > 0:
        success_rate = (success_2xx / total_requests) * 100
        error_rate = ((client_4xx + server_5xx) / total_requests) * 100
        
        print(f"\n📈 Success Metrics:")
        print(f"   Success Rate:   {success_rate:5.2f}%")
        print(f"   Error Rate:     {error_rate:5.2f}%")
        
        if success_rate >= 99.9:
            print(f"   Status:         ✅ EXCELLENT (>99.9%)")
        elif success_rate >= 99:
            print(f"   Status:         ✅ GOOD (>99%)")
        elif success_rate >= 95:
            print(f"   Status:         ⚠️  ACCEPTABLE (>95%)")
        else:
            print(f"   Status:         ❌ POOR (<95%)")
    
    print("\n" + "="*70)


def main():
    """Main execution"""
    print("="*70)
    print("AZURE CONTAINER APPS LOG ANALYZER")
    print("="*70)
    print()
    
    # Default values from environment
    default_resource_group = os.getenv("RESOURCE_GROUP", "rg_award_nomination")
    default_apps = ["award-api-eastus", "award-api-westus"]
    
    # Get parameters
    if len(sys.argv) < 2:
        print("Usage: python analyze_container_logs.py [app-name|both] [resource-group] [hours]")
        print()
        print(f"Defaults:")
        print(f"  Resource Group: {default_resource_group}")
        print(f"  Apps: {', '.join(default_apps)}")
        print()
        print("Examples:")
        print("  # Analyze both regions with defaults")
        print("  python analyze_container_logs.py both")
        print()
        print("  # Analyze specific region")
        print("  python analyze_container_logs.py award-api-eastus")
        print()
        print("  # Analyze both regions with custom hours")
        print("  python analyze_container_logs.py both rg_award_nomination 12")
        print()
        print("  # Analyze specific region with custom resource group and hours")
        print("  python analyze_container_logs.py award-api-westus my-resource-group 6")
        sys.exit(1)
    
    # Parse arguments
    app_arg = sys.argv[1]
    resource_group = sys.argv[2] if len(sys.argv) > 2 else default_resource_group
    hours = int(sys.argv[3]) if len(sys.argv) > 3 else 24
    
    # Determine which apps to analyze
    if app_arg.lower() == "both":
        apps_to_analyze = default_apps
        print(f"📊 Analyzing both regions: {', '.join(apps_to_analyze)}")
    else:
        apps_to_analyze = [app_arg]
        print(f"📊 Analyzing: {app_arg}")
    
    print(f"📁 Resource Group: {resource_group}")
    print(f"⏰ Time Range: Last {hours} hours")
    print()
    
    # Analyze each app
    all_results = {}
    
    for app_name in apps_to_analyze:
        print("\n" + "="*70)
        print(f"ANALYZING: {app_name}")
        print("="*70)
        
        # Download logs
        logs = get_container_app_logs(app_name, resource_group, hours_back=hours)
        
        if not logs:
            print(f"❌ Failed to download logs for {app_name}")
            continue
        
        # Save to file
        filename = f"{app_name}_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        with open(filename, 'w') as f:
            f.write(logs)
        print(f"💾 Logs saved to: {filename}\n")
        
        # Analyze
        metrics = analyze_logs(logs)
        
        # Print results
        print_analysis(metrics)
        
        # Store results
        all_results[app_name] = metrics
        
        # Save analysis
        analysis_filename = f"{app_name}_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(analysis_filename, 'w') as f:
            # Convert to JSON-serializable format
            json_metrics = {
                "app_name": app_name,
                "resource_group": resource_group,
                "hours_analyzed": hours,
                "analysis_time": datetime.now().isoformat(),
                "total_lines": metrics["total_lines"],
                "status_codes": dict(metrics["status_codes"]),
                "methods": dict(metrics["methods"]),
                "endpoints": dict(sorted(metrics["endpoints"].items(), key=lambda x: x[1], reverse=True)[:20]),
                "error_count": len(metrics["errors"]),
                "warning_count": len(metrics["warnings"]),
                "slow_request_count": len(metrics["slow_requests"]),
                "response_times": {
                    "count": len(metrics["response_times"]),
                    "mean": sum(metrics["response_times"]) / len(metrics["response_times"]) if metrics["response_times"] else 0,
                    "min": min(metrics["response_times"]) if metrics["response_times"] else 0,
                    "max": max(metrics["response_times"]) if metrics["response_times"] else 0,
                }
            }
            json.dump(json_metrics, f, indent=2)
        
        print(f"\n💾 Analysis saved to: {analysis_filename}")
    
    # If analyzing both, print comparison
    if len(all_results) > 1:
        print("\n" + "="*70)
        print("COMPARISON: EASTUS vs WESTUS")
        print("="*70)
        
        for app_name, metrics in all_results.items():
            total_requests = sum(metrics["status_codes"].values())
            success_2xx = sum(count for code, count in metrics["status_codes"].items() if 200 <= code < 300)
            server_5xx = sum(count for code, count in metrics["status_codes"].items() if 500 <= code < 600)
            
            success_rate = (success_2xx / total_requests * 100) if total_requests > 0 else 0
            error_count = len(metrics["errors"])
            
            print(f"\n{app_name}:")
            print(f"  Total Requests:  {total_requests:7,d}")
            print(f"  Success Rate:    {success_rate:6.2f}%")
            print(f"  5xx Errors:      {server_5xx:7,d}")
            print(f"  Log Errors:      {error_count:7,d}")
            
            if metrics["response_times"]:
                import statistics
                print(f"  Avg Response:    {statistics.mean(metrics['response_times']):7.1f}ms")
                print(f"  P95 Response:    {statistics.quantiles(metrics['response_times'], n=20)[18] if len(metrics['response_times']) >= 20 else 'N/A':>7}")
        
        print("\n" + "="*70)


if __name__ == "__main__":
    main()