# Notifications Skill

Use these tools when the user explicitly asks to send an email or schedule
a calendar event. Never use them unless the user requests it.

## Tool: send_email
Send a plain-text email via the system Gmail account. Use when the user asks
to email findings, notify a colleague, or share results by email.

Compose the body yourself from available context — be concise and professional.
Include the key data points, names, and any recommended actions relevant to
the recipient. Always confirm in your answer what was sent (to address, subject).

## Tool: add_to_calendar
Log a calendar follow-up request. This is a STUB — the actual calendar write
is not yet implemented. Use when the user asks to schedule a review, set a
reminder, or block time for follow-up.

Always let the user know the calendar integration is coming soon and that
their request has been noted.

## Rules
- Never send an email without an explicit user instruction to do so.
- Never invent an email address — ask if one was not provided.
- For calendar requests: acknowledge the stub status clearly; do not imply
  the event was actually created in any calendar system.
