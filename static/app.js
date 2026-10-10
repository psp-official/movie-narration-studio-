const form = document.getElementById('uploadForm');
const input = document.getElementById('video');
const dropzone = document.getElementById('dropzone');
const fileTitle = document.getElementById('fileTitle');
const fileInfo = document.getElementById('fileInfo');
const submitBtn = document.getElementById('submitBtn');
const panel = document.getElementById('progressPanel');
const progressBar = document.getElementById('progressBar');
const percent = document.getElementById('percent');
const statusText = document.getElementById('statusText');
const errorBox = document.getElementById('errorBox');
const result = document.getElementById('result');
const apiKeyInput = document.getElementById('apiKey');
const keyStatus = document.getElementById('keyStatus');
const keyMessage = document.getElementById('keyMessage');
const KEY_STORAGE = 'mns_gemini_api_key_v1';
let pollTimer = null;
let activeJobId = null;

function getKey() {
  return (apiKeyInput.value || localStorage.getItem(KEY_STORAGE) || '').trim();
}
function setKeyMessage(message, good = false) {
  keyMessage.textContent = message;
  keyMessage.className = `key-message ${good ? 'good' : 'bad'}`;
}
function showFile(file) {
  if (!file) return;
  fileTitle.textContent = file.name;
  fileInfo.textContent = `${(file.size / 1024 / 1024).toFixed(1)} MB · ဖိုင်ရွေးပြီးပါပြီ`;
  dropzone.classList.add('active');
}
input.addEventListener('change', () => showFile(input.files[0]));
['dragenter', 'dragover'].forEach(ev => dropzone.addEventListener(ev, e => {
  e.preventDefault(); dropzone.classList.add('active');
}));
['dragleave', 'drop'].forEach(ev => dropzone.addEventListener(ev, e => {
  e.preventDefault();
  if (ev === 'drop' && e.dataTransfer.files.length) {
    input.files = e.dataTransfer.files; showFile(input.files[0]);
  } else if (!input.files.length) dropzone.classList.remove('active');
}));
function updateProgress(p, msg) {
  progressBar.style.width = `${p}%`; percent.textContent = `${p}%`;
  statusText.textContent = msg || 'လုပ်ဆောင်နေပါတယ်…';
}

(async function initSettings() {
  const saved = localStorage.getItem(KEY_STORAGE) || '';
  if (saved) apiKeyInput.value = saved;
  try {
    const res = await fetch('/api/settings');
    const data = await res.json();
    keyStatus.textContent = data.server_key_configured ? 'Server Key ရှိသည်' : 'Key ထည့်ရန်လိုသည်';
    if (!saved && data.server_key_configured) setKeyMessage('Render Server မှာ API Key ထည့်ထားပြီးသားပါ။ ကိုယ့် Key ထည့်ချင်ရင် အပေါ်မှာ ဖြည့်နိုင်ပါတယ်။', true);
    else if (saved) setKeyMessage('ဒီ Browser မှာ သိမ်းထားတဲ့ Key ကို တွေ့ပါတယ်။', true);
  } catch (_) {
    keyStatus.textContent = 'မစစ်ဆေးနိုင်ပါ';
  }
})();

document.getElementById('toggleKey').addEventListener('click', () => {
  apiKeyInput.type = apiKeyInput.type === 'password' ? 'text' : 'password';
});
document.getElementById('saveKey').addEventListener('click', () => {
  const key = apiKeyInput.value.trim();
  if (!key) {
    localStorage.removeItem(KEY_STORAGE);
    setKeyMessage('Browser ထဲက သိမ်းထားသော Key ကို ဖယ်ရှားလိုက်ပါပြီ။ Server Key ရှိလျှင် ဆက်သုံးနိုင်ပါတယ်။', true);
    return;
  }
  localStorage.setItem(KEY_STORAGE, key);
  setKeyMessage('Key ကို ဒီ Browser ထဲမှာ သိမ်းပြီးပါပြီ။ Server Disk ပေါ်မှာ မသိမ်းထားပါဘူး။', true);
});
document.getElementById('testKey').addEventListener('click', async () => {
  const key = getKey();
  if (!key) { setKeyMessage('API Key ထည့်ပေးပါ။'); apiKeyInput.focus(); return; }
  const button = document.getElementById('testKey');
  button.disabled = true; button.textContent = 'စမ်းသပ်နေပါတယ်…';
  setKeyMessage('Gemini API ကို ချိတ်ဆက်စမ်းသပ်နေပါတယ်…', true);
  try {
    const res = await fetch('/api/test-key', { method: 'POST', headers: { 'X-Gemini-API-Key': key } });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'API စမ်းသပ်မှု မအောင်မြင်ပါ။');
    setKeyMessage(`${data.message} (${data.model})`, true);
    keyStatus.textContent = 'API ချိတ်ဆက်ပြီး';
  } catch (err) {
    setKeyMessage(err.message || 'API စမ်းသပ်မှု မအောင်မြင်ပါ။');
  } finally {
    button.disabled = false; button.textContent = 'API စမ်းမယ်';
  }
});

