"""
Default (TenantId=1, Lang='en') email template seed data.

Each value is a Jinja2 source string converted 1:1 from the legacy hard-coded
templates in auxiliary-service/email_client.py. The amount is passed in
pre-formatted as `formatted_amount`; risk colour / check label / fraud score are
likewise passed pre-computed by the handler. Bodies are HTML (autoescaped at
render time — a security upgrade over the old f-strings); subjects are plain text.

Context variables per key:
  nomination_pending   : manager_name, nominator_name, beneficiary_name,
                         formatted_amount, description, approve_url, reject_url, category
  nomination_approved  : beneficiary_name, formatted_amount, category
  nomination_rejected  : beneficiary_name, formatted_amount, category
  beneficiary_award    : beneficiary_name, formatted_amount, nominator_name, category
  payment_confirmed    : beneficiary_name, formatted_amount, payment_ref
  demo_access_invite   : first_name, redeem_url
  hrbp_review_request  : hrbp_name, nomination_id, nominator_name, beneficiary_name,
                         formatted_amount, description, risk_level, risk_color,
                         fraud_score, warning_flags, portal_url
  hrbp_approved        : nominator_name, beneficiary_name, formatted_amount
  hrbp_rejected        : nominator_name, beneficiary_name, formatted_amount, reason
  hrbp_sla_breach      : recipient_name, nomination_id, nominator_name, beneficiary_name,
                         risk_level, risk_color, nomination_date, sla_hours
  description_rejected : nominator_name, beneficiary_name, formatted_amount,
                         check_label, reason, category_description, nomination_description
"""

