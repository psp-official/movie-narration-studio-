import os, re, json, uuid, time, asyncio, shutil, subprocess
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
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
JOBS = {}
app = FastAPI(title='Movie Narration Studio')
app.mount('/static', StaticFiles(directory=str(BASE / 'static')), name='static')
templates = Jinja2Templates(directory=str(BASE / 'templates'))

@app.get('/', response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse('index.html', {'request': request})

@app.get('/api/health')
def health():
    return {'ok': True, 'service': 'movie-narration-studio'}

@app.post('/api/jobs')
async def create_job(video: UploadFile = File(...), voice: str = Form('male'), narration_style: str = Form('cinematic')):
    key = os.getenv('GEMINI_API_KEY')
    if not key:
        raise HTTPException(503, 'Render Environment မှာ GEMINI_API_KEY မထည့်ရသေးပါ။')
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
                if not chunk: break
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
    except Exception as e:
        shutil.rmtree(work, ignore_errors=True)
        raise HTTPException(400, 'Video ကိုဖတ်မရပါ။ ဖိုင်မှန်/မမှန် စစ်ပြီး ထပ်တင်ပေးပါ။')
    if duration > MAX_DURATION:
        shutil.rmtree(work, ignore_errors=True)
        raise HTTPException(400, 'ဒီဗားရှင်းမှာ Video ကို ၅ မိနစ် ၃၀ စက္ကန့်အထိသာ လက်ခံပါတယ်။')
    JOBS[job_id] = {'id': job_id, 'status': 'queued', 'progress': 2, 'message': 'Video လက်ခံပြီးပါပြီ။', 'created': time.time(), 'voice': voice, 'duration': duration}
    asyncio.create_task(process_job(job_id, input_path, voice, narration_style, duration))
    return {'job_id': job_id}

def probe_duration(path: Path) -> float:
    result = subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(path)], capture_output=True, text=True, check=True, timeout=30)
    return float(result.stdout.strip())

def run(cmd, timeout=None):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or 'Command failed')[-1800:])
    return p.stdout

def extract_json(text: str):
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    try: return json.loads(text)
    except Exception:
        start, end = text.find('{'), text.rfind('}')
        if start >= 0 and end > start:
            return json.loads(text[start:end+1])
        raise ValueError('AI က JSON အဖြေမှန် မထုတ်ပေးနိုင်ပါ။ ထပ်စမ်းကြည့်ပါ။')

