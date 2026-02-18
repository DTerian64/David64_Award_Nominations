"""
Email Utility - Gmail SMTP Implementation with Action Buttons
Free alternative to SendGrid for development
"""

import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
import logging
logger = logging.getLogger(__name__) 

# Configuration from environment variables
GMAIL_USER = os.getenv("GMAIL_USER", "david.terian@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
FROM_EMAIL = os.getenv("FROM_EMAIL", GMAIL_USER)
FROM_NAME = os.getenv("FROM_NAME", "Award Nomination System")
API_BASE_URL = os.getenv("API_BASE_URL")

# SMTP Configuration
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

# Validate configuration
if not GMAIL_APP_PASSWORD:
    logger.warning("⚠️  WARNING: GMAIL_APP_PASSWORD not set. Email notifications will fail.")
    logger.info("   Generate at: https://myaccount.google.com/apppasswords")


async def send_email(
    to_email: str, 
    subject: str, 
    body: str,
    from_email: Optional[str] = None,
    from_name: Optional[str] = None
) -> bool:
    """
    📧 Send email using Gmail SMTP
    
    Args:
        to_email: Recipient email address
        subject: Email subject
        body: HTML email body
        from_email: Optional sender email (defaults to GMAIL_USER)
        from_name: Optional sender name (defaults to FROM_NAME)
    
    Returns:
        bool: True if email sent successfully, False otherwise
    """
    try:
        if not GMAIL_APP_PASSWORD:
            logger.error("❌ Cannot send email: GMAIL_APP_PASSWORD not configured")
            return False
        
        # Create message
        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = f"{from_name or FROM_NAME} <{from_email or FROM_EMAIL}>"
        message["To"] = to_email
        
        # Attach HTML content
        html_part = MIMEText(body, "html")
        message.attach(html_part)
        
        # Send via Gmail SMTP
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.sendmail(from_email or GMAIL_USER, [to_email], message.as_string())
        
        logger.info(f"✅ Email sent successfully to {to_email}")
        return True
        
    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"❌ Gmail authentication failed: {e}")
        logger.info("   Check your app password at: https://myaccount.google.com/apppasswords")
        return False
    except smtplib.SMTPException as e:
        logger.error(f"❌ SMTP error: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Email error: {e}")
        return False


# 📧 Email Templates

