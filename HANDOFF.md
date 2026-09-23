# Jarvis — where things stand

This file used to describe a build from an early phase: Deepgram for speech, ElevenLabs for the
voice, the brain running on a Claude subscription through the Agent SDK, a file map with modules
that no longer exist. None of that is how it works now, and a stale handoff is worse than no
handoff — it is the document people trust when they are new and have no way to check.

So it has been replaced with what is true, and with what is *known* to be true, which is not the
same list.

`README.md` is the real documentation. This file only covers what changed most recently and how
far each piece was actually taken.

---

## Verified by watching it work

Not "the tests pass" — these were each driven against the real thing and the result observed.

| What | How it was checked | Result |
|---|---|---|
| Browser control on Zen | Drove a real isolated Zen over Marionette | navigate, read, click, type, close all work |
| Study mode | Opened a Shorts tab and an ordinary tab in a real browser | the Short closed, the other stayed |
| Shorts blocking | Opened the Shorts feed and read where it landed | a real Short keeps `/shorts/`, caught by URL |
| Microphone | Recorded from the real device | unmuted, RMS 0.0137, peak 0.178 |
| Speech in and out | `python -m jarvis --selftest` | spoke, heard, transcribed "Jarvis, how are you?" at rms 1710 |
| Streaming a reply | Real model (local Ollama) | reassembles exactly; a tool call forwarded zero fragments |
| Time to first word | Real model and real synthesis | 3-sentence answer 0.76 s sooner, 8-sentence 3.20 s sooner |
| The whole program | `python -m jarvis --text` on the local brain | answered correctly |
| The overlay | Launched this branch beside the running one, captured the window | renders light |

The isolation mattered for the browser work. `flatpak run` on an already-running app hands its
arguments to the existing instance, so `--profile` is silently dropped and a test connects to
the real browser with all its tabs. Two defences: `--new-instance`, and a Marionette port of its
own, because the real browser already holds 2828.

## Measured

| | before | after |
|---|---|---|
| Routing recall@10 | 87.7% | 100% (and 100% on 17 held-out phrasings) |
| Time to first spoken word, short answer | 2.11 s | 0.96 s |
| Time to first spoken word, 8-sentence answer | 5.73 s | 2.53 s |
| Microphone noise floor | 0.84 RMS (saturated) | 0.0052 |
| Idle overlay CPU | 37.3% | 0.59% |

## Known limits, stated plainly

- **Streaming is only measured against a local model.** The path is the same for the hosted
  brains, which are OpenAI-compatible, but the numbers above come from Ollama. A provider that
  will not stream falls back to waiting for the whole reply.
- **The hosted voice does not stream.** It is asked for whole utterances, so it behaves exactly
  as it did before.
- **The voice loop itself is not covered by automatic tests.** It needs a microphone, a speaker
  and a wake word. `--selftest` is the check; the seam between the brain and the speaking is
  unit-tested, the loop around it is not.
- **Tab closing needs a drivable browser.** An extension or the debug port for Chromium, a
  restart with automation for Firefox. Without it study mode can judge a tab and not close it.

## If something looks wrong

Start with `python -m jarvis --check` (dependencies, models, audio devices — no API calls), then
`--selftest` for one live round trip through the microphone and speaker.

If the overlay comes up black, the checkout is probably behind: the light theme is recent. A
compositor that refuses a transparent visual is the other cause, and the window now falls back
to white rather than black so that failure is no longer mistaken for the first one.

If Jarvis cannot hear you, just ask it — "is my mic working". It will say, and unmute it if that
was the trouble.
