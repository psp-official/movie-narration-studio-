import os, re, json, uuid, time, asyncio, shutil, subprocess
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Header
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
import edge_tts
from google import genai

BASE = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv('DATA_DIR', '/var/data'))
DATA_DIR.mkdir(parents=True, exist_ok=True)
MAX_BYTES = 300 * 1024 * 1024
MAX_DURATION = 5 * 60 + 30
MODEL_NAME = os.getenv('GEMINI_MODEL', 'gemini-3.8-flash')
JOBS = {}

app = FastAPI(title='Movie Narration Studio')
app.mount('/static', StaticFiles(directory=str(BASE / 'static')), name='static')
templates = Jinja2Templates(directory=str(BASE / 'templates'))


def choose_key(header_key: Optional[str]) -> Optional[str]:
    key = (header_key or '').strip()
    return key or os.getenv('GEMINI_API_KEY')


def job_dir(job_id: str) -> Path:
    return DATA_DIR / 'jobs' / job_id


def save_job_metadata(job_id: str) -> None:
    """Persist safe job status so completed results survive page navigation/reloads."""
    job = JOBS.get(job_id)
    if not job:
        return
    safe = {k: v for k, v in job.items() if k not in {'api_key', 'key'}}
    folder = job_dir(job_id)
    folder.mkdir(parents=True, exist_ok=True)
    temp = folder / 'job.json.tmp'
    temp.write_text(json.dumps(safe, ensure_ascii=False), encoding='utf-8')
    temp.replace(folder / 'job.json')


def update_job(job_id: str, **changes) -> None:
    job = JOBS[job_id]
    job.update(changes)
    save_job_metadata(job_id)


def read_job_metadata(job_id: str):
    job = JOBS.get(job_id)
    if job:
        return job
    meta_path = job_dir(job_id) / 'job.json'
    try:
        data = json.loads(meta_path.read_text(encoding='utf-8'))
        JOBS[job_id] = data
        return data
    except (OSError, ValueError, TypeError):
        return None


@app.get('/', response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse('index.html', {'request': request})


@app.get('/api/health')
def health():
    return {'ok': True, 'service': 'movie-narration-studio'}


@app.get('/api/settings')
def settings_status():
    # Never return the API key itself.
    return {'server_key_configured': bool(os.getenv('GEMINI_API_KEY')), 'model': MODEL_NAME}


@app.post('/api/test-key')
async def test_key(x_gemini_api_key: Optional[str] = Header(default=None, alias='X-Gemini-API-Key')):
    key = choose_key(x_gemini_api_key)
    if not key:
        raise HTTPException(400, 'Gemini API Key ကို အရင်ထည့်ပါ။')
    try:
        client = genai.Client(api_key=key)
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=MODEL_NAME,
            contents='Reply with exactly: OK'
        )
        if not (getattr(response, 'text', '') or '').strip():
            raise RuntimeError('Gemini က စာသားအဖြေမပြန်လာပါ။')
        return {'ok': True, 'message': 'Gemini API ချိတ်ဆက်မှု အောင်မြင်ပါပြီ။', 'model': MODEL_NAME}
    except Exception as exc:
        # Avoid returning request headers or the key in error messages.
        message = str(exc).replace(key, '[API_KEY]')[:500]
        raise HTTPException(400, f'Gemini API စမ်းသပ်မှု မအောင်မြင်ပါ။ {message}')