def get_nomination_pending_email(
    manager_name: str,
    nominator_name: str,
    beneficiary_name: str,
    dollar_amount: float,
    description: str,
    approve_url: str,
    reject_url: str
) -> str:
    """
    📧 Email template for pending nomination with action buttons
    
    Args:
        manager_name: Name of the approving manager
        nominator_name: Name of person who submitted nomination
        beneficiary_name: Name of person being nominated
        dollar_amount: Award amount
        description: Nomination description
        approve_url: URL for approve button (with token)
        reject_url: URL for reject button (with token)
    
    Returns:
        str: HTML email body with approve/reject buttons
    """
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
    </head>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
        <div style="background-color: #f8f9fa; border-radius: 10px; padding: 30px; margin-bottom: 20px;">
            <h2 style="color: #2c3e50; margin-top: 0;">🔔 New Award Nomination Pending Approval</h2>
            <p style="font-size: 16px;">Dear <strong>{manager_name}</strong>,</p>
            <p style="font-size: 16px;">
                <strong>{nominator_name}</strong> has nominated <strong>{beneficiary_name}</strong> 
                for a monetary award of <strong>${dollar_amount:,.2f}</strong>.
            </p>
        </div>
        
        <div style="background-color: #fff; border: 1px solid #e0e0e0; border-radius: 8px; padding: 20px; margin-bottom: 30px;">
            <h3 style="color: #2c3e50; margin-top: 0;">📝 Nomination Details:</h3>
            <p style="background-color: #f8f9fa; padding: 15px; border-radius: 5px; border-left: 4px solid #3498db;">
                {description}
            </p>
        </div>
        
        <div style="text-align: center; margin: 40px 0;">
            <p style="font-size: 16px; margin-bottom: 20px;"><strong>Take Action:</strong></p>
            
            <!-- Approve Button -->
            <a href="{approve_url}" 
               style="display: inline-block; 
                      background-color: #27ae60; 
                      color: white; 
                      padding: 15px 40px; 
                      text-decoration: none; 
                      border-radius: 5px; 
                      font-weight: bold; 
                      font-size: 16px;
                      margin: 0 10px 10px 0;
                      box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                ✅ Approve
            </a>
            
            <!-- Reject Button -->
            <a href="{reject_url}" 
               style="display: inline-block; 
                      background-color: #e74c3c; 
                      color: white; 
                      padding: 15px 40px; 
                      text-decoration: none; 
                      border-radius: 5px; 
                      font-weight: bold; 
                      font-size: 16px;
                      margin: 0 0 10px 10px;
                      box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                ❌ Reject
            </a>
        </div>
        
        <div style="background-color: #fff3cd; border-left: 4px solid #ffc107; padding: 15px; margin: 20px 0; border-radius: 4px;">
            <p style="margin: 0; font-size: 14px;">
                <strong>⏰ Note:</strong> This approval link will expire in 72 hours. 
                You can also approve/reject this nomination by logging into the Award Nomination System.
            </p>
        </div>
        
        <hr style="border: none; border-top: 1px solid #e0e0e0; margin: 30px 0;">
        
        <p style="color: #7f8c8d; font-size: 12px; text-align: center;">
            This is an automated message from the Award Nomination System.<br>
            Please do not reply to this email.
        </p>
    </body>
    </html>
    """


def get_nomination_submitted_email(nominee_name: str, award_name: str) -> str:
    """📧 Email template for nomination submission confirmation"""
    return f"""
    <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2 style="color: #2c3e50;">✅ Nomination Submitted</h2>
            <p>Your nomination has been successfully submitted:</p>
            <ul>
                <li><strong>Nominee:</strong> {nominee_name}</li>
                <li><strong>Award:</strong> {award_name}</li>
            </ul>
            <p>You will receive updates as your nomination is reviewed.</p>
            <hr style="margin: 20px 0;">
            <p style="color: #7f8c8d; font-size: 12px;">
                This is an automated message from the Award Nomination System.
            </p>
        </body>
    </html>
    """


def get_nomination_approved_email(nominee_name: str, award_name: str) -> str:
    """🎉 Email template for nomination approval"""
    return f"""
    <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2 style="color: #27ae60;">🎉 Nomination Approved!</h2>
            <p>Great news! Your nomination has been approved:</p>
            <ul>
                <li><strong>Nominee:</strong> {nominee_name}</li>
                <li><strong>Award:</strong> {award_name}</li>
            </ul>
            <p>The nominee will be notified of this honor.</p>
            <hr style="margin: 20px 0;">
            <p style="color: #7f8c8d; font-size: 12px;">
                This is an automated message from the Award Nomination System.
            </p>
        </body>
    </html>
    """


def get_action_confirmation_page(action: str, success: bool, message: str) -> str:
    """
    📄 HTML page shown after clicking approve/reject button
    
    Args:
        action: "approved" or "rejected"
        success: Whether the action succeeded
        message: Details message to display
    
    Returns:
        str: HTML page to display in browser
    """
    if success:
        color = "#27ae60" if action == "approved" else "#e74c3c"
        icon = "✅" if action == "approved" else "❌"
        title = f"Nomination {action.title()}"
    else:
        color = "#e74c3c"
        icon = "⚠️"
        title = "Action Failed"
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{title}</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
                margin: 0;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            }}
            .container {{
                background: white;
                padding: 40px;
                border-radius: 10px;
                box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                text-align: center;
                max-width: 500px;
            }}
            .icon {{
                font-size: 72px;
                margin-bottom: 20px;
            }}
            h1 {{
                color: {color};
                margin-bottom: 20px;
            }}
            p {{
                font-size: 18px;
                color: #666;
                line-height: 1.6;
            }}
            .button {{
                display: inline-block;
                background-color: #667eea;
                color: white;
                padding: 12px 30px;
                text-decoration: none;
                border-radius: 5px;
                margin-top: 20px;
                font-weight: bold;
            }}
            .button:hover {{
                background-color: #5568d3;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="icon">{icon}</div>
            <h1>{title}</h1>
            <p>{message}</p>
            <a href="https://awards.terian-services.com" class="button">Go to Dashboard</a>
        </div>
    </body>
    </html>
    """


# Usage example:
if __name__ == "__main__":
    import asyncio
    from token_utils import get_action_url
    
    async def test_email():
        """Test email with action buttons"""
        
        # Generate action URLs
        approve_url = get_action_url(API_BASE_URL, 123, "approve", 456)
        reject_url = get_action_url(API_BASE_URL, 123, "reject", 456)
        
        # Create email body
        body = get_nomination_pending_email(
            manager_name="Jordan Miller",
            nominator_name="David Terian",
            beneficiary_name="Drew Anderson",
            dollar_amount=550.00,
            description="Outstanding performance in Q4 2025",
            approve_url=approve_url,
            reject_url=reject_url
        )
        
        # Send email
        success = await send_email(
            to_email="david.terian@gmail.com",
            subject="Test - Award Nomination Pending Approval",
            body=body
        )
        logger.info(f"Email test {'succeeded' if success else 'failed'}")
    
    asyncio.run(test_email())