def srt_time(seconds: float):
    ms = max(0, int(seconds * 1000))
    h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); s, ms = divmod(ms, 1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'

async def process_job(job_id, input_path, voice, style, video_duration):
    job = JOBS[job_id]
    work = input_path.parent
    try:
        job.update(status='processing', progress=5, message='AI က Video ကို စစ်ဆေးနေပါတယ်…')
        client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))
        uploaded = await asyncio.to_thread(client.files.upload, file=str(input_path))
        # Wait until Gemini finishes preparing the video.
        for _ in range(90):
            state = getattr(uploaded, 'state', None)
            state_name = getattr(state, 'name', str(state or ''))
            if state_name.upper() in {'ACTIVE', 'FILE_STATE_ACTIVE'}: break
            if state_name.upper() in {'FAILED', 'FILE_STATE_FAILED'}:
                raise RuntimeError('Gemini က Video ဖိုင်ကို ပြင်ဆင်မရပါ။')
            await asyncio.sleep(2)
            uploaded = await asyncio.to_thread(client.files.get, name=uploaded.name)
        else:
            raise RuntimeError('Video analysis စတင်ဖို့ ကြာလွန်းနေပါတယ်။ နောက်တစ်ကြိမ် ထပ်စမ်းပါ။')
        job.update(progress=20, message='ဇာတ်ကွက်နဲ့ ဖြစ်ရပ်တွေကို ခွဲခြမ်းစိတ်ဖြာနေပါတယ်…')
        style_text = 'cinematic, engaging movie recap narration' if style == 'cinematic' else 'simple, natural spoken Burmese movie recap narration'
        prompt = f'''Analyze the uploaded movie clip (duration about {int(video_duration)} seconds). Create a coherent Burmese-language movie recap narration, not a transcript of the characters' dialogue. Describe visible actions accurately, preserve event order, do not invent events, and avoid saying the same thing repeatedly. Write natural spoken Burmese suitable for a Myanmar movie recap voice-over. Style: {style_text}. Create enough narration to cover the full clip, approximately {max(220, int(video_duration*1.55))} to {max(320, int(video_duration*2.0))} Burmese words if appropriate. Return ONLY valid JSON, no markdown, with this exact schema: {{"title":"short Burmese title","segments":[{{"text":"one or two natural spoken Burmese sentences"}}]}}. Create 25 to 55 segments for a 5-minute clip, fewer for shorter clips. Each segment should be concise, easy to speak, and together tell the story from beginning to end. Do not include timestamps. Do not include dialogue in quotation marks unless essential.'''
        response = await asyncio.to_thread(client.models.generate_content, model='gemini-2.5-flash', contents=[uploaded, prompt])
        data = extract_json(response.text or '')
        segments = data.get('segments', [])
        texts = [str(s.get('text','')).strip() for s in segments if isinstance(s, dict) and str(s.get('text','')).strip()]
        if len(texts) < 3:
            raise RuntimeError('AI Narration စာသားမလုံလောက်ပါ။ နောက်တစ်ကြိမ် ထပ်စမ်းပါ။')
        # Keep output manageable and avoid accidental runaway scripts.
        texts = texts[:80]
        job.update(progress=35, message=f'Narration စာသား {len(texts)} ပိုင်း ရရှိပါပြီ။ အသံဖန်တီးနေပါတယ်…')
        voice_name = 'my-MM-ThihaNeural' if voice == 'male' else 'my-MM-NilarNeural'
        audio_parts = []
        srt_blocks = []
        cursor = 0.0
        for i, text in enumerate(texts, start=1):
            mp3 = work / f'part_{i:03}.mp3'
            communicate = edge_tts.Communicate(text, voice_name, rate='+0%')
            await communicate.save(str(mp3))
            part_duration = probe_duration(mp3)
            # Small gap improves natural phrasing and subtitle readability.
            audio_parts.append(mp3)
            start = cursor
            end = cursor + part_duration
            srt_blocks.append(f'{i}\n{srt_time(start)} --> {srt_time(end)}\n{text}\n')
            cursor = end
            if i % 5 == 0 or i == len(texts):
                job.update(progress=35 + int(35*i/len(texts)), message=f'မြန်မာအသံ ဖန်တီးနေပါတယ်… ({i}/{len(texts)})')
        if cursor > video_duration * 1.25:
            job.update(message='Narration အသံကို Video အချိန်နဲ့ ကိုက်ညီအောင် ညှိနေပါတယ်…')
        concat_file = work / 'concat.txt'
        concat_file.write_text(''.join(f"file '{p.name}'\n" for p in audio_parts), encoding='utf-8')
        narration_audio = work / 'narration.mp3'
        run(['ffmpeg','-y','-f','concat','-safe','0','-i',str(concat_file),'-c:a','libmp3lame','-q:a','3',str(narration_audio)], timeout=300)
        # If narration runs longer than the source, fit it to the video with atempo where reasonable.
        audio_duration = probe_duration(narration_audio)
        final_audio = narration_audio
        if audio_duration > video_duration and audio_duration / video_duration <= 1.8:
            factor = audio_duration / video_duration
            fitted = work / 'narration_fitted.mp3'
            run(['ffmpeg','-y','-i',str(narration_audio),'-filter:a',f'atempo={min(2.0,max(0.5,factor))}','-t',str(video_duration),str(fitted)], timeout=300)
            # Rebuild SRT timing using scaled timestamps.
            audio_duration = probe_duration(fitted)
            scale = audio_duration / max(cursor, 0.01)
            scaled = []
            for idx, block in enumerate(srt_blocks):
                lines = block.strip().splitlines()
                times = lines[1].split(' --> ')
                def parse_srt(t):
                    hh, mm, ss = t.split(':'); sec, milli = ss.split(',')
                    return int(hh)*3600+int(mm)*60+int(sec)+int(milli)/1000
                st, en = parse_srt(times[0])*scale, parse_srt(times[1])*scale
                scaled.append(f'{idx+1}\n{srt_time(st)} --> {srt_time(en)}\n'+'\n'.join(lines[2:])+'\n')
            srt_blocks = scaled
            final_audio = fitted
        srt_path = work / 'subtitles.srt'
        srt_path.write_text('\n'.join(srt_blocks), encoding='utf-8-sig')
        job.update(progress=75, message='မြန်မာစာတန်းထိုးနဲ့ Video ကို ပေါင်းစပ်နေပါတယ်…')
        output_path = work / 'movie_narration.mp4'
        # Mix original audio quietly under narration when the source has an audio stream.
        has_audio = subprocess.run(['ffprobe','-v','error','-select_streams','a:0','-show_entries','stream=codec_type','-of','csv=p=0',str(input_path)], capture_output=True, text=True).stdout.strip() == 'audio'
        subtitle_filter = f"subtitles='{str(srt_path).replace(chr(39), chr(92)+chr(39))}':force_style='FontName=Noto Sans Myanmar,FontSize=22,Outline=2,Shadow=1,Alignment=2,MarginV=28'"
        if has_audio:
            cmd = ['ffmpeg','-y','-i',str(input_path),'-i',str(final_audio),'-filter_complex','[0:a]volume=0.14[bg];[1:a]volume=1.0[vo];[bg][vo]amix=inputs=2:duration=longest:dropout_transition=2[a]','-vf',subtitle_filter,'-map','0:v:0','-map','[a]','-c:v','libx264','-preset','veryfast','-crf','23','-c:a','aac','-b:a','160k','-t',str(video_duration),'-movflags','+faststart',str(output_path)]
        else:
            cmd = ['ffmpeg','-y','-i',str(input_path),'-i',str(final_audio),'-vf',subtitle_filter,'-map','0:v:0','-map','1:a:0','-c:v','libx264','-preset','veryfast','-crf','23','-c:a','aac','-b:a','160k','-t',str(video_duration),'-movflags','+faststart',str(output_path)]
        run(cmd, timeout=1800)
        # Save script as text and keep separate SRT download too.
        (work / 'narration.txt').write_text('\n\n'.join(texts), encoding='utf-8')
        job.update(status='done', progress=100, message='ပြီးပါပြီ။ MP4 ကို Download လုပ်နိုင်ပါပြီ။', title=data.get('title','Movie Narration'), video_url=f'/api/jobs/{job_id}/download', srt_url=f'/api/jobs/{job_id}/subtitles', script_url=f'/api/jobs/{job_id}/script')
        try:
            await asyncio.to_thread(client.files.delete, name=uploaded.name)
        except Exception:
            pass
    except Exception as e:
        job.update(status='error', progress=100, message='လုပ်ဆောင်မှု မအောင်မြင်ပါ။', error=str(e)[:700])