@app.post('/api/jobs')
async def create_job(
    video: UploadFile = File(...),
    voice: str = Form('male'),
    narration_style: str = Form('cinematic'),
    x_gemini_api_key: Optional[str] = Header(default=None, alias='X-Gemini-API-Key'),
):
    key = choose_key(x_gemini_api_key)
    if not key:
        raise HTTPException(503, 'Website ရဲ့ API Key အကွက်ထဲ Gemini API Key ထည့်ပါ၊ သို့မဟုတ် Render မှာ GEMINI_API_KEY ထည့်ပါ။')
    suffix = Path(video.filename or '').suffix.lower()
    allowed = {'.mp4', '.mov', '.m4v', '.webm', '.avi', '.mkv'}
    if suffix not in allowed:
        raise HTTPException(400, 'MP4, MOV, M4V, WEBM, AVI, MKV ဖိုင်ကိုသာ တင်ပေးပါ။')
    if voice not in {'male', 'female'}:
        voice = 'male'

    job_id = uuid.uuid4().hex[:12]
    work = DATA_DIR / 'jobs' / job_id
    work.mkdir(parents=True, exist_ok=True)
    input_path = work / f'input{suffix}'
    size = 0
    try:
        with input_path.open('wb') as out:
            while True:
                chunk = await video.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, 'ဖိုင်အရွယ်အစား 300MB ထက်ကျော်နေပါတယ်။')
                out.write(chunk)
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise
    finally:
        await video.close()

    if size == 0:
        shutil.rmtree(work, ignore_errors=True)
        raise HTTPException(400, 'ဗီဒီယိုဖိုင်အလွတ်ဖြစ်နေပါတယ်။')
    try:
        duration = probe_duration(input_path)
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise HTTPException(400, 'Video ကိုဖတ်မရပါ။ ဖိုင်မှန်/မမှန် စစ်ပြီး ထပ်တင်ပေးပါ။')
    if duration > MAX_DURATION:
        shutil.rmtree(work, ignore_errors=True)
        raise HTTPException(400, 'ဒီဗားရှင်းမှာ Video ကို ၅ မိနစ် ၃၀ စက္ကန့်အထိသာ လက်ခံပါတယ်။')

    JOBS[job_id] = {
        'id': job_id, 'status': 'queued', 'progress': 2,
        'message': 'Video လက်ခံပြီးပါပြီ။', 'created': time.time(),
        'voice': voice, 'duration': duration,
        'title': Path(video.filename or 'Movie Narration').stem[:120],
    }
    save_job_metadata(job_id)
    # The key is passed in memory to this job only; it is not written to disk or returned to the browser.
    asyncio.create_task(process_job(job_id, input_path, voice, narration_style, duration, key))
    return {'job_id': job_id}


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'default=noprint_wrappers=1:nokey=1', str(path)],
        capture_output=True, text=True, check=True, timeout=30
    )
    return float(result.stdout.strip())


def run(cmd, timeout=None):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or 'Command failed')[-1800:])
    return p.stdout


async def gemini_call_with_retry(func, *args, **kwargs):
    """Retry temporary Gemini overload/rate-limit errors with bounded exponential backoff."""
    last_error = None
    for attempt in range(4):
        try:
            return await asyncio.to_thread(func, *args, **kwargs)
        except Exception as exc:
            last_error = exc
            message = str(exc).lower()
            transient = any(token in message for token in (
                '503', 'unavailable', 'high demand', '429', 'resource_exhausted',
                'temporarily overloaded', 'internal error', '500'
            ))
            if not transient or attempt == 3:
                raise
            wait_seconds = min(2 ** attempt, 8)
            # Keep the job alive and make the retry visible in Results.
            await asyncio.sleep(wait_seconds)
    raise last_error


def extract_json(text: str):
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    try:
        return json.loads(text)
    except Exception:
        start, end = text.find('{'), text.rfind('}')
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise ValueError('AI က JSON အဖြေမှန် မထုတ်ပေးနိုင်ပါ။ ထပ်စမ်းကြည့်ပါ။')