EN_TEMPLATES: dict[str, dict[str, str]] = {
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

    "payment_confirmed": {
        "subject": "💳 Payment Confirmed — {{ beneficiary_name }}",
        "body": """
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #2980b9;">💳 Payment Processed</h2>
        <p>The monetary award for your approved nomination has been paid:</p>
        <ul>
            <li><strong>Nominee:</strong> {{ beneficiary_name }}</li>
            <li><strong>Amount:</strong> {{ formatted_amount }}</li>
            <li><strong>Payment Reference:</strong> {{ payment_ref }}</li>
        </ul>
        <p>The payment has been submitted to payroll and will appear on the next pay run.</p>
        <hr style="margin: 20px 0;">
        <p style="color: #7f8c8d; font-size: 12px;">
            This is an automated message from the Award Nomination System.
        </p>
    </body>
    </html>
    """,
    },

    "demo_access_invite": {
        "subject": "Your Award Nominations demo access is ready",
        "body": """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
    </head>
    <body style="margin:0;padding:0;background-color:#f3f4f6;font-family:Arial,sans-serif;">
      <table width="100%" cellpadding="0" cellspacing="0"
             style="background-color:#f3f4f6;padding:40px 0;">
        <tr><td align="center">
          <table width="580" cellpadding="0" cellspacing="0"
                 style="background:#ffffff;border-radius:12px;overflow:hidden;
                        box-shadow:0 2px 8px rgba(0,0,0,.08);max-width:580px;">

            <!-- Header -->
            <tr>
              <td style="background:linear-gradient(135deg,#4f46e5 0%,#7c3aed 100%);
                         padding:36px 40px;text-align:center;">
                <div style="font-size:48px;margin-bottom:12px;">🏆</div>
                <h1 style="margin:0;color:#ffffff;font-size:24px;font-weight:700;
                           letter-spacing:-0.3px;">
                  Award Nominations
                </h1>
                <p style="margin:6px 0 0;color:#c7d2fe;font-size:14px;">
                  Demo Environment
                </p>
              </td>
            </tr>

            <!-- Body -->
            <tr>
              <td style="padding:36px 40px;">
                <p style="margin:0 0 16px;font-size:16px;color:#111827;font-weight:600;">
                  Hi {{ first_name }},
                </p>
                <p style="margin:0 0 16px;font-size:15px;color:#374151;line-height:1.7;">
                  Your demo access request has been approved! You're about to explore
                  a <strong>live SaaS platform</strong> for employee recognition and
                  monetary award management.
                </p>
                <p style="margin:0 0 28px;font-size:15px;color:#374151;line-height:1.7;">
                  Click the button below to activate your account. You'll be guided
                  through sign-in automatically — no password setup required.
                </p>

                <!-- CTA -->
                <table cellpadding="0" cellspacing="0" style="margin:0 auto 36px;">
                  <tr>
                    <td style="background:#4f46e5;border-radius:8px;
                               box-shadow:0 4px 12px rgba(79,70,229,.35);">
                      <a href="{{ redeem_url }}"
                         style="display:inline-block;padding:16px 40px;
                                color:#ffffff;font-size:16px;font-weight:700;
                                text-decoration:none;letter-spacing:0.2px;">
                        Activate My Demo Access &rarr;
                      </a>
                    </td>
                  </tr>
                </table>

                <!-- Feature tiles -->
                <table cellpadding="0" cellspacing="0" width="100%"
                       style="border:1px solid #e5e7eb;border-radius:10px;
                              overflow:hidden;margin-bottom:28px;">
                  <tr>
                    <td style="background:#f5f3ff;padding:18px 24px;
                               border-bottom:1px solid #e5e7eb;">
                      <p style="margin:0;font-size:13px;font-weight:700;
                                color:#6d28d9;text-transform:uppercase;
                                letter-spacing:0.5px;">
                        What's included in your demo
                      </p>
                    </td>
                  </tr>
                  <tr>
                    <td style="padding:6px 0;">
                      <table cellpadding="0" cellspacing="0" width="100%">
                        <tr>
                          <td style="padding:10px 24px;font-size:14px;color:#374151;">
                            <span style="color:#4f46e5;font-weight:700;margin-right:10px;">✓</span>
                            Submit and approve award nominations
                          </td>
                        </tr>
                        <tr>
                          <td style="padding:10px 24px;font-size:14px;color:#374151;
                                     background:#fafafa;">
                            <span style="color:#4f46e5;font-weight:700;margin-right:10px;">✓</span>
                            Real-time analytics and spending trends
                          </td>
                        </tr>
                        <tr>
                          <td style="padding:10px 24px;font-size:14px;color:#374151;">
                            <span style="color:#4f46e5;font-weight:700;margin-right:10px;">✓</span>
                            AI-powered fraud detection engine
                          </td>
                        </tr>
                        <tr>
                          <td style="padding:10px 24px;font-size:14px;color:#374151;
                                     background:#fafafa;">
                            <span style="color:#4f46e5;font-weight:700;margin-right:10px;">✓</span>
                            Impersonate any demo user to explore different roles
                          </td>
                        </tr>
                      </table>
                    </td>
                  </tr>
                </table>

                <!-- Expiry note -->
                <div style="background:#fffbeb;border-left:4px solid #f59e0b;
                            border-radius:4px;padding:14px 18px;">
                  <p style="margin:0;font-size:13px;color:#92400e;">
                    <strong>⏰ Note:</strong> This invitation link expires in 30 days.
                    If you didn't request demo access, you can safely ignore this email.
                  </p>
                </div>
              </td>
            </tr>

            <!-- Footer -->
            <tr>
              <td style="padding:20px 40px;border-top:1px solid #e5e7eb;
                         text-align:center;background:#f9fafb;">
                <p style="margin:0;font-size:12px;color:#9ca3af;line-height:1.6;">
                  This is an automated message from the Award Nominations demo environment.<br>
                  Please do not reply to this email.
                </p>
              </td>
            </tr>

          </table>
        </td></tr>
      </table>
    </body>
    </html>
    """,
    },

    "hrbp_review_request": {
        "subject": "⚠️ HRBP Review Required — Nomination #{{ nomination_id }} ({{ risk_level }} Risk)",
        "body": """
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
    </head>
    <body style="font-family:Arial,sans-serif;line-height:1.6;color:#333;
                 max-width:600px;margin:0 auto;padding:20px;">
        <div style="background:#f8f9fa;border-radius:10px;padding:30px;margin-bottom:20px;">
            <h2 style="color:#2c3e50;margin-top:0;">🔍 HRBP Review Required</h2>
            <p style="font-size:16px;">Dear <strong>{{ hrbp_name }}</strong>,</p>
            <p style="font-size:16px;">
                The fraud detection system has flagged nomination
                <strong>#{{ nomination_id }}</strong> for your review before it proceeds
                to manager approval.
            </p>
        </div>

        <div style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;
                    padding:20px;margin-bottom:20px;">
            <h3 style="color:#2c3e50;margin-top:0;">📋 Nomination Details</h3>
            <ul style="padding-left:20px;">
                <li><strong>Nominator:</strong> {{ nominator_name }}</li>
                <li><strong>Nominee:</strong> {{ beneficiary_name }}</li>
                <li><strong>Amount:</strong> {{ formatted_amount }}</li>
            </ul>
            <p style="background:#f8f9fa;padding:12px;border-radius:5px;
                      border-left:4px solid #3498db;margin:0;">{{ description }}</p>
        </div>

        <div style="background:#fff;border:2px solid {{ risk_color }};border-radius:8px;
                    padding:20px;margin-bottom:20px;">
            <h3 style="color:{{ risk_color }};margin-top:0;">⚠️ Risk Assessment</h3>
            <ul style="padding-left:20px;">
                <li><strong>Risk Level:</strong>
                    <span style="color:{{ risk_color }};font-weight:bold;">{{ risk_level }}</span>
                </li>
                {% if fraud_score is not none %}<li><strong>Fraud Score:</strong> {{ "%.3f"|format(fraud_score) }}</li>{% endif %}
            </ul>
            {% if warning_flags %}
        <div style="background:#fff3cd;border-left:4px solid #f39c12;
                    padding:12px 16px;border-radius:4px;margin:16px 0;">
            <strong>⚠️ Warning Flags:</strong>
            <ul style="margin:8px 0 0;padding-left:20px;">{% for f in warning_flags %}<li>{{ f }}</li>{% endfor %}</ul>
        </div>{% endif %}
        </div>

        <div style="background:#e8f4fd;border-left:4px solid #3498db;
                    padding:15px;border-radius:4px;margin-bottom:20px;">
            <p style="margin:0;font-size:14px;">
                <strong>Action required:</strong> Please log into the
                {% if portal_url %}<a href="{{ portal_url }}" style="color:#2980b9;font-weight:bold;">Award Nominations portal</a>{% else %}<strong>Award Nominations portal</strong>{% endif %}
                to review the full nomination details and either approve or
                reject the nomination. The nominator will be notified of your decision.
            </p>
        </div>

        <hr style="border:none;border-top:1px solid #e0e0e0;margin:30px 0;">
        <p style="color:#7f8c8d;font-size:12px;text-align:center;">
            This is an automated message from the Award Nomination System.<br>
            Please do not reply to this email.
        </p>
    </body>
    </html>
    """,
    },

    "hrbp_approved": {
        "subject": "Nomination Update — {{ beneficiary_name }}",
        "body": """
    <!DOCTYPE html>
    <html>
    <body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;">
        <div style="background:#f8f9fa;border-radius:10px;padding:30px;margin-bottom:20px;">
            <h2 style="color:#27ae60;margin-top:0;">✅ Nomination Cleared HR Review</h2>
            <p style="font-size:16px;">Dear <strong>{{ nominator_name }}</strong>,</p>
            <p style="font-size:16px;">
                Your nomination for <strong>{{ beneficiary_name }}</strong>
                ({{ formatted_amount }}) has been reviewed and approved by the HR team.
            </p>
        </div>
        <p style="font-size:15px;">
            Your nomination has now been forwarded to the relevant manager for
            final approval. You will receive another notification once the manager
            has made their decision.
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

    "hrbp_rejected": {
        "subject": "Nomination Not Approved — {{ beneficiary_name }}",
        "body": """
    <!DOCTYPE html>
    <html>
    <body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;">
        <div style="background:#f8f9fa;border-radius:10px;padding:30px;margin-bottom:20px;">
            <h2 style="color:#e74c3c;margin-top:0;">Nomination Not Approved</h2>
            <p style="font-size:16px;">Dear <strong>{{ nominator_name }}</strong>,</p>
            <p style="font-size:16px;">
                Your nomination for <strong>{{ beneficiary_name }}</strong>
                ({{ formatted_amount }}) was reviewed by the HR team and has not
                been approved to proceed at this time.
            </p>
        </div>
        {% if reason %}<div style="background:#f8f9fa;padding:12px;border-radius:5px;
                        border-left:4px solid #e74c3c;margin:16px 0;">
                <strong>Reason provided:</strong><br>{{ reason }}
            </div>{% endif %}
        <p style="font-size:15px;">
            Thank you for recognising your colleague. You are encouraged to
            continue nominating outstanding contributors.
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

    "hrbp_sla_breach": {
        "subject": "🚨 SLA Breach — Nomination #{{ nomination_id }} Awaiting HRBP Review",
        "body": """
    <!DOCTYPE html>
    <html>
    <body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;">
        <div style="background:#fdf2f2;border:2px solid #e74c3c;border-radius:10px;
                    padding:30px;margin-bottom:20px;">
            <h2 style="color:#c0392b;margin-top:0;">🚨 SLA Breach — HRBP Review Overdue</h2>
            <p style="font-size:16px;">Dear <strong>{{ recipient_name }}</strong>,</p>
            <p style="font-size:16px;">
                Nomination <strong>#{{ nomination_id }}</strong> has been awaiting HRBP
                review for more than <strong>{{ sla_hours }} hours</strong> and requires
                your immediate attention.
            </p>
        </div>

        <div style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;
                    padding:20px;margin-bottom:20px;">
            <h3 style="color:#2c3e50;margin-top:0;">📋 Nomination Details</h3>
            <ul style="padding-left:20px;">
                <li><strong>Nomination ID:</strong> #{{ nomination_id }}</li>
                <li><strong>Nominator:</strong> {{ nominator_name }}</li>
                <li><strong>Nominee:</strong> {{ beneficiary_name }}</li>
                <li><strong>Submitted:</strong> {{ nomination_date }}</li>
                <li><strong>Risk Level:</strong>
                    <span style="color:{{ risk_color }};font-weight:bold;">{{ risk_level }}</span>
                </li>
            </ul>
        </div>

        <div style="background:#fff3cd;border-left:4px solid #ffc107;
                    padding:15px;border-radius:4px;margin-bottom:20px;">
            <p style="margin:0;font-size:14px;">
                <strong>⏰ Action required:</strong> Please log into the Award Nominations
                portal and review this nomination as soon as possible to avoid
                further escalation.
            </p>
        </div>

        <hr style="border:none;border-top:1px solid #e0e0e0;margin:30px 0;">
        <p style="color:#7f8c8d;font-size:12px;text-align:center;">
            This is an automated message from the Award Nomination System.<br>
            Please do not reply to this email.
        </p>
    </body>
    </html>
    """,
    },

    "description_rejected": {
        "subject": "Action Required: Please Resubmit Your Nomination for {{ beneficiary_name }}",
        "body": """
    <!DOCTYPE html>
    <html>
    <body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;">
        <div style="background:#f8f9fa;border-radius:10px;padding:30px;margin-bottom:20px;">
            <h2 style="color:#e67e22;margin-top:0;">Nomination Requires a Better Description</h2>
            <p style="font-size:16px;">Dear <strong>{{ nominator_name }}</strong>,</p>
            <p style="font-size:16px;">
                Your nomination for <strong>{{ beneficiary_name }}</strong>
                ({{ formatted_amount }}) was not accepted because the description
                did not meet our quality requirements.
            </p>
        </div>

        <div style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;
                    padding:20px;margin-bottom:20px;">
            <h3 style="color:#2c3e50;margin-top:0;">📋 Nomination Details</h3>
            <ul style="padding-left:20px;">
                <li><strong>Nominee:</strong> {{ beneficiary_name }}</li>
                <li><strong>Award Amount:</strong> {{ formatted_amount }}</li>
                {% if category_description %}<li><strong>Category:</strong> {{ category_description }}</li>{% endif %}
            </ul>
        </div>

        {% if nomination_description %}<div style="background:#f4f6f8;border:1px solid #d0d7de;border-radius:8px;
                    padding:20px;margin-bottom:20px;">
            <h3 style="color:#2c3e50;margin-top:0;">Your Original Description</h3>
            <p style="font-size:14px;white-space:pre-wrap;margin:0;">{{ nomination_description }}</p>
        </div>{% endif %}

        <div style="background:#fff8f0;border-left:4px solid #e67e22;
                    padding:15px;border-radius:4px;margin-bottom:20px;">
            <p style="margin:0 0 8px 0;font-size:14px;">
                <strong>⚠ Issue: {{ check_label }}</strong>
            </p>
            <p style="margin:0;font-size:14px;">{{ reason }}</p>
        </div>

        <p style="font-size:15px;">
            You are welcome to resubmit this nomination with an improved description.
            A good description explains <em>what</em> the person did and
            <em>why</em> it was impactful — specific examples make a strong case.
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
