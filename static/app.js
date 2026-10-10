const form=document.getElementById('uploadForm');
const input=document.getElementById('video');
const dropzone=document.getElementById('dropzone');
const fileTitle=document.getElementById('fileTitle');
const fileInfo=document.getElementById('fileInfo');
const submitBtn=document.getElementById('submitBtn');
const panel=document.getElementById('progressPanel');
const progressBar=document.getElementById('progressBar');
const percent=document.getElementById('percent');
const statusText=document.getElementById('statusText');
const errorBox=document.getElementById('errorBox');
const result=document.getElementById('result');
let pollTimer=null;
function showFile(file){if(!file)return;fileTitle.textContent=file.name;fileInfo.textContent=`${(file.size/1024/1024).toFixed(1)} MB · ဖိုင်ရွေးပြီးပါပြီ`;dropzone.classList.add('active')}
input.addEventListener('change',()=>showFile(input.files[0]));
['dragenter','dragover'].forEach(ev=>dropzone.addEventListener(ev,e=>{e.preventDefault();dropzone.classList.add('active')}));
['dragleave','drop'].forEach(ev=>dropzone.addEventListener(ev,e=>{e.preventDefault();if(ev==='drop'&&e.dataTransfer.files.length){input.files=e.dataTransfer.files;showFile(input.files[0])}else if(!input.files.length)dropzone.classList.remove('active')}));
function updateProgress(p,msg){progressBar.style.width=`${p}%`;percent.textContent=`${p}%`;statusText.textContent=msg||'လုပ်ဆောင်နေပါတယ်…'}
async function poll(id){try{const res=await fetch(`/api/jobs/${id}`);const data=await res.json();if(!res.ok)throw new Error(data.detail||'Status ကို မဖတ်နိုင်ပါ။');updateProgress(data.progress||0,data.message||'လုပ်ဆောင်နေပါတယ်…');if(data.status==='done'){clearInterval(pollTimer);submitBtn.disabled=false;submitBtn.innerHTML='<span>✦</span> Narration MP4 ဖန်တီးမယ် <span class="arrow">→</span>';result.hidden=false;document.getElementById('resultTitle').textContent=data.title||'ပြီးပါပြီ';document.getElementById('preview').src=data.video_url;document.getElementById('downloadVideo').href=data.video_url;document.getElementById('downloadSrt').href=data.srt_url;document.getElementById('downloadScript').href=data.script_url;statusText.textContent='အောင်မြင်ပါပြီ။';}else if(data.status==='error'){clearInterval(pollTimer);submitBtn.disabled=false;submitBtn.innerHTML='<span>✦</span> ထပ်မံစမ်းသပ်မယ် <span class="arrow">→</span>';errorBox.hidden=false;errorBox.textContent=(data.error||data.message||'အမှားတစ်ခုဖြစ်နေပါတယ်။')+'\n\nAPI quota၊ Video ဖိုင်နဲ့ Render Logs ကို စစ်ဆေးပါ။';} }catch(e){statusText.textContent='အခြေအနေကို ပြန်စစ်နေပါတယ်…';}}
form.addEventListener('submit',async e=>{e.preventDefault();const file=input.files[0];if(!file){alert('အရင်ဆုံး Video ဖိုင်ရွေးပေးပါ။');return}if(file.size>300*1024*1024){alert('Video ဖိုင်က 300MB ထက်ကြီးနေပါတယ်။');return}if(pollTimer)clearInterval(pollTimer);panel.hidden=false;result.hidden=true;errorBox.hidden=true;submitBtn.disabled=true;submitBtn.textContent='Video တင်နေပါတယ်…';updateProgress(1,'Video ကို Server ဆီ Upload တင်နေပါတယ်…');panel.scrollIntoView({behavior:'smooth',block:'start'});try{const body=new FormData(form);const res=await fetch('/api/jobs',{method:'POST',body});const data=await res.json();if(!res.ok)throw new Error(data.detail||'Upload မအောင်မြင်ပါ။');submitBtn.textContent='AI က လုပ်ဆောင်နေပါတယ်…';await poll(data.job_id);pollTimer=setInterval(()=>poll(data.job_id),3000);}catch(err){submitBtn.disabled=false;submitBtn.innerHTML='<span>✦</span> ထပ်မံစမ်းသပ်မယ် <span class="arrow">→</span>';errorBox.hidden=false;errorBox.textContent=err.message||'အမှားတစ်ခု ဖြစ်နေပါတယ်။';}});