async def process_job(job_id, input_path, voice, style, video_duration, api_key):
    job = JOBS[job_id]
    work = input_path.parent
    client = None
    uploaded = None
    try:
        update_job(job_id, status='processing', progress=5, message='AI က Video ကို စစ်ဆေးနေပါတယ်…')
        client = genai.Client(api_key=api_key)
        uploaded = await asyncio.to_thread(client.files.upload, file=str(input_path))

        for _ in range(90):
            state = getattr(uploaded, 'state', None)
            state_name = getattr(state, 'name', str(state or ''))
            if state_name.upper() in {'ACTIVE', 'FILE_STATE_ACTIVE'}:
                break
            if state_name.upper() in {'FAILED', 'FILE_STATE_FAILED'}:
                raise RuntimeError('Gemini က Video ဖိုင်ကို ပြင်ဆင်မရပါ။')
            await asyncio.sleep(2)
            uploaded = await asyncio.to_thread(client.files.get, name=uploaded.name)
        else:
            raise RuntimeError('Video analysis စတင်ဖို့ ကြာလွန်းနေပါတယ်။ နောက်တစ်ကြိမ် ထပ်စမ်းပါ။')

        # Pass 1: establish a timestamped, evidence-based scene timeline.
        update_job(job_id, progress=18, message='ဇာတ်ကွက်တွေကို အချိန်အစဉ်အလိုက် စစ်ဆေးနေပါတယ်…')
        analysis_prompt = f'''You are a careful film-scene analyst. Analyze this exact uploaded video, whose duration is about {video_duration:.1f} seconds.
Return ONLY valid JSON with schema {{"timeline":[{{"start":0,"end":8,"visual_facts":"...","characters":"...","action":"...","confidence":"high|medium|low"}}]}}.
Rules:
- Cover the clip from 0 seconds to {video_duration:.1f} seconds in chronological order, with about 15-35 entries for a five-minute clip and fewer for shorter clips.
- Use approximate timestamps in seconds; entries must not overlap substantially and should cover the whole clip.
- Record only things actually visible or clearly inferable from the frames. Describe visible actions, locations, character appearances, and changes in the scene.
- Never guess names, motives, relationships, backstory, or off-screen events. If uncertain, say unknown/unclear.
- For fast action, describe only what is clearly visible. Do not invent impacts, weapons, injuries, dialogue, or outcomes.
- Keep each entry concrete and brief. Do not write the recap narration yet.'''
        analysis_response = await gemini_call_with_retry(
            client.models.generate_content, model=MODEL_NAME, contents=[uploaded, analysis_prompt]
        )
        analysis = extract_json(analysis_response.text or '')
        timeline = analysis.get('timeline', [])
        if not isinstance(timeline, list) or len(timeline) < 3:
            raise RuntimeError('Video ဇာတ်ကွက် Timeline မလုံလောက်ပါ။ ထပ်စမ်းကြည့်ပါ။')
        timeline = timeline[:60]

        # Pass 2: write narration using the timeline as evidence, not free-form guessing.
        update_job(job_id, progress=30, message='စစ်ဆေးထားတဲ့ ဇာတ်ကွက်အချက်အလက်အပေါ် မူတည်ပြီး Narration ရေးနေပါတယ်…')
        style_text = 'cinematic and engaging, but factual' if style == 'cinematic' else 'simple, natural spoken Burmese'
        target_words = max(120, int(video_duration * 1.15))
        script_prompt = f'''Write a Burmese-language movie recap voice-over for the same {video_duration:.1f}-second video. Use ONLY the observed timeline notes below as factual evidence.
TIMELINE NOTES (untrusted observations to summarize, do not add events):
{json.dumps(timeline, ensure_ascii=False)}
Requirements:
- Natural spoken Burmese for Myanmar viewers. Style: {style_text}.
- Narrate the visible events in the same order as the timeline. Mention transitions when the scene changes.
- Never invent names, dialogue, relationships, motives, powers, injuries, deaths, or outcomes not supported by the timeline. If unclear, describe only the visible action.
- Do not claim that a character says something unless speech is clearly established in the notes. Do not write dialogue quotes.
- Every narration segment must correspond to the relevant timeline interval. Do not move later events earlier or repeat the same event.
- Make around {target_words} Burmese words if the footage supports that much; accuracy matters more than length. Keep the voice-over concise enough to fit the video without unnaturally fast speech.
Return ONLY valid JSON: {{"title":"short Burmese title","segments":[{{"start":0,"end":8,"text":"one or two natural spoken Burmese sentences describing only that interval"}}]}}.
Return about 15-35 segments for a five-minute clip, fewer for short clips. Use timestamps from the timeline, in seconds.'''
        response = await gemini_call_with_retry(
            client.models.generate_content, model=MODEL_NAME, contents=[script_prompt]
        )
        data = extract_json(response.text or '')
        raw_segments = data.get('segments', [])
        segments = []
        for seg in raw_segments if isinstance(raw_segments, list) else []:
            if not isinstance(seg, dict):
                continue
            text = str(seg.get('text', '')).strip()
            if not text:
                continue
            try:
                start = max(0.0, min(video_duration, float(seg.get('start', 0))))
                end = max(start, min(video_duration, float(seg.get('end', video_duration))))
            except (TypeError, ValueError):
                start, end = 0.0, video_duration
            segments.append({'text': text, 'start': start, 'end': end})
        if len(segments) < 3:
            raise RuntimeError('AI Narration စာသားမလုံလောက်ပါ။ ထပ်စမ်းကြည့်ပါ။')
        segments = segments[:60]
        texts = [s['text'] for s in segments]
        update_job(job_id, progress=38, message=f'ဇာတ်ကွက်နဲ့ချိတ်ထားတဲ့ Narration {len(texts)} ပိုင်း ရပါပြီ။ မြန်မာအသံဖန်တီးနေပါတယ်…')

        voice_name = 'my-MM-ThihaNeural' if voice == 'male' else 'my-MM-NilarNeural'
        audio_parts = []
        track_specs = []
        for i, segment in enumerate(segments, start=1):
            mp3 = work / f'part_{i:03}.mp3'
            await edge_tts.Communicate(segment['text'], voice_name, rate='+0%').save(str(mp3))
            part_duration = probe_duration(mp3)
            start_sec = max(0.0, min(video_duration, segment.get('start', 0.0)))
            end_sec = max(start_sec, min(video_duration, segment.get('end', video_duration)))
            # If the AI gave a meaningful interval, fit speech into it moderately; never silently
            # move a later scene narration to the beginning of the video.
            allotted = end_sec - start_sec
            if allotted >= 1.0 and part_duration > allotted * 1.08:
                factor = part_duration / allotted
                if factor <= 1.65:
                    fitted_part = work / f'part_{i:03}_fit.mp3'
                    run(['ffmpeg', '-y', '-i', str(mp3), '-filter:a', f'atempo={factor:.5f}',
                         '-t', str(allotted), str(fitted_part)], timeout=180)
                    mp3 = fitted_part
            audio_parts.append(mp3)
            track_specs.append((mp3, start_sec))
            if i % 5 == 0 or i == len(segments):
                update_job(job_id, progress=38 + int(30 * i / len(segments)), message=f'မြန်မာအသံဖန်တီးနေပါတယ်… ({i}/{len(segments)})')

        # Place each narration segment at its scene timestamp. This reduces the drift caused by
        # concatenating all speech into one long track without regard to scene boundaries.
        narration_audio = work / 'narration_timed.m4a'
        cmd = ['ffmpeg', '-y']
        for part, _ in track_specs:
            cmd += ['-i', str(part)]
        filters = []
        mix_inputs = []
        for idx, (_, start_sec) in enumerate(track_specs):
            delay_ms = int(max(0.0, start_sec) * 1000)
            filters.append(f'[{idx}:a]adelay={delay_ms}|{delay_ms},apad=pad_dur=0.15[a{idx}]')
            mix_inputs.append(f'[a{idx}]')
        filters.append(''.join(mix_inputs) + f'amix=inputs={len(mix_inputs)}:duration=longest:dropout_transition=0,atrim=duration={video_duration}[aout]')
        cmd += ['-filter_complex', ';'.join(filters), '-map', '[aout]', '-c:a', 'aac', '-b:a', '160k',
                '-t', str(video_duration), str(narration_audio)]
        run(cmd, timeout=900)
        final_audio = narration_audio

        update_job(job_id, progress=75, message='မူရင်းအသံနဲ့ စာတန်းမပါဘဲ MP4 ထုတ်နေပါတယ်…')
        output_path = work / 'movie_narration.mp4'
        # Map only source video + generated narration. Original audio and subtitles are intentionally omitted.
        run([
            'ffmpeg', '-y', '-i', str(input_path), '-i', str(final_audio),
            '-map', '0:v:0', '-map', '1:a:0',
            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
            '-c:a', 'aac', '-b:a', '160k', '-t', str(video_duration),
            '-movflags', '+faststart', str(output_path)
        ], timeout=1800)
        (work / 'narration.txt').write_text('\n\n'.join(texts), encoding='utf-8')
        update_job(job_id, 
            status='done', progress=100, message='ပြီးပါပြီ။ မူရင်းအသံနဲ့ Subtitle မပါသော MP4 ကို Download လုပ်နိုင်ပါပြီ။',
            title=data.get('title', 'Movie Narration'),
            video_url=f'/api/jobs/{job_id}/download',
            script_url=f'/api/jobs/{job_id}/script'
        )
    except Exception as exc:
        message = str(exc)
        if api_key:
            message = message.replace(api_key, '[API_KEY]')
        update_job(job_id, status='error', progress=100, message='လုပ်ဆောင်မှု မအောင်မြင်ပါ။ မူရင်း Video ကို မဖျက်ထားပါ။ Retry လုပ်နိုင်ပါသည်။', error=message[:700], retryable=True)
    finally:
        if client and uploaded:
            try:
                await asyncio.to_thread(client.files.delete, name=uploaded.name)
            except Exception:
                pass


