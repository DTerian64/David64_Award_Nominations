# Email Migration Guide: SendGrid → Gmail SMTP

This guide explains the migration from SendGrid to Gmail SMTP for email notifications in your Award Nomination System.

## 🎯 Changes Made

### 1. **New Email Module** (`email_utils.py`)
- Consolidated all email functionality into a reusable module
- Implemented Gmail SMTP instead of SendGrid API
- Added professional email templates
- Better error handling and logging

### 2. **Updated main.py**
- Removed SendGrid dependencies
- Imported email functions from `email_utils.py`
- Added email notifications for:
  - ✅ New nomination submitted (to manager)
  - ✅ Nomination approved (to nominator)
  - ✅ Nomination rejected (to nominator)
- Improved email formatting with HTML templates

### 3. **SQL Helper Addition**
- Added `get_nomination_details()` function to retrieve email addresses
- Needed for sending approval/rejection notifications

---

## 📋 Setup Instructions

### Step 1: Generate Gmail App Password

1. Go to https://myaccount.google.com/security
2. Enable **2-Step Verification** (if not already enabled)
3. Go to https://myaccount.google.com/apppasswords
4. Click **Create**:
   - App: Mail
   - Device: Other (Custom) → "Award Nomination API"
5. Copy the 16-character password (format: `xxxx xxxx xxxx xxxx`)

### Step 2: Update Azure Container App Environment Variables

#### Option A: Azure Portal (Easiest)

1. Navigate to: **Azure Portal** → **Container Apps** → `award-api-eastus`
2. Click **Settings** → **Containers**
3. Edit your container
4. Scroll to **Environment variables**
5. **Add Secrets:**
   ```
   Name: gmail-app-password
   Value: <your-16-char-password>
   ```
6. **Add Environment Variables:**
   ```
   Name: GMAIL_APP_PASSWORD
   Source: Reference a secret
   Secret: gmail-app-password
   
   Name: GMAIL_USER
   Value: david.terian@gmail.com
   
   Name: FROM_EMAIL
   Value: david.terian@gmail.com
   
   Name: FROM_NAME
   Value: Award Nomination System
   ```
7. Click **Save** (container will restart)

#### Option B: Azure CLI

```bash
# Add the app password as a secret
az containerapp secret set \
  --name award-api-eastus \
  --resource-group rg_award_nomination \
  --secrets gmail-app-password="<your-16-char-password>"

# Add environment variables
az containerapp update \
  --name award-api-eastus \
  --resource-group rg_award_nomination \
  --set-env-vars \
    "GMAIL_APP_PASSWORD=secretref:gmail-app-password" \
    "GMAIL_USER=david.terian@gmail.com" \
    "FROM_EMAIL=david.terian@gmail.com" \
    "FROM_NAME=Award Nomination System"
```

### Step 3: Update Your Code

#### 3.1 Add `email_utils.py` to your project
```bash
# Copy email_utils.py to your FastAPI project root
cp email_utils.py /path/to/your/fastapi/project/
```

#### 3.2 Update `main.py`
Replace your existing `main.py` with the updated version, or manually apply these changes:

**Remove:**
```python
# Email Configuration (SendGrid)
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
FROM_EMAIL = os.getenv("FROM_EMAIL")

async def send_email(to_email: str, subject: str, body: str):
    # ... SendGrid implementation ...
```

**Add:**
```python
# Email Configuration (Gmail SMTP)
from email_utils import send_email, get_nomination_submitted_email, get_nomination_approved_email
```

#### 3.3 Add SQL Helper Function
Add the `get_nomination_details()` function from `sqlhelper_addition.py` to your `sqlhelper.py` file.

### Step 4: Update requirements.txt

**Remove:**
```txt
sendgrid>=6.11.0
```

**No additional packages needed!** Gmail SMTP uses Python's built-in `smtplib`.

### Step 5: Update Dockerfile (Already Done ✅)

Your Dockerfile already includes `bash` and is ready to go!

### Step 6: Deploy

```bash
# Build new image
docker build -t <your-registry>.azurecr.io/award-api:v2 .

# Push to ACR
az acr login --name <your-registry>
docker push <your-registry>.azurecr.io/award-api:v2

# Update Container App
az containerapp update \
  --name award-api-eastus \
  --resource-group rg_award_nomination \
  --image <your-registry>.azurecr.io/award-api:v2
```

---

## 🧪 Testing

### Test Locally

