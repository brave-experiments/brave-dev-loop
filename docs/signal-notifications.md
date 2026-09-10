# Signal Notifications (Optional)

The bot can send real-time notifications via Signal when key events happen (PR created, review comments posted, CI retriggered, etc.). This is entirely optional — everything works without it.

## Install signal-cli

```bash
brew install signal-cli
```

Or download manually from the [signal-cli releases page](https://github.com/AsamK/signal-cli/releases).

Requires Java 21+ (Homebrew handles this automatically).

## Register a Phone Number

You can register any phone number you own. Numbers must be in E.164 format (e.g., `+14155551234`).

### Landline Registration (voice verification)

Signal requires an SMS attempt before allowing voice verification. For a landline, the SMS won't arrive — that's expected.

```bash
# Step 1: Solve the captcha
#   Go to https://signalcaptchas.org/registration/generate.html
#   Solve the captcha, then RIGHT-CLICK "Open Signal" and copy the link.
#   The link starts with signalcaptcha://

# Step 2: Request SMS verification with the captcha token (SMS won't arrive on landline — that's OK)
signal-cli -u +1YOURPHONENUMBER register --captcha "signalcaptcha://YOUR-CAPTCHA-TOKEN"

# Step 3: Wait at least 60 seconds for the SMS attempt to expire

# Step 4: Request voice verification (this will call your landline)
signal-cli -u +1YOURPHONENUMBER register --voice

# Step 5: Answer the call, note the 6-digit code, then verify
signal-cli -u +1YOURPHONENUMBER verify 123456
```

**Troubleshooting landline registration:**
- Steps 2 and 4 must be **separate commands**. Do NOT pass `--voice` and `--captcha` together — it will fail with `InvalidTransportModeException`.
- If step 4 fails with "Before requesting voice verification you need to request SMS verification and wait a minute", the SMS attempt (step 2) didn't register properly. Get a fresh captcha token and redo step 2 — tokens expire quickly.
- The voice call may have a **3-5 second silence** before the code is spoken. Use speakerphone and listen carefully. If no code is read, hang up and re-run step 4 to get a new call.
- If the voice call consistently has no audio, try a mobile number instead (see below).

### Mobile Registration (SMS verification)

```bash
# Step 1: Solve captcha (same URL as above), then register
signal-cli -u +1YOURMOBILENUMBER register --captcha "signalcaptcha://YOUR-CAPTCHA-TOKEN"

# Step 2: Wait for SMS with code, then verify
signal-cli -u +1YOURMOBILENUMBER verify 123456
```

## Set Profile Name

After registration, set a display name so the recipient knows who's messaging:

```bash
signal-cli -u +1YOURPHONENUMBER updateProfile --given-name "Brave Core" --family-name "Bot"
```

## Configure Environment Variables

Add these to your `.envrc`:

```bash
export SIGNAL_SENDER="+14155551234"      # Your registered signal-cli number
export SIGNAL_RECIPIENT="+14155559876"   # Number to receive notifications
```

Then reload: `direnv allow`

## Test It

```bash
./scripts/signal-notify.sh "Hello from brave-dev-loop!"
```

You should receive the message on the recipient's Signal account.

## What Gets Notified

| Event | Example Message |
|-------|----------------|
| PR created | `PR created: #12345 - Fix auth test https://...` |
| PR merged | `PR merged: #12345 - Fix auth test https://...` |
| Review feedback addressed | `Review addressed: PR #12345 - removed unnecessary thread hop` |
| PR review comments posted | `Review complete: 5 PRs reviewed, 2 with violations, 4 comments posted` |
| CI jobs retriggered | `CI retriggered: PR #12345 - 3 jobs restarted (2 normal, 1 WIPE_WORKSPACE)` |
| Learnable patterns found | `Learnable patterns: analyzed 10 PRs, found 3 patterns, updated coding-standards.md` |
| Best practice PR created | `Best practice PR created: https://... - Adjust RunUntilIdle rule` |

## Graceful Degradation

The notification script (`scripts/signal-notify.sh`) silently does nothing if:
- `SIGNAL_SENDER` or `SIGNAL_RECIPIENT` env vars are not set
- `signal-cli` is not installed
- The message send fails for any reason

No skill or workflow will error due to missing Signal configuration.
