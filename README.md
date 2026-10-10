# Movie Narration Studio (MVP)

Mobile-first website prototype for turning a short uploaded video into an MP4 with Burmese narration and burned-in Myanmar subtitles.

## Features
- Upload MP4/MOV/WEBM/MKV/AVI up to 300 MB and 5:30 duration.
- Gemini video analysis + Burmese recap script.
- Burmese male/female voice options (`my-MM-ThihaNeural`, `my-MM-NilarNeural`) through `edge-tts`.
- SRT and narration TXT downloads.
- FFmpeg combines voice, source audio (lowered), and hard-burned subtitles.
- Docker deployment with FFmpeg and Noto fonts included.

## Important limitations
- This is an MVP. Job status is kept in memory; a service restart clears active job status. Completed files are on the attached disk.
- Gemini free-tier availability/rate limits can change by account, model, and region. Set up an API key in Google AI Studio and check current quota/pricing before using it heavily.
- `edge-tts` uses Microsoft's online speech service without an Azure subscription key; it is not a guaranteed/contracted API and may change or be rate-limited. For production, switch to an official TTS API with an available free quota.
- AI can misunderstand fast action, small details, names, or original dialogue. Review the output before publishing.
- Use videos you own or have permission to process and publish.

## Deploy on Render
1. Create a private GitHub repository and upload the contents of this project (the files inside `movie-narration-web`, not the ZIP itself).
2. In Render, choose **New > Blueprint** and connect the repository, or create a Docker Web Service from the repository.
3. Add the environment variable `GEMINI_API_KEY` from Google AI Studio. Never put the key in frontend files or commit it to GitHub.
4. Make sure the service has a persistent disk mounted at `/var/data`. The included `render.yaml` requests a 10 GB disk and Starter plan; adjust the plan/disk to your budget.
5. Deploy and check `/api/health` on your service URL.

The Dockerfile installs FFmpeg and Noto fonts needed for Myanmar subtitle rendering.
