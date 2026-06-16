"""
Default (TenantId=1, Lang='en') email template seed data.

Each value is a Jinja2 source string converted 1:1 from the legacy hard-coded
templates in auxiliary-service/email_client.py. The amount is passed in
pre-formatted as `formatted_amount`; all other dynamic fields are plain context
variables. Bodies are HTML (autoescaped at render time); subjects are plain text.

Phase 1 covers the core nomination flow. Remaining keys (HRBP x4,
payment_confirmed, description_rejected, demo_access_invite) follow the same
pattern and are added in the next batch.
"""

EN_TEMPLATES: dict[str, dict[str, str]] = {
    # ── Approver: nomination pending (with approve/reject buttons) ────────────
    "nomination_pending": {
        "subject": "Award Nomination Pending Approval — {{ beneficiary_name }}",
        "body": """
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
    </head>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;
                 max-width: 600px; margin: 0 auto; padding: 20px;">
        <div style="background-color: #f8f9fa; border-radius: 10px; padding: 30px; margin-bottom: 20px;">
            <h2 style="color: #2c3e50; margin-top: 0;">🔔 New Award Nomination Pending Approval</h2>
            <p style="font-size: 16px;">Dear <strong>{{ manager_name }}</strong>,</p>
            <p style="font-size: 16px;">
                <strong>{{ nominator_name }}</strong> has nominated <strong>{{ beneficiary_name }}</strong>
                for a monetary award of <strong>{{ formatted_amount }}</strong>.
            </p>
            {% if category %}<p style="margin: 4px 0 0;"><strong>Category:</strong> {{ category }}</p>{% endif %}
        </div>

        <div style="background-color: #fff; border: 1px solid #e0e0e0; border-radius: 8px;
                    padding: 20px; margin-bottom: 30px;">
            <h3 style="color: #2c3e50; margin-top: 0;">📝 Nomination Details:</h3>
            <p style="background-color: #f8f9fa; padding: 15px; border-radius: 5px;
                      border-left: 4px solid #3498db;">
                {{ description }}
            </p>
        </div>

        <div style="text-align: center; margin: 40px 0;">
            <p style="font-size: 16px; margin-bottom: 20px;"><strong>Take Action:</strong></p>
            <a href="{{ approve_url }}"
               style="display: inline-block; background-color: #27ae60; color: white;
                      padding: 15px 40px; text-decoration: none; border-radius: 5px;
                      font-weight: bold; font-size: 16px; margin: 0 10px 10px 0;
                      box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                ✅ Approve
            </a>
            <a href="{{ reject_url }}"
               style="display: inline-block; background-color: #e74c3c; color: white;
                      padding: 15px 40px; text-decoration: none; border-radius: 5px;
                      font-weight: bold; font-size: 16px; margin: 0 0 10px 10px;
                      box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                ❌ Reject
            </a>
        </div>

        <div style="background-color: #fff3cd; border-left: 4px solid #ffc107;
                    padding: 15px; margin: 20px 0; border-radius: 4px;">
            <p style="margin: 0; font-size: 14px;">
                <strong>⏰ Note:</strong> This approval link will expire in 72 hours.
                You can also approve or reject this nomination by logging into the
                Award Nomination System.
            </p>
        </div>

        <hr style="border: none; border-top: 1px solid #e0e0e0; margin: 30px 0;">
        <p style="color: #7f8c8d; font-size: 12px; text-align: center;">
            This is an automated message from the Award Nomination System.<br>
            Please do not reply to this email.
        </p>
    </body>
    </html>
    """,
    },

    # ── Nominator: approved ───────────────────────────────────────────────────
    "nomination_approved": {
        "subject": "✅ Nomination Approved — {{ beneficiary_name }}",
        "body": """
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #27ae60;">🎉 Nomination Approved!</h2>
        <p>Great news! Your nomination has been approved:</p>
        <ul>
            <li><strong>Nominee:</strong> {{ beneficiary_name }}</li>
            <li><strong>Award:</strong> Monetary Award ({{ formatted_amount }})</li>
            {% if category %}<li><strong>Category:</strong> {{ category }}</li>{% endif %}
        </ul>
        <p>The nominee will be notified of this honour.</p>
        <hr style="margin: 20px 0;">
        <p style="color: #7f8c8d; font-size: 12px;">
            This is an automated message from the Award Nomination System.
        </p>
    </body>
    </html>
    """,
    },

    # ── Nominator: rejected ───────────────────────────────────────────────────
    "nomination_rejected": {
        "subject": "Nomination Status — {{ beneficiary_name }}",
        "body": """
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #e74c3c;">Nomination Status Update</h2>
        <p>Your nomination has been reviewed:</p>
        <ul>
            <li><strong>Nominee:</strong> {{ beneficiary_name }}</li>
            <li><strong>Award:</strong> Monetary Award ({{ formatted_amount }})</li>
            {% if category %}<li><strong>Category:</strong> {{ category }}</li>{% endif %}
            <li><strong>Outcome:</strong> Not approved at this time</li>
        </ul>
        <p>
            Thank you for recognising your colleague. You are encouraged to
            continue nominating outstanding contributors.
        </p>
        <hr style="margin: 20px 0;">
        <p style="color: #7f8c8d; font-size: 12px;">
            This is an automated message from the Award Nomination System.
        </p>
    </body>
    </html>
    """,
    },

    # ── Beneficiary: award received ───────────────────────────────────────────
    "beneficiary_award": {
        "subject": "🏆 You've received an award — congratulations, {{ beneficiary_name }}!",
        "body": """
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
    </head>
    <body style="font-family:Arial,sans-serif;line-height:1.6;color:#333;
                 max-width:600px;margin:0 auto;padding:20px;">
        <div style="background:#f0fff4;border-radius:10px;padding:30px;margin-bottom:20px;
                    border:1px solid #c6f6d5;">
            <h2 style="color:#27ae60;margin-top:0;">🏆 Congratulations, {{ beneficiary_name }}!</h2>
            <p style="font-size:16px;">
                You have been recognised with a monetary award of
                <strong>{{ formatted_amount }}</strong> for your outstanding contribution.
            </p>
        </div>

        <div style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;
                    padding:20px;margin-bottom:20px;">
            <h3 style="color:#2c3e50;margin-top:0;">🎁 Award Details</h3>
            <ul style="padding-left:20px;">
                <li><strong>Award:</strong> Monetary Award ({{ formatted_amount }})</li>
                {% if category %}<li><strong>Category:</strong> {{ category }}</li>{% endif %}
                {% if nominator_name %}<li><strong>Recognised by:</strong> {{ nominator_name }}</li>{% endif %}
            </ul>
        </div>

        <p style="font-size:15px;">
            Thank you for the great work that earned this recognition. Your
            manager will be in touch and may present you with an award certificate.
        </p>

        <hr style="border:none;border-top:1px solid #e0e0e0;margin:30px 0;">
        <p style="color:#7f8c8d;font-size:12px;text-align:center;">
            This is an automated message from the Award Nomination System.<br>
            Please do not reply to this email.
        </p>
    </body>
    </html>
    """,
    },
}
