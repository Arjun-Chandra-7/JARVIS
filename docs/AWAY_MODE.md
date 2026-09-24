# Away mode

> "Jarvis, I'm going out until 8 PM. Handle my messages and calls."

Jarvis answers messages as **JARVIS, the owner's assistant** — never as the owner — within a policy
the owner approved, alerts them only when something is urgent, and briefs them when they're back.
Code: `jarvis/away_mode/`. Tests: `tests/test_away_mode.py`.

## What is real, what is simulated

| Capability | Status |
|---|---|
| WhatsApp direct messages: receive, reply, **delivery verified** (bridge returns the server's message id) | Real |
| WhatsApp groups: counted; urgent family/VIP messages escalate; never answered unless the owner allows it | Real |
| Owner writing from the phone hands that thread over (read from the bridge history) | Real |
| Instagram / SMS / Telegram / Signal via phone notifications (KDE Connect) | Received and summarised. Replies only if the owner names the app; KDE Connect gives **no delivery receipt**, so they are reported as "submitted, unconfirmed" |
| Calls: caller identity, ringing and missed events (KDE Connect `callReceived`) | Real |
| Calls: repeated calls from family/VIPs escalate | Real |
| Calls: answering, hearing the caller, speaking to them | **Not possible with the current phone link.** KDE Connect's telephony plugin has no answer/reject method and no audio; the Android companion app (`mobile/android`) has no telephony permissions. The full call conversation (disclosure, take a message, Hinglish, barge-in, duration limit, owner takeover, hang-up) runs against `SimulatedCallAdapter` |
| Escalation: desktop notification, ping to the paired phone, spoken alert | Real (spoken alerts come from the voice process) |

### What a real call bridge needs

`calls.CallAdapter` is the interface; `CallHandler` converses only when an adapter reports all six
of: answer, caller audio, send audio, hang-up events, caller id, low latency. Any one of these would
fill it:

1. **Android companion with `InCallService`** (default dialer role) plus `ANSWER_PHONE_CALLS`,
   streaming call audio over a WebSocket to the backend. Android only exposes call audio to the
   default dialer, and some OEMs block capture entirely — verify on the Nothing Phone 2a first.
2. **Bluetooth HFP audio gateway** on the laptop (oFono/PipeWire `hfp_ag` role): the phone treats
   the laptop as a headset, so answering and both audio directions work over Bluetooth.
3. **SIP trunk with conditional call forwarding** (e.g. forward-on-busy/no-answer to a SIP number
   terminated on the laptop): works without phone-side code, costs a telephony provider.

Until one exists, the capability report says so (`"can you answer my calls?"` → honest answer), and
the approval read-back says calls are only noted.

## The flow

```
owner speech ─► intent.py ─► control.propose_start ─► ApprovalManager ("Ready to turn on away mode until 8 PM …")
                                                         │ "yes"
                                                         ▼
                                  session.py: AwaySession (state.json, 0600, file-locked)
                                                         │
WhatsApp bridge ─┐                                       ▼
KDE Connect ─────┼─► daemon.py (backend) ─► engine.AwayEngine.handle(msg)
                 │      verify → dedupe → own/owner message → resolve sender (contacts.py)
                 │      → group gate → policy (platform, contacts, takeover, kill switch)
                 │      → language (en / hi / hinglish, slang folded) → classify (topics, urgency,
                 │        injection, bots) → limits (turns, cooldown, rate, loops) → compose
                 │        (templates; a no-tool model only for plain chat) → validate → send
                 │        → provider id → thread state → briefing item → escalation
                 ▼
voice process: speaks queued alerts only (escalation.take_spoken)
```

Only `AwayEngine` replies to anyone. The voice process used to auto-reply to Instagram/SMS and text
callers on its own; that path is gone. The model's tools (`set_away`, `set_pa_status`) can only
*propose* a session — the owner still has to say yes.

## Policy

- **Disclosure:** the first reply in a thread, a reply after three hours of silence, and the first
  reply to each new participant in an allowed group start with "Hi, I'm JARVIS, <owner>'s
  assistant. <owner> is unavailable until around 8 PM…" (Hinglish and Hindi versions too).
  Every reply is checked: no speaking as the owner, no commitments, no links, no long numbers.
- **Allowed:** say they're away and when they're back, take a message, note a call-back request,
  note a message to pass on, offer to alert the owner, short small talk.
- **Refused and flagged for the owner:** money, OTPs/passwords/codes, personal details, files,
  legal, purchases, promising attendance, emotionally sensitive topics, account recovery, location,
  contacting other people, and any attempt to change Jarvis's instructions. Incoming text is data;
  it is classified, never obeyed, and never passes through the owner-command normaliser.
- **Urgency is evidence, not a keyword:** "urgent" alone from an unknown sender stays normal. Points
  for explicit urgency, a call-back request, danger words, time-critical changes, security
  warnings, family/VIP sender, three messages in fifteen minutes, two calls. Danger from family or
  repeated contact is an *emergency*: alert repeats every 5 minutes, three times, until the owner
  asks "what's happening".
- **Loops and floods:** event-id and same-text dedupe, our own message ids and reply fingerprints,
  auto-responder phrasing, three answers within 4 s of ours → the thread stops as a bot. 12 s
  cooldown per thread, 6 turns per thread (2 in take-message mode), 30 replies an hour and 8 a
  minute overall, 3 model calls per thread. Kill switch: `JARVIS_AWAY_KILL=1` or a `KILL` file in
  the state directory.

## Owner commands

Start / change: "I'm going out until 8, handle my messages", "handle messages for two hours, only
reply to family", "handle WhatsApp but silence Instagram", "reply to everyone except work groups",
"take messages, but don't hold conversations", "only interrupt me if it's urgent", "extend away mode
by an hour", "stop away mode" / "I'm back".

While away: "what's happening?", "summarize my messages", "read only urgent ones", "stop replying to
Rohan", "take over Maya's conversation", "reply to Maya saying I'll call later" (sent after a yes,
as "<owner> says: …"), "mute Instagram", "mark Papa as VIP".

After: "what happened while I was away?", "tell me more about Rohan's message", "mark that
handled", "save this summary" (journal), "delete the away-session history" (asks first).

## Privacy and storage

`<vault>/Jarvis/private/away/state.json` (0600, directory 0700) holds the session, one bounded
record per thread (redacted gists, collected details, flags — never the full message), dedupe ids,
our outbound ids, pending alerts and an archive of briefings. Gists mask codes, long numbers, links
and e-mail addresses. Raw recent turns for the next reply live in memory only. Logs carry masked
ids and counts, never text or numbers. Idle thread state is dropped after 6 hours (the briefing
line stays); archived briefings are kept 14 days (`prefs.retention_days`). No call audio is ever
recorded.

## Configuration

- `JARVIS_OWNER_NAME` — the name used in disclosures ("Hi, I'm JARVIS, <name>'s assistant").
  Falls back to `JARVIS_USER_NAME`, then to the established default.
- `JARVIS_DRY_RUN_SENDS=1` — sessions started while set are dry runs: nothing is sent.
- `JARVIS_AWAY_KILL=1` — no away-mode sends at all.