async function poll(id) {
  try {
    const res = await fetch(`/api/jobs/${id}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Status ကို မဖတ်နိုင်ပါ။');
    updateProgress(data.progress || 0, data.message || 'လုပ်ဆောင်နေပါတယ်…');
    if (data.status === 'done') {
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = null; submitBtn.disabled = false;
      submitBtn.innerHTML = '<span>✦</span> Narration MP4 ဖန်တီးမယ် <span class="arrow">→</span>';
      result.hidden = false;
      document.getElementById('resultTitle').textContent = data.title || 'ပြီးပါပြီ';
      document.getElementById('preview').src = data.video_url;
      document.getElementById('downloadVideo').href = data.video_url;
      document.getElementById('downloadScript').href = data.script_url;
      statusText.textContent = 'အောင်မြင်ပါပြီ။';
      return true;
    }
    if (data.status === 'error') {
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = null; submitBtn.disabled = false;
      submitBtn.innerHTML = '<span>✦</span> ထပ်မံစမ်းသပ်မယ် <span class="arrow">→</span>';
      errorBox.hidden = false;
      errorBox.textContent = (data.error || data.message || 'အမှားတစ်ခုဖြစ်နေပါတယ်။') + '\n\nAPI quota၊ Video ဖိုင်နဲ့ Render Logs ကို စစ်ဆေးပါ။';
      return true;
    }
  } catch (_) {
    statusText.textContent = 'အခြေအနေကို ပြန်စစ်နေပါတယ်…';
  }
  return false;
}

form.addEventListener('submit', async e => {
  e.preventDefault();
  const file = input.files[0];
  const key = getKey();
  if (!file) { alert('အရင်ဆုံး Video ဖိုင်ရွေးပေးပါ။'); return; }
  if (file.size > 300 * 1024 * 1024) { alert('Video ဖိုင်က 300MB ထက်ကြီးနေပါတယ်။'); return; }
  if (!key) { alert('Gemini API Key ကို အရင်ထည့်ပါ။'); document.getElementById('settingsPanel').scrollIntoView({ behavior: 'smooth' }); apiKeyInput.focus(); return; }
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null; activeJobId = null;
  panel.hidden = false; result.hidden = true; errorBox.hidden = true;
  submitBtn.disabled = true; submitBtn.textContent = 'Video တင်နေပါတယ်…';
  updateProgress(1, 'Video ကို Server ဆီ Upload တင်နေပါတယ်…');
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  try {
    const body = new FormData(form);
    const res = await fetch('/api/jobs', { method: 'POST', body, headers: { 'X-Gemini-API-Key': key } });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Upload မအောင်မြင်ပါ။');
    activeJobId = data.job_id;
    submitBtn.textContent = 'AI က လုပ်ဆောင်နေပါတယ်…';
    const finished = await poll(activeJobId);
    if (!finished) pollTimer = setInterval(() => { if (activeJobId) poll(activeJobId); }, 3000);
  } catch (err) {
    submitBtn.disabled = false;
    submitBtn.innerHTML = '<span>✦</span> ထပ်မံစမ်းသပ်မယ် <span class="arrow">→</span>';
    errorBox.hidden = false; errorBox.textContent = err.message || 'အမှားတစ်ခု ဖြစ်နေပါတယ်။';
  }
});