@app.get('/api/jobs')
def list_jobs():
    """Return completed results from memory and the configured data disk."""
    jobs_by_id = {}
    jobs_root = DATA_DIR / 'jobs'
    if jobs_root.exists():
        for folder in jobs_root.iterdir():
            if not folder.is_dir():
                continue
            job_id = folder.name
            meta = read_job_metadata(job_id) or {}
            output_path = folder / 'movie_narration.mp4'
            script_path = folder / 'narration.txt'
            # Older completed outputs may not have metadata; recover them from disk.
            output_ok = output_path.is_file() and output_path.stat().st_size > 0
            script_ok = script_path.is_file()
            created = meta.get('created', output_path.stat().st_mtime if output_path.exists() else folder.stat().st_mtime)
            if output_ok and script_ok:
                jobs_by_id[job_id] = {
                    'id': job_id,
                    'status': 'done',
                    'progress': 100,
                    'message': 'ပြီးပါပြီ။',
                    'title': meta.get('title') or 'Movie Narration',
                    'created': created,
                    'video_url': f'/api/jobs/{job_id}/download',
                    'script_url': f'/api/jobs/{job_id}/script',
                }
            elif meta and meta.get('status') in {'queued', 'processing', 'error'}:
                jobs_by_id[job_id] = {
                    'id': job_id,
                    'status': meta.get('status'),
                    'progress': meta.get('progress', 0),
                    'message': meta.get('message', ''),
                    'title': meta.get('title') or 'Movie Narration',
                    'created': created,
                    'video_url': None,
                    'script_url': None,
                }
    # Include currently active jobs even before output files exist.
    for job_id, job in JOBS.items():
        if job.get('status') != 'done' and job_id not in jobs_by_id:
            jobs_by_id[job_id] = {
                'id': job_id, 'status': job.get('status', 'queued'),
                'progress': job.get('progress', 0), 'message': job.get('message', ''),
                'title': job.get('title') or 'လုပ်ဆောင်နေဆဲ Video',
                'created': job.get('created', time.time()),
                'video_url': f'/api/jobs/{job_id}/download' if job.get('status') == 'done' else None,
                'script_url': f'/api/jobs/{job_id}/script' if job.get('status') == 'done' else None,
            }
    return sorted(jobs_by_id.values(), key=lambda item: item.get('created', 0), reverse=True)[:30]


