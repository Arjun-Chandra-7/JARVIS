"""Meeting/capture mode: continuous transcription into vault."""

import asyncio
from .mic import Microphone
from . import vad

async def run_meeting_mode(config, agent) -> None:
    print("\n[Meeting Mode] Starting continuous transcription.")
    print("Press Ctrl-C when the meeting is over to generate notes.\n")
    
    backend = config.resolved_voice_backend()
    
    def transcribe(pcm: bytes) -> str:
        if backend == "local":
            from . import local_stt
            return local_stt.transcribe(pcm, 16000, config.whisper_model)
        from . import stt
        return stt.transcribe(pcm, config.deepgram_api_key, 16000, config.stt_model)

    device = config.audio_input_device if config.audio_input_device >= 0 else -1
    mic = Microphone(1280, device_index=device)
    mic.start()
    
    threshold = config.vad_threshold or vad.calibrate_threshold(mic.read)
    
    transcript_parts = []
    
    try:
        while True:
            # We wait a long time for someone to speak.
            pcm = vad.record_utterance(
                mic.read,
                sample_rate=16000,
                frame_length=1280,
                threshold=threshold,
                silence_ms=1000,
                max_s=30, # chunk max size so we transcribe iteratively
                wait_s=3600, # basically wait forever for next speaker
            )
            if pcm:
                # Transcribe in a thread to keep things somewhat non-blocking
                text = await asyncio.to_thread(transcribe, pcm)
                if text:
                    print(f"  {text}")
                    transcript_parts.append(text)
    except KeyboardInterrupt:
        pass
    finally:
        mic.stop()
        mic.delete()
        
    transcript = " ".join(transcript_parts).strip()
    if not transcript:
        print("\n[Meeting Mode] No speech detected. Exiting.")
        return
        
    print("\n[Meeting Mode] Meeting ended. Analyzing transcript...")
    # Send it to the agent
    prompt = (
        f"I just finished a meeting. Here is the raw transcript:\n\n{transcript}\n\n"
        "Please summarize the meeting, extract any decisions or action items, and save them into the active project note. "
        "Any action items for me should be added as Markdown tasks (- [ ]). Make sure to write this into the vault."
    )
    
    reply = await agent.send(prompt)
    print(f"\njarvis> {reply}")