@app.get('/api/jobs/{job_id}')
def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, 'ဒီအလုပ်ကို မတွေ့ပါ။')
    return {k:v for k,v in job.items() if k not in {'created','voice','duration'}}

@app.get('/api/jobs/{job_id}/download')
def download_video(job_id: str):
    job = JOBS.get(job_id)
    path = DATA_DIR / 'jobs' / job_id / 'movie_narration.mp4'
    if not job or job.get('status') != 'done' or not path.exists(): raise HTTPException(404, 'Video မပြီးသေးပါ။')
    return FileResponse(path, media_type='video/mp4', filename='movie_narration.mp4')

@app.get('/api/jobs/{job_id}/subtitles')
def download_subtitles(job_id: str):
    path = DATA_DIR / 'jobs' / job_id / 'subtitles.srt'
    if not path.exists(): raise HTTPException(404, 'Subtitle မရသေးပါ။')
    return FileResponse(path, media_type='application/x-subrip', filename='myanmar_subtitles.srt')

@app.get('/api/jobs/{job_id}/script')
def download_script(job_id: str):
    path = DATA_DIR / 'jobs' / job_id / 'narration.txt'
    if not path.exists(): raise HTTPException(404, 'Script မရသေးပါ။')
    return FileResponse(path, media_type='text/plain; charset=utf-8', filename='myanmar_narration.txt')