@app.get('/api/jobs/{job_id}')
def get_job(job_id: str):
    job = read_job_metadata(job_id)
    if not job:
        raise HTTPException(404, 'ဒီအလုပ်ကို မတွေ့ပါ။')
    # Recover a completed status when the app restarted but the result files remain.
    output_path = job_dir(job_id) / 'movie_narration.mp4'
    script_path = job_dir(job_id) / 'narration.txt'
    if output_path.is_file() and output_path.stat().st_size > 0 and script_path.is_file():
        job.update(status='done', progress=100, message='ပြီးပါပြီ။')
        job.setdefault('video_url', f'/api/jobs/{job_id}/download')
        job.setdefault('script_url', f'/api/jobs/{job_id}/script')
        save_job_metadata(job_id)
    return {k: v for k, v in job.items() if k not in {'created', 'voice', 'duration', 'api_key', 'key'}}


@app.get('/api/jobs/{job_id}/download')
def download_video(job_id: str):
    path = job_dir(job_id) / 'movie_narration.mp4'
    if not path.is_file() or path.stat().st_size == 0:
        job = read_job_metadata(job_id)
        if not job:
            raise HTTPException(404, 'ဒီ Video ကို မတွေ့ပါ။ Results စာရင်းကို ပြန်ဖွင့်ပါ။')
        if job.get('status') in {'queued', 'processing'}:
            raise HTTPException(409, 'Video ကို ထုတ်လုပ်နေဆဲပါ။ Results ထဲက အခြေအနေကို ပြန်စစ်ပါ။')
        raise HTTPException(404, 'ထုတ်ပြီးသား Video ဖိုင်ကို မတွေ့ပါ။ Render Persistent Disk ရှိ/မရှိ စစ်ဆေးပါ။')
    return FileResponse(path, media_type='video/mp4', filename='movie_narration.mp4')


@app.get('/api/jobs/{job_id}/script')
def download_script(job_id: str):
    path = job_dir(job_id) / 'narration.txt'
    if not path.is_file():
        raise HTTPException(404, 'Script ဖိုင်ကို မတွေ့ပါ။')
    return FileResponse(path, media_type='text/plain; charset=utf-8', filename='myanmar_narration.txt')