```bash
# Create a test script
python -c "
import asyncio
from email_utils import send_email, get_nomination_submitted_email

async def test():
    body = get_nomination_submitted_email('John Doe', 'Employee of the Month')
    result = await send_email(
        'david.terian@gmail.com',
        'Test - Award Nomination',
        body
    )
    print(f'Email sent: {result}')

asyncio.run(test())
"
```

### Test in Container

```bash
# Connect to container console
az containerapp exec \
  --name award-api-eastus \
  --resource-group rg_award_nomination \
  --command /bin/bash

# Inside container, test:
python3 -c "
import os
print('GMAIL_USER:', os.getenv('GMAIL_USER'))
print('GMAIL_APP_PASSWORD:', 'SET' if os.getenv('GMAIL_APP_PASSWORD') else 'NOT SET')
print('FROM_EMAIL:', os.getenv('FROM_EMAIL'))
"
```

### Test API Endpoint

```bash
# Submit a test nomination (will trigger email to manager)
curl -X POST https://award-api-eastus.lemonpond-a2daba01.eastus.azurecontainerapps.io/api/nominations \
  -H "Authorization: Bearer <your-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "BeneficiaryId": 123,
    "DollarAmount": 500,
    "NominationDescription": "Test nomination"
  }'
```

---

## 📧 Email Notifications Flow

### 1. Nomination Submitted
**Trigger:** User submits a nomination  
**Recipient:** Manager (approver)  
**Subject:** "Award Nomination Pending Approval - [Beneficiary Name]"  
**Content:** Nomination details and approval request

### 2. Nomination Approved
**Trigger:** Manager approves nomination  
**Recipient:** Nominator  
**Subject:** "✅ Nomination Approved - [Beneficiary Name]"  
**Content:** Approval confirmation with nominee and award details

### 3. Nomination Rejected
**Trigger:** Manager rejects nomination  
**Recipient:** Nominator  
**Subject:** "Nomination Status - [Beneficiary Name]"  
**Content:** Status update notification

---

## 💰 Cost Comparison

| Service | Free Tier | Cost After |
|---------|-----------|------------|
| **Gmail SMTP** | 500 emails/day | FREE forever |
| SendGrid | 100 emails/day | $19.95/mo for 40K |
| Mailgun | 5K/mo (3 months) | $35/mo for 50K |
| Resend | 3K/month | $20/mo for 50K |

**Savings:** $240/year by using Gmail SMTP!

---

## 🔒 Security Notes

1. **App Password vs Regular Password**
   - NEVER use your regular Gmail password
   - Always use an app-specific password
   - App passwords can be revoked independently

2. **Environment Variables**
   - Store passwords as Container App secrets
   - Never commit passwords to Git
   - Use Azure Key Vault for production

3. **Email Rate Limits**
   - Gmail: 500 emails/day (more than enough for most apps)
   - If you exceed this, consider Resend or AWS SES

---

## 🐛 Troubleshooting

### Issue: "Authentication failed"
**Solution:**
- Verify 2-Step Verification is enabled
- Regenerate app password
- Check environment variable is set correctly

### Issue: "SMTP connection timeout"
**Solution:**
- Check Azure Container App egress is allowed
- Verify port 587 is not blocked
- Try port 465 (SSL) instead

### Issue: "Emails not received"
**Solution:**
- Check spam folder
- Verify sender email matches Gmail account
- Test with a different recipient email

### Issue: "Module not found: email_utils"
**Solution:**
- Ensure `email_utils.py` is in the same directory as `main.py`
- Rebuild Docker image
- Check file was copied in Dockerfile

---

## 🚀 Next Steps

1. **Monitor email delivery** in Azure Container App logs
2. **Set up email analytics** (open rates, click rates)
3. **Consider email templates** for branding
4. **Add email preferences** for users
5. **Implement email queuing** for high volume

---

## 📝 Files Changed

- ✅ `email_utils.py` - New email module
- ✅ `main.py` - Updated to use email_utils
- ✅ `sqlhelper.py` - Add get_nomination_details()
- ✅ `requirements.txt` - Remove sendgrid
- ✅ `Dockerfile` - Already has bash support
- ✅ Azure Container App - Update environment variables

---

## 📞 Support

If you encounter issues:
1. Check Container App logs: `az containerapp logs show --follow`
2. Test email locally first
3. Verify environment variables are set
4. Check Gmail app password is correct

---

**Migration Complete!** 🎉

Your Award Nomination System now uses Gmail SMTP for free, reliable email notifications.
