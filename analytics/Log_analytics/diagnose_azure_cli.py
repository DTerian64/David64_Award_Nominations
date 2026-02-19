"""
Azure CLI Diagnostic Tool
Checks if Azure CLI is properly installed and accessible
"""

import subprocess
import platform
import os
import shutil

print("="*70)
print("AZURE CLI DIAGNOSTIC TOOL")
print("="*70)
print()

# System info
print(f"🖥️  System: {platform.system()} {platform.release()}")
print(f"🐍 Python: {platform.python_version()}")
print()

# Check if 'az' is in PATH
print("1️⃣  Checking if 'az' is in PATH...")
az_in_path = shutil.which("az")

if az_in_path:
    print(f"   ✅ Found: {az_in_path}")
else:
    print(f"   ❌ Not found in PATH")

# On Windows, check common installation locations
if platform.system() == "Windows":
    print("\n2️⃣  Checking common Windows installation paths...")
    
    possible_paths = [
        r"C:\Program Files (x86)\Microsoft SDKs\Azure\CLI2\wbin\az.cmd",
        r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd",
        os.path.expanduser(r"~\AppData\Local\Programs\Microsoft\Azure CLI\wbin\az.cmd"),
        r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az",
    ]
    
    found_paths = []
    for path in possible_paths:
        if os.path.exists(path):
            print(f"   ✅ Found: {path}")
            found_paths.append(path)
        else:
            print(f"   ❌ Not found: {path}")
    
    if found_paths:
        az_cmd = found_paths[0]
    elif az_in_path:
        az_cmd = az_in_path
    else:
        az_cmd = None
else:
    az_cmd = az_in_path

# Try to run az --version
if az_cmd:
    print(f"\n3️⃣  Testing Azure CLI execution...")
    print(f"   Command: {az_cmd} --version")
    
    try:
        result = subprocess.run(
            [az_cmd, "--version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            print(f"   ✅ Success!")
            print()
            print("📋 Azure CLI Version:")
            # Print first few lines of version output
            for line in result.stdout.split('\n')[:5]:
                if line.strip():
                    print(f"   {line}")
        else:
            print(f"   ❌ Failed with error:")
            print(f"   {result.stderr}")
    
    except Exception as e:
        print(f"   ❌ Error running command: {e}")
else:
    print(f"\n❌ Azure CLI not found!")

# Check if logged in
if az_cmd:
    print(f"\n4️⃣  Checking Azure login status...")
    
    try:
        result = subprocess.run(
            [az_cmd, "account", "show"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            print(f"   ✅ Logged in!")
            # Try to parse subscription info
            try:
                import json
                account = json.loads(result.stdout)
                print(f"   Subscription: {account.get('name', 'Unknown')}")
                print(f"   User: {account.get('user', {}).get('name', 'Unknown')}")
            except:
                pass
        else:
            print(f"   ❌ Not logged in")
            print(f"   Run: az login")
    
    except Exception as e:
        print(f"   ⚠️  Could not check login status: {e}")

# Test containerapp command
if az_cmd:
    print(f"\n5️⃣  Testing containerapp extension...")
    
    try:
        result = subprocess.run(
            [az_cmd, "containerapp", "--help"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            print(f"   ✅ containerapp extension available")
        else:
            print(f"   ❌ containerapp extension not available")
            print(f"   Install with: az extension add --name containerapp")
    
    except Exception as e:
        print(f"   ⚠️  Could not check extension: {e}")

# Summary
print("\n" + "="*70)
print("SUMMARY")
print("="*70)

if az_cmd:
    print(f"✅ Azure CLI found at: {az_cmd}")
    print()
    print("💡 To use in Python script, the script will automatically use this path")
else:
    print("❌ Azure CLI not found")
    print()
    print("📥 Install Azure CLI:")
    if platform.system() == "Windows":
        print("   Download: https://aka.ms/installazurecliwindows")
        print("   Or use winget: winget install Microsoft.AzureCLI")
    elif platform.system() == "Darwin":
        print("   Run: brew install azure-cli")
    else:
        print("   Run: curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash")
    
    print()
    print("After installation:")
    print("   1. Close and reopen your terminal")
    print("   2. Run: az login")
    print("   3. Run this diagnostic again")

print("="*70)