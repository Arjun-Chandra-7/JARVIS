# LinkedIn Content Copilot

Jarvis drives the LinkedIn Content Copilot (a separate project, by default at
`~/Dev/Linkdin/repo`): it opens the console, reads the numbers out, and can
capture an idea or approve a post by voice.

## Setup

```bash
LINKEDIN_COPILOT_URL=http://127.0.0.1:8000
LINKEDIN_COPILOT_DIR=$HOME/Dev/Linkdin/repo
```

Nothing else is needed. If the backend isn't running when you ask for it,
Jarvis starts it (`scripts/dev.sh`) and waits for it to come up.

Authentication is automatic: the copilot issues desktop sessions to callers on
the same machine only. A phone still has to pair with a one-time code.

## What you can say

| You say | What happens |
|---|---|
| "open LinkedIn stats" | Console opens on the dashboard, Jarvis reads the summary aloud |
| "open my LinkedIn approvals" | Console opens on the approval queue |
| "anything to approve?" | Lists what's waiting, with quality scores |
| "read the first one" | Reads the whole post aloud |
| "approve it" | Approves and schedules — only if it was read aloud first |
| "who should I connect with?" | Names them and why, opens the networking queue |
| "write a post about the scheduler bug I just fixed" | Captures it as a draft |

## Two limits, on purpose

**A post can only be approved by voice after Jarvis has read it.** The approval
carries the hash of the exact text that was read, so if the draft changed in
between, the backend refuses it. Asking to approve something unheard gets a
refusal, not a shortcut — approving a post you haven't heard is the one failure
this system exists to prevent.

**No invitation is ever sent or accepted automatically.** Jarvis will tell you
who is worth knowing and open the queue, where one keystroke opens a profile
with your note already copied. You click Connect. LinkedIn's API has no
endpoint for sending or accepting invitations, so automating it would mean
driving your logged-in browser session — against LinkedIn's terms, and the
fastest way to get a real account restricted.
