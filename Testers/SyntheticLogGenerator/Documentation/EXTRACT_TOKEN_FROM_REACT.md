# Extract Token from Your React App - Visual Guide

## 🎯 This is the EASIEST Method!

Your React app at `https://awards.terianix.ai` already has the authentication token! Let's grab it.

---

## 📋 Step-by-Step Instructions

### Step 1: Open Your React App
```
https://awards.terianix.ai
```

### Step 2: Make Sure You're Logged In
- Log in as `david64.terian@terian-services.com` if you're not already

### Step 3: Open Browser DevTools
- Press **F12** (Windows/Linux)
- Or **Cmd+Option+I** (Mac)
- Or Right-click anywhere → "Inspect"

### Step 4: Go to Console Tab
- Click the **"Console"** tab at the top of DevTools

### Step 5: Run the Token Extraction Script
1. Open the file: `extract_token_from_react_app.js`
2. Copy the ENTIRE contents
3. Paste into the Console
4. Press **Enter**

### Step 6: Copy Your Token
The script will display:
```
✅ SUCCESS! TOKEN FOUND!
============================================================

🎫 YOUR TOKEN:
============================================================
eyJ0eXAiOiJKV1QiLCJhbGc... (very long string)
============================================================

📋 To copy to clipboard, run this command:
copy(`eyJ0eXAiOiJKV1QiLCJhbGc...`)
```

**To copy it:**
- Either: Select the token and Ctrl+C (Cmd+C on Mac)
- Or: Run the `copy(...)` command shown

---

## 🚀 Alternative: Network Tab Method (Even Easier!)

### Step 1: Open DevTools and Go to Network Tab
- Press **F12**
- Click **"Network"** tab

### Step 2: Reload the Page or Click Something
- Press **Ctrl+R** (Cmd+R on Mac) to reload
- Or click "Nominate Employee" or any button that makes an API call

### Step 3: Find an API Request
- Look for requests to `award-nomination-api-bqb8ftbdfpemfyck.z02.azurefd.net`
- Click on any of them

### Step 4: View Request Headers
- In the right panel, click **"Headers"** tab
- Scroll down to **"Request Headers"** section
- Find the line that says:
  ```
  Authorization: Bearer eyJ0eXAiOiJKV1QiLCJhbGc...
  ```

### Step 5: Copy the Token
- Click on the token (everything after "Bearer ")
- It will highlight
- Right-click → "Copy value"
- Or just select and Ctrl+C

---

## ✅ You Now Have Your Token!

### Next Step: Start Load Testing

```bash
python impersonation_load_generator_browser_token.py
```

When prompted:
```
🔑 Paste your token here: [paste the token you just copied]
```

Then configure:
- Virtual users: 50
- Duration: 15 minutes
- Auto-approve: Y

And you're load testing! 🚀

---

## 📊 What the Token Looks Like

A valid JWT token:
- Starts with `eyJ`
- Has dots (`.`) in it
- Is around 1500-2000 characters long
- Example: `eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIsIng1dCI6Ik...`

If your token doesn't look like this, try again or use the Network tab method.

---

## ⏰ Token Expiry

Tokens typically expire after **1 hour**.

If you see this error during load testing:
```
❌ Nomination failed (401): ... - Unauthorized
```

**Solution:**
1. Go back to your React app
2. Log out and log back in
3. Extract a fresh token using the same steps above
4. Restart the load test with the new token

---

## 🎓 Understanding What We're Doing

```
┌─────────────────────────┐
│  Your React App         │
│  (awards.terian-        │
│   services.com)         │
│                         │
│  You log in here ───────┼──► Gets token from Azure AD
│                         │
└────────┬────────────────┘
         │
         │ Token stored in browser
         │ (localStorage/sessionStorage)
         │
         ▼
┌─────────────────────────┐
│  We extract it using    │
│  JavaScript console     │
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│  Use token for          │
│  load testing           │
└─────────────────────────┘
```

---

## 🐛 Troubleshooting

### "Token not found"
**Try:**
1. Make sure you're logged into the React app
2. Try the Network tab method instead (usually more reliable)
3. Click around in the app to trigger some API calls

### "Token is expired"
**Solution:**
1. Log out of the React app
2. Log back in
3. Extract token again

### "Token too short or weird format"
**Cause:** You might have copied only part of it

**Solution:**
- Make sure you copied the ENTIRE token (all ~1500-2000 characters)
- Use the `copy()` command in console for reliable copying

---

## 💡 Pro Tip

**Save the token temporarily:**

After extracting, you can save it to a file:
```bash
echo "eyJ0eXAiOiJKV1QiLCJhbGc..." > admin_token.txt
```

Then when running the load test, you can paste from the file.

**Remember to delete this file after testing for security!**

```bash
rm admin_token.txt
```

---

## ✨ That's It!

This is the **simplest and most reliable** way to get your token because:
- ✅ Your React app already has it
- ✅ No Azure AD configuration needed
- ✅ No CLI tools needed
- ✅ Just copy and paste!

**Ready to start load testing?** 🚀
