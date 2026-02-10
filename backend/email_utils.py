"""
Email Utility - Gmail SMTP Implementation
Free alternative to SendGrid for development
"""

import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

# Configuration from environment variables
GMAIL_USER = os.getenv("GMAIL_USER", "david.terian@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")  # App-specific password
FROM_EMAIL = os.getenv("FROM_EMAIL", GMAIL_USER)
FROM_NAME = os.getenv("FROM_NAME", "Award Nomination System")

# SMTP Configuration
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

# Validate configuration
if not GMAIL_APP_PASSWORD:
    print("⚠️  WARNING: GMAIL_APP_PASSWORD not set. Email notifications will fail.")
    print("   Generate at: https://myaccount.google.com/apppasswords")


async def send_email(
    to_email: str, 
    subject: str, 
    body: str,
    from_email: Optional[str] = None,
    from_name: Optional[str] = None
) -> bool:
    """
    Send email using Gmail SMTP
    
    Args:
        to_email: Recipient email address
        subject: Email subject
        body: HTML email body
        from_email: Optional sender email (defaults to GMAIL_USER)
        from_name: Optional sender name (defaults to FROM_NAME)
    
    Returns:
        bool: True if email sent successfully, False otherwise
    
    Example:
        >>> await send_email(
        ...     "user@example.com",
        ...     "Nomination Approved",
        ...     "<h1>Your nomination has been approved!</h1>"
        ... )
    """
    try:
        if not GMAIL_APP_PASSWORD:
            print("❌ Cannot send email: GMAIL_APP_PASSWORD not configured")
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
        
        print(f"✅ Email sent successfully to {to_email}")
        return True
        
    except smtplib.SMTPAuthenticationError as e:
        print(f"❌ Gmail authentication failed: {e}")
        print("   Check your app password at: https://myaccount.google.com/apppasswords")
        return False
    except smtplib.SMTPException as e:
        print(f"❌ SMTP error: {e}")
        return False
    except Exception as e:
        print(f"❌ Email error: {e}")
        return False


# Email Templates
def get_nomination_submitted_email(nominee_name: str, award_name: str) -> str:
    """Email template for nomination submission"""
    return f"""
    <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #2c3e50;">Nomination Submitted</h2>
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
    """Email template for nomination approval"""
    return f"""
    <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
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


def get_fraud_alert_email(nomination_id: int, fraud_score: float, risk_level: str) -> str:
    """Email template for fraud detection alerts"""
    return f"""
    <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #e74c3c;">⚠️ Fraud Alert</h2>
            <p>A nomination has been flagged for potential fraud:</p>
            <ul>
                <li><strong>Nomination ID:</strong> {nomination_id}</li>
                <li><strong>Fraud Score:</strong> {fraud_score:.2f}</li>
                <li><strong>Risk Level:</strong> <span style="color: #e74c3c;">{risk_level}</span></li>
            </ul>
            <p>Please review this nomination immediately.</p>
            <hr style="margin: 20px 0;">
            <p style="color: #7f8c8d; font-size: 12px;">
                This is an automated security alert from the Award Nomination System.
            </p>
        </body>
    </html>
    """


# Usage example:
if __name__ == "__main__":
    import asyncio
    
    async def test_email():
        """Test email sending"""
        success = await send_email(
            to_email="david.terian@gmail.com",
            subject="Test Email - Award System",
            body=get_nomination_submitted_email("John Doe", "Employee of the Month")
        )
        print(f"Email test {'succeeded' if success else 'failed'}")
    
    asyncio.run(test_email())