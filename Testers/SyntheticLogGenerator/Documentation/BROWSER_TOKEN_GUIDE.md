# Load Testing with Browser Token - Quick Start

## 🎯 Why This Approach?

Your API uses **OAuth2 Implicit Flow** (browser-based authentication), not ROPC. This means we can't use username/password directly. Instead, we'll:

1. ✅ Get a token from Swagger UI (where you normally log in)
2. ✅ Use that token for load testing
3. ✅ Much simpler - no Azure AD configuration needed!

## 🚀 Quick Start

### Step 1: Install Dependencies

```bash
pip install aiohttp
```

### Step 2: Get Your Token from Swagger UI

1. Open your API docs: **https://award-nomination-api-bqb8ftbdfpemfyck.z02.azurefd.net/docs**

2. Click the **"Authorize"** button (green lock icon at top right)

3. Log in as `david64.terian@terian-services.com`

4. After successful login, open **Browser DevTools** (Press `F12`)

5. **Option A - From Network Tab (Easier)**:
   - Go to **Network** tab in DevTools
   - Click any endpoint in Swagger (e.g., GET /api/users)
   - Click "Execute"
   - Find the request in Network tab
   - Click on it → **Headers** tab
   - Find **"Authorization: Bearer ..."**
   - Copy everything **after** "Bearer " (the token)

6. **Option B - From Storage**:
   - Go to **Application** tab in DevTools  
   - Expand **Session Storage** or **Local Storage**
   - Find a key related to auth/token
   - Copy the value

### Step 3: Run Load Test

```bash
python impersonation_load_generator_browser_token.py
```

You'll be prompted to:
1. **Paste your token** (from Step 2)
2. **Virtual users** (default: 50)
3. **Duration** (default: 15 minutes)
4. **Auto-approve?** (default: Yes)

### Example Session

```
====================================================================
GET ADMIN TOKEN FROM SWAGGER UI
====================================================================

📋 Instructions:
1. Open: https://award-nomination-api-bqb8ftbdfpemfyck.z02.azurefd.net/docs
2. Click 'Authorize' button (green lock icon)
3. Log in as david64.terian@terian-services.com
...

🔑 Paste your token here: eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIsIng1dCI6...

✅ Token received (length: 1847 chars)

📊 Load Test Configuration
   Concurrent virtual users [50]: 50
   Duration in minutes [15]: 10
   Auto-approve nominations? [Y/n]: Y

====================================================================
Impersonation-Based Load Test Starting
  Virtual users: 50
  Duration: 0.17 hours
  Auto-approve: True
  API: https://award-nomination-api-bqb8ftbdfpemfyck.z02.azurefd.net
====================================================================

Fetching users from /api/users...
✅ Loaded 156 users
   - 156 potential nominators
   - 142 potential beneficiaries
   - 23 managers

▶️  Starting 50 virtual user sessions...

✅ Nomination created: alice.jones@... → Beneficiary 42, $150 (ID: 1234)
✅ Nomination approved: ID 1234 by manager.smith@...
...
```

## ⚠️ Token Expiry

**Important**: Browser tokens typically expire after **1 hour**.

If your load test runs longer than 1 hour, you'll see 401 errors:
```
❌ Nomination failed (401): ... - Unauthorized
```

**Solutions**:
1. Run tests under 1 hour
2. Get a fresh token and restart
3. (Advanced) Implement token refresh logic

## 🔄 Getting a Fresh Token

If token expires mid-test:
1. Stop the test (Ctrl+C)
2. Go back to Swagger UI
3. Log out and log back in
4. Get the new token
5. Restart the load test

## 📊 What This Tests

✅ Full authentication flow (using real browser token)  
✅ Impersonation via X-Impersonate-User header  
✅ Nomination creation workflow  
✅ Approval workflow  
✅ Fraud detection under load  
✅ Manager inference  
✅ Database performance  
✅ API concurrency handling  

## 🎯 Advantages Over ROPC Approach

| Aspect | Browser Token | ROPC |
|--------|---------------|------|
| **Setup** | None needed | Azure AD config required |
| **Works with your API** | ✅ Yes | ❌ No (wrong flow) |
| **Azure AD changes** | None | Must enable ROPC |
| **MFA support** | ✅ Yes | ❌ No |
| **Token source** | Real login | Password flow |

## 🐛 Troubleshooting

### "Failed to fetch users: 401"
**Cause**: Token expired or invalid

**Solution**:
- Get a fresh token from Swagger UI
- Make sure you copied the entire token
- Don't include "Bearer " when pasting

### "Failed to fetch users: 403"
**Cause**: Admin role not configured

**Solution**:
- Verify david64.terian@terian-services.com has AWard_Nomination_Admin role
- Check Azure AD app registration → App roles

### "No eligible beneficiaries found"
**Cause**: Users in database don't have managers

**Solution**:
```sql
SELECT COUNT(*) FROM Users WHERE ManagerId IS NOT NULL;
-- Should return > 0
```

### Token too short/looks wrong
**Cause**: Didn't copy full token

**Solution**:
- Token should be ~1500-2000 characters
- Should start with "eyJ..."
- Should have dots (.) in it
- Example: `eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIsIng1dCI6...`

## 📝 Files Needed

Only 1 file required:
- **`impersonation_load_generator_browser_token.py`** - Main script

That's it! No other dependencies or configuration files needed.

## 🎓 How It Works

```
┌─────────────────────┐
│   1. You log into   │
│   Swagger UI via    │
│   browser           │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│   2. Browser gets   │
│   JWT token from    │
│   Azure AD          │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│   3. You copy token │
│   and paste into    │
│   load generator    │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│   4. Load generator │
│   uses token with   │
│   impersonation     │
└─────────────────────┘
```

## ✨ Summary

This is the **simplest approach** for your use case:
- ✅ No Azure AD configuration changes
- ✅ No ROPC setup
- ✅ Works with your existing auth flow
- ✅ Just get token from Swagger and run!

**Ready to test?**

```bash
python impersonation_load_generator_browser_token.py
```
