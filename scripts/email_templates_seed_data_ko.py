"""
Korean (Lang='ko', TenantId=2) email template seed data.

AI-DRAFTED — please have a native Korean speaker review before production.
Structure, HTML, and every {{ placeholder }} / {% block %} are kept identical to
the English defaults so the resolver and handlers work unchanged; only the
human-readable text is translated. Dynamic values that arrive already-composed
in their source language (e.g. `reason`, `check_label`) are not translated here.
"""

KO_TEMPLATES: dict[str, dict[str, str]] = {
    "nomination_pending": {
        "subject": "포상 추천 승인 대기 — {{ beneficiary_name }}",
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
            <h2 style="color: #2c3e50; margin-top: 0;">🔔 새로운 포상 추천 승인 대기</h2>
            <p style="font-size: 16px;"><strong>{{ manager_name }}</strong>님께,</p>
            <p style="font-size: 16px;">
                <strong>{{ nominator_name }}</strong>님이 <strong>{{ beneficiary_name }}</strong>님을
                <strong>{{ formatted_amount }}</strong>의 포상 후보로 추천하였습니다.
            </p>
            {% if category %}<p style="margin: 4px 0 0;"><strong>분류:</strong> {{ category }}</p>{% endif %}
        </div>

        <div style="background-color: #fff; border: 1px solid #e0e0e0; border-radius: 8px;
                    padding: 20px; margin-bottom: 30px;">
            <h3 style="color: #2c3e50; margin-top: 0;">📝 추천 상세 내용:</h3>
            <p style="background-color: #f8f9fa; padding: 15px; border-radius: 5px;
                      border-left: 4px solid #3498db;">
                {{ description }}
            </p>
        </div>

        <div style="text-align: center; margin: 40px 0;">
            <p style="font-size: 16px; margin-bottom: 20px;"><strong>조치하기:</strong></p>
            <a href="{{ approve_url }}"
               style="display: inline-block; background-color: #27ae60; color: white;
                      padding: 15px 40px; text-decoration: none; border-radius: 5px;
                      font-weight: bold; font-size: 16px; margin: 0 10px 10px 0;
                      box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                ✅ 승인
            </a>
            <a href="{{ reject_url }}"
               style="display: inline-block; background-color: #e74c3c; color: white;
                      padding: 15px 40px; text-decoration: none; border-radius: 5px;
                      font-weight: bold; font-size: 16px; margin: 0 0 10px 10px;
                      box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                ❌ 반려
            </a>
        </div>

        <div style="background-color: #fff3cd; border-left: 4px solid #ffc107;
                    padding: 15px; margin: 20px 0; border-radius: 4px;">
            <p style="margin: 0; font-size: 14px;">
                <strong>⏰ 안내:</strong> 이 승인 링크는 72시간 후 만료됩니다.
                포상 추천 시스템에 로그인하여 승인 또는 반려하실 수도 있습니다.
            </p>
        </div>

        <hr style="border: none; border-top: 1px solid #e0e0e0; margin: 30px 0;">
        <p style="color: #7f8c8d; font-size: 12px; text-align: center;">
            본 메일은 포상 추천 시스템에서 자동 발송되었습니다.<br>
            회신하지 마시기 바랍니다.
        </p>
    </body>
    </html>
    """,
    },

    "nomination_approved": {
        "subject": "✅ 추천 승인됨 — {{ beneficiary_name }}",
        "body": """
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #27ae60;">🎉 추천이 승인되었습니다!</h2>
        <p>기쁜 소식입니다! 회원님의 추천이 승인되었습니다:</p>
        <ul>
            <li><strong>수상자:</strong> {{ beneficiary_name }}</li>
            <li><strong>포상:</strong> 포상금 ({{ formatted_amount }})</li>
            {% if category %}<li><strong>분류:</strong> {{ category }}</li>{% endif %}
        </ul>
        <p>수상자에게 이 소식이 안내될 예정입니다.</p>
        <hr style="margin: 20px 0;">
        <p style="color: #7f8c8d; font-size: 12px;">
            본 메일은 포상 추천 시스템에서 자동 발송되었습니다.
        </p>
    </body>
    </html>
    """,
    },

    "nomination_rejected": {
        "subject": "추천 상태 안내 — {{ beneficiary_name }}",
        "body": """
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #e74c3c;">추천 상태 안내</h2>
        <p>회원님의 추천이 검토되었습니다:</p>
        <ul>
            <li><strong>수상자:</strong> {{ beneficiary_name }}</li>
            <li><strong>포상:</strong> 포상금 ({{ formatted_amount }})</li>
            {% if category %}<li><strong>분류:</strong> {{ category }}</li>{% endif %}
            <li><strong>결과:</strong> 현재로서는 승인되지 않음</li>
        </ul>
        <p>
            동료를 인정해 주셔서 감사합니다. 앞으로도 우수한 기여자를 계속
            추천해 주시기 바랍니다.
        </p>
        <hr style="margin: 20px 0;">
        <p style="color: #7f8c8d; font-size: 12px;">
            본 메일은 포상 추천 시스템에서 자동 발송되었습니다.
        </p>
    </body>
    </html>
    """,
    },

    "beneficiary_award": {
        "subject": "🏆 포상을 받으셨습니다 — 축하드립니다, {{ beneficiary_name }}님!",
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
            <h2 style="color:#27ae60;margin-top:0;">🏆 축하드립니다, {{ beneficiary_name }}님!</h2>
            <p style="font-size:16px;">
                뛰어난 기여를 인정받아 <strong>{{ formatted_amount }}</strong>의
                포상을 받으셨습니다.
            </p>
        </div>

        <div style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;
                    padding:20px;margin-bottom:20px;">
            <h3 style="color:#2c3e50;margin-top:0;">🎁 포상 상세 내용</h3>
            <ul style="padding-left:20px;">
                <li><strong>포상:</strong> 포상금 ({{ formatted_amount }})</li>
                {% if category %}<li><strong>분류:</strong> {{ category }}</li>{% endif %}
                {% if nominator_name %}<li><strong>추천인:</strong> {{ nominator_name }}</li>{% endif %}
            </ul>
        </div>

        <p style="font-size:15px;">
            이번 인정을 이끌어낸 훌륭한 성과에 감사드립니다. 담당 관리자가 곧
            연락드릴 예정이며, 포상 증서를 전달드릴 수 있습니다.
        </p>

        <hr style="border:none;border-top:1px solid #e0e0e0;margin:30px 0;">
        <p style="color:#7f8c8d;font-size:12px;text-align:center;">
            본 메일은 포상 추천 시스템에서 자동 발송되었습니다.<br>
            회신하지 마시기 바랍니다.
        </p>
    </body>
    </html>
    """,
    },

    "payment_confirmed": {
        "subject": "💳 지급 완료 — {{ beneficiary_name }}",
        "body": """
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #2980b9;">💳 지급이 완료되었습니다</h2>
        <p>승인된 추천에 대한 포상금이 지급되었습니다:</p>
        <ul>
            <li><strong>수상자:</strong> {{ beneficiary_name }}</li>
            <li><strong>금액:</strong> {{ formatted_amount }}</li>
            <li><strong>지급 참조번호:</strong> {{ payment_ref }}</li>
        </ul>
        <p>지급 내역이 급여팀으로 전달되었으며, 다음 급여 지급 시 반영됩니다.</p>
        <hr style="margin: 20px 0;">
        <p style="color: #7f8c8d; font-size: 12px;">
            본 메일은 포상 추천 시스템에서 자동 발송되었습니다.
        </p>
    </body>
    </html>
    """,
    },

    "demo_access_invite": {
        "subject": "Award Nominations 데모 액세스가 준비되었습니다",
        "body": """
    <!DOCTYPE html>
    <html lang="ko">
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
                  데모 환경
                </p>
              </td>
            </tr>

            <!-- Body -->
            <tr>
              <td style="padding:36px 40px;">
                <p style="margin:0 0 16px;font-size:16px;color:#111827;font-weight:600;">
                  {{ first_name }}님, 안녕하세요,
                </p>
                <p style="margin:0 0 16px;font-size:15px;color:#374151;line-height:1.7;">
                  데모 액세스 요청이 승인되었습니다! 직원 인정 및 포상 관리를 위한
                  <strong>실제 SaaS 플랫폼</strong>을 곧 체험하실 수 있습니다.
                </p>
                <p style="margin:0 0 28px;font-size:15px;color:#374151;line-height:1.7;">
                  아래 버튼을 클릭하여 계정을 활성화하세요. 로그인은 자동으로
                  안내되며, 비밀번호 설정은 필요하지 않습니다.
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
                        데모 액세스 활성화 &rarr;
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
                        데모에 포함된 기능
                      </p>
                    </td>
                  </tr>
                  <tr>
                    <td style="padding:6px 0;">
                      <table cellpadding="0" cellspacing="0" width="100%">
                        <tr>
                          <td style="padding:10px 24px;font-size:14px;color:#374151;">
                            <span style="color:#4f46e5;font-weight:700;margin-right:10px;">✓</span>
                            포상 추천 제출 및 승인
                          </td>
                        </tr>
                        <tr>
                          <td style="padding:10px 24px;font-size:14px;color:#374151;
                                     background:#fafafa;">
                            <span style="color:#4f46e5;font-weight:700;margin-right:10px;">✓</span>
                            실시간 분석 및 지출 추이
                          </td>
                        </tr>
                        <tr>
                          <td style="padding:10px 24px;font-size:14px;color:#374151;">
                            <span style="color:#4f46e5;font-weight:700;margin-right:10px;">✓</span>
                            AI 기반 부정 탐지 엔진
                          </td>
                        </tr>
                        <tr>
                          <td style="padding:10px 24px;font-size:14px;color:#374151;
                                     background:#fafafa;">
                            <span style="color:#4f46e5;font-weight:700;margin-right:10px;">✓</span>
                            여러 역할을 체험하기 위한 데모 사용자 가장 기능
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
                    <strong>⏰ 안내:</strong> 이 초대 링크는 30일 후 만료됩니다.
                    데모 액세스를 요청하지 않으셨다면 본 메일을 무시하셔도 됩니다.
                  </p>
                </div>
              </td>
            </tr>

            <!-- Footer -->
            <tr>
              <td style="padding:20px 40px;border-top:1px solid #e5e7eb;
                         text-align:center;background:#f9fafb;">
                <p style="margin:0;font-size:12px;color:#9ca3af;line-height:1.6;">
                  본 메일은 Award Nominations 데모 환경에서 자동 발송되었습니다.<br>
                  회신하지 마시기 바랍니다.
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
        "subject": "⚠️ HRBP 검토 필요 — 추천 #{{ nomination_id }} ({{ risk_level }} 위험)",
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
            <h2 style="color:#2c3e50;margin-top:0;">🔍 HRBP 검토 필요</h2>
            <p style="font-size:16px;"><strong>{{ hrbp_name }}</strong>님께,</p>
            <p style="font-size:16px;">
                부정 탐지 시스템이 추천 <strong>#{{ nomination_id }}</strong>을(를)
                관리자 승인 전 검토 대상으로 표시하였습니다.
            </p>
        </div>

        <div style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;
                    padding:20px;margin-bottom:20px;">
            <h3 style="color:#2c3e50;margin-top:0;">📋 추천 상세 내용</h3>
            <ul style="padding-left:20px;">
                <li><strong>추천인:</strong> {{ nominator_name }}</li>
                <li><strong>수상자:</strong> {{ beneficiary_name }}</li>
                <li><strong>금액:</strong> {{ formatted_amount }}</li>
            </ul>
            <p style="background:#f8f9fa;padding:12px;border-radius:5px;
                      border-left:4px solid #3498db;margin:0;">{{ description }}</p>
        </div>

        <div style="background:#fff;border:2px solid {{ risk_color }};border-radius:8px;
                    padding:20px;margin-bottom:20px;">
            <h3 style="color:{{ risk_color }};margin-top:0;">⚠️ 위험 평가</h3>
            <ul style="padding-left:20px;">
                <li><strong>위험 수준:</strong>
                    <span style="color:{{ risk_color }};font-weight:bold;">{{ risk_level }}</span>
                </li>
                {% if fraud_score is not none %}<li><strong>부정 점수:</strong> {{ "%.3f"|format(fraud_score) }}</li>{% endif %}
            </ul>
            {% if warning_flags %}
        <div style="background:#fff3cd;border-left:4px solid #f39c12;
                    padding:12px 16px;border-radius:4px;margin:16px 0;">
            <strong>⚠️ 경고 플래그:</strong>
            <ul style="margin:8px 0 0;padding-left:20px;">{% for f in warning_flags %}<li>{{ f }}</li>{% endfor %}</ul>
        </div>{% endif %}
        </div>

        <div style="background:#e8f4fd;border-left:4px solid #3498db;
                    padding:15px;border-radius:4px;margin-bottom:20px;">
            <p style="margin:0;font-size:14px;">
                <strong>조치 필요:</strong>
                {% if portal_url %}<a href="{{ portal_url }}" style="color:#2980b9;font-weight:bold;">Award Nominations 포털</a>{% else %}<strong>Award Nominations 포털</strong>{% endif %}에
                로그인하여 추천 상세 내용을 검토하고 승인 또는 반려해 주시기
                바랍니다. 추천인에게 결정 사항이 안내됩니다.
            </p>
        </div>

        <hr style="border:none;border-top:1px solid #e0e0e0;margin:30px 0;">
        <p style="color:#7f8c8d;font-size:12px;text-align:center;">
            본 메일은 포상 추천 시스템에서 자동 발송되었습니다.<br>
            회신하지 마시기 바랍니다.
        </p>
    </body>
    </html>
    """,
    },

    "hrbp_approved": {
        "subject": "추천 진행 안내 — {{ beneficiary_name }}",
        "body": """
    <!DOCTYPE html>
    <html>
    <body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;">
        <div style="background:#f8f9fa;border-radius:10px;padding:30px;margin-bottom:20px;">
            <h2 style="color:#27ae60;margin-top:0;">✅ 추천이 HR 검토를 통과했습니다</h2>
            <p style="font-size:16px;"><strong>{{ nominator_name }}</strong>님께,</p>
            <p style="font-size:16px;">
                <strong>{{ beneficiary_name }}</strong>님에 대한 회원님의 추천
                ({{ formatted_amount }})이 HR 팀의 검토를 거쳐 승인되었습니다.
            </p>
        </div>
        <p style="font-size:15px;">
            이제 추천이 최종 승인을 위해 담당 관리자에게 전달되었습니다. 관리자가
            결정을 내리면 다시 안내드리겠습니다.
        </p>
        <hr style="border:none;border-top:1px solid #e0e0e0;margin:30px 0;">
        <p style="color:#7f8c8d;font-size:12px;text-align:center;">
            본 메일은 포상 추천 시스템에서 자동 발송되었습니다.<br>
            회신하지 마시기 바랍니다.
        </p>
    </body>
    </html>
    """,
    },

    "hrbp_rejected": {
        "subject": "추천 미승인 — {{ beneficiary_name }}",
        "body": """
    <!DOCTYPE html>
    <html>
    <body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;">
        <div style="background:#f8f9fa;border-radius:10px;padding:30px;margin-bottom:20px;">
            <h2 style="color:#e74c3c;margin-top:0;">추천 미승인</h2>
            <p style="font-size:16px;"><strong>{{ nominator_name }}</strong>님께,</p>
            <p style="font-size:16px;">
                <strong>{{ beneficiary_name }}</strong>님에 대한 회원님의 추천
                ({{ formatted_amount }})이 HR 팀의 검토 결과, 현재로서는 진행이
                승인되지 않았습니다.
            </p>
        </div>
        {% if reason %}<div style="background:#f8f9fa;padding:12px;border-radius:5px;
                        border-left:4px solid #e74c3c;margin:16px 0;">
                <strong>사유:</strong><br>{{ reason }}
            </div>{% endif %}
        <p style="font-size:15px;">
            동료를 인정해 주셔서 감사합니다. 앞으로도 우수한 기여자를 계속
            추천해 주시기 바랍니다.
        </p>
        <hr style="border:none;border-top:1px solid #e0e0e0;margin:30px 0;">
        <p style="color:#7f8c8d;font-size:12px;text-align:center;">
            본 메일은 포상 추천 시스템에서 자동 발송되었습니다.<br>
            회신하지 마시기 바랍니다.
        </p>
    </body>
    </html>
    """,
    },

    "hrbp_sla_breach": {
        "subject": "🚨 SLA 위반 — 추천 #{{ nomination_id }} HRBP 검토 대기 중",
        "body": """
    <!DOCTYPE html>
    <html>
    <body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;">
        <div style="background:#fdf2f2;border:2px solid #e74c3c;border-radius:10px;
                    padding:30px;margin-bottom:20px;">
            <h2 style="color:#c0392b;margin-top:0;">🚨 SLA 위반 — HRBP 검토 지연</h2>
            <p style="font-size:16px;"><strong>{{ recipient_name }}</strong>님께,</p>
            <p style="font-size:16px;">
                추천 <strong>#{{ nomination_id }}</strong>이(가) <strong>{{ sla_hours }}시간</strong>
                이상 HRBP 검토를 기다리고 있어 즉각적인 조치가 필요합니다.
            </p>
        </div>

        <div style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;
                    padding:20px;margin-bottom:20px;">
            <h3 style="color:#2c3e50;margin-top:0;">📋 추천 상세 내용</h3>
            <ul style="padding-left:20px;">
                <li><strong>추천 번호:</strong> #{{ nomination_id }}</li>
                <li><strong>추천인:</strong> {{ nominator_name }}</li>
                <li><strong>수상자:</strong> {{ beneficiary_name }}</li>
                <li><strong>제출일:</strong> {{ nomination_date }}</li>
                <li><strong>위험 수준:</strong>
                    <span style="color:{{ risk_color }};font-weight:bold;">{{ risk_level }}</span>
                </li>
            </ul>
        </div>

        <div style="background:#fff3cd;border-left:4px solid #ffc107;
                    padding:15px;border-radius:4px;margin-bottom:20px;">
            <p style="margin:0;font-size:14px;">
                <strong>⏰ 조치 필요:</strong> 추가 에스컬레이션을 방지하기 위해
                가능한 한 빨리 Award Nominations 포털에 로그인하여 이 추천을
                검토해 주시기 바랍니다.
            </p>
        </div>

        <hr style="border:none;border-top:1px solid #e0e0e0;margin:30px 0;">
        <p style="color:#7f8c8d;font-size:12px;text-align:center;">
            본 메일은 포상 추천 시스템에서 자동 발송되었습니다.<br>
            회신하지 마시기 바랍니다.
        </p>
    </body>
    </html>
    """,
    },

    "description_rejected": {
        "subject": "조치 필요: {{ beneficiary_name }}님 추천을 다시 제출해 주세요",
        "body": """
    <!DOCTYPE html>
    <html>
    <body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;">
        <div style="background:#f8f9fa;border-radius:10px;padding:30px;margin-bottom:20px;">
            <h2 style="color:#e67e22;margin-top:0;">추천 사유를 보완해 주세요</h2>
            <p style="font-size:16px;"><strong>{{ nominator_name }}</strong>님께,</p>
            <p style="font-size:16px;">
                <strong>{{ beneficiary_name }}</strong>님에 대한 회원님의 추천
                ({{ formatted_amount }})이 사유 설명이 품질 기준을 충족하지 못하여
                접수되지 않았습니다.
            </p>
        </div>

        <div style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;
                    padding:20px;margin-bottom:20px;">
            <h3 style="color:#2c3e50;margin-top:0;">📋 추천 상세 내용</h3>
            <ul style="padding-left:20px;">
                <li><strong>수상자:</strong> {{ beneficiary_name }}</li>
                <li><strong>포상 금액:</strong> {{ formatted_amount }}</li>
                {% if category_description %}<li><strong>분류:</strong> {{ category_description }}</li>{% endif %}
            </ul>
        </div>

        {% if nomination_description %}<div style="background:#f4f6f8;border:1px solid #d0d7de;border-radius:8px;
                    padding:20px;margin-bottom:20px;">
            <h3 style="color:#2c3e50;margin-top:0;">회원님이 작성한 사유</h3>
            <p style="font-size:14px;white-space:pre-wrap;margin:0;">{{ nomination_description }}</p>
        </div>{% endif %}

        <div style="background:#fff8f0;border-left:4px solid #e67e22;
                    padding:15px;border-radius:4px;margin-bottom:20px;">
            <p style="margin:0 0 8px 0;font-size:14px;">
                <strong>⚠ 문제: {{ check_label }}</strong>
            </p>
            <p style="margin:0;font-size:14px;">{{ reason }}</p>
        </div>

        <p style="font-size:15px;">
            사유를 보완하여 추천을 다시 제출하실 수 있습니다. 좋은 사유는 그 사람이
            <em>무엇을</em> 했는지와 그것이 <em>왜</em> 큰 영향을 주었는지를
            설명하며, 구체적인 예시가 설득력을 높입니다.
        </p>

        <hr style="border:none;border-top:1px solid #e0e0e0;margin:30px 0;">
        <p style="color:#7f8c8d;font-size:12px;text-align:center;">
            본 메일은 포상 추천 시스템에서 자동 발송되었습니다.<br>
            회신하지 마시기 바랍니다.
        </p>
    </body>
    </html>
    """,
    },
}
