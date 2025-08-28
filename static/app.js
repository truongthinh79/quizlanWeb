
// index uses /api/classes to populate class/name dropdowns
async function loadClasses() {
  const res = await fetch('/api/classes');
  const data = await res.json();
  const classSel = document.getElementById('classSel');
  const nameSel = document.getElementById('nameSel');
  classSel.innerHTML = '<option value="">-- Chọn lớp --</option>';
  for (const cls of Object.keys(data).sort()) {
    const opt = document.createElement('option'); opt.value = cls; opt.textContent = cls;
    classSel.appendChild(opt);
  }
  nameSel.innerHTML = '<option value="">-- Chọn tên --</option>';
}
function onClassChange(){
  const cls = document.getElementById('classSel').value;
  const nameSel = document.getElementById('nameSel');
  nameSel.innerHTML = '<option value="">-- Chọn tên --</option>';
  if (!cls) return;
  fetch('/api/classes').then(r=>r.json()).then(data=>{
    const arr = data[cls] || [];
    arr.sort((a,b)=>a.name.localeCompare(b.name));
    arr.forEach(s=>{
      const opt = document.createElement('option'); opt.value = s.name; opt.textContent = s.name;
      nameSel.appendChild(opt);
    });
  });
}

async function registerName(name, cls){
  const res = await fetch('/api/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name, class:cls})});
  return res.json();
}

let currentCode = '';
async function checkCode(){
  const code = document.getElementById('access_code').value.trim();
  if(!code){ document.getElementById('codeStatus').textContent='Vui lòng nhập mã kỳ thi!'; return; }
  const res = await fetch('/api/check_code',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({access_code: code})});
  const data = await res.json();
  if(data.ok){ 
    currentCode = code;
    document.getElementById('codeStatus').textContent = '';
    document.getElementById('student-code-form').style.display = 'none';
    document.getElementById('student-form').style.display = 'block';
  }
  else { document.getElementById('codeStatus').textContent = data.error || 'Lỗi kiểm tra mã'; }
}

async function joinQuiz(){
  const cls = document.getElementById('classSel').value;
  const name = document.getElementById('nameSel').value;
  if(!cls){ document.getElementById('joinStatus').textContent='Vui lòng chọn lớp!'; return; }
  if(!name){ document.getElementById('joinStatus').textContent='Vui lòng chọn tên!'; return; }
  const rc = await registerName(name, cls);
  if(!rc.ok){ document.getElementById('joinStatus').textContent = rc.error || 'Lỗi lưu tên'; return; }
  const res = await fetch('/api/join',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({access_code: currentCode})});
  const data = await res.json();
  if(data.ok){ location.href = '/quiz/' + data.quiz_id; }
  else { document.getElementById('joinStatus').textContent = data.error || 'Lỗi tham gia'; }
}

function toggleStudentCodeForm(){
  document.getElementById('student-code-form').style.display = 'block';
}

function toggleTeacherForm(){
  document.getElementById('teacher-form').style.display = 'block';
}

// Quiz page logic
if (typeof QUIZ_ID !== 'undefined') {
  try { document.getElementById('timer').style.display = 'block'; } catch(e) {}

  let remaining = DURATION;
  const timerEl = document.getElementById('timer');
  const formEl = document.getElementById('quizForm');
  const submitBtn = document.getElementById('submitBtn');
  function renderTimer(){
    const m = Math.floor(remaining/60).toString().padStart(2,'0');
    const s = (remaining%60).toString().padStart(2,'0');
    timerEl.textContent = m + ':' + s;
  }
  function tick(){
    remaining -= 1;
    renderTimer();
    if(remaining <= 0){
      submitQuiz(QUIZ_ID);
    } else {
      setTimeout(tick, 1000);
    }
  }
  async function loadQuiz(){
    const res = await fetch('/api/quiz/' + QUIZ_ID);
    const data = await res.json();
    if (data.error){ document.getElementById('submitStatus').textContent = data.error; return; }
    data.questions.forEach((q, idx) => {
      const card = document.createElement('div'); card.className='card mb-3';
      const header = document.createElement('div'); header.className='card-header'; header.innerHTML=`Câu ${idx+1}/${data.questions.length}: ${q.text}`;
      if(q.image) header.innerHTML += `<br><img src="${q.image}" class="img-fluid" alt="Question image">`;
      card.appendChild(header);
      const body = document.createElement('div'); body.className='card-body';
      q.options.forEach(opt => {
        const id = `q${q.id}_${opt.label}`;
        const div = document.createElement('div'); div.className='form-check mb-2';
        const input = document.createElement('input'); input.className='form-check-input';
        input.type = q.multi ? 'checkbox' : 'radio';
        input.name = 'q_' + q.id + (q.multi ? '[]' : '');
        input.value = opt.label;
        input.id = id;
        const label = document.createElement('label'); label.className='form-check-label'; label.htmlFor = id; 
        label.innerHTML = `${opt.label}) ${opt.text}`;
        if(opt.image) label.innerHTML += `<br><img src="${opt.image}" class="img-fluid" alt="Option image">`;
        div.appendChild(input); div.appendChild(label); body.appendChild(div);
      });
      card.appendChild(body); formEl.appendChild(card);
    });
    MathJax.typeset();
    document.getElementById('timer').style.display = 'block'; renderTimer(); setTimeout(tick, 1000);
  }
  loadQuiz();
  window.submitQuiz = async function(quizId){
    if (!confirm('Bạn chắc chắn muốn nộp bài?')) return;
    submitBtn.disabled = true;
    const answers = {};
    const inputs = document.querySelectorAll('input[name^="q_"]');
    inputs.forEach(inp => {
      const name = inp.name.replace('[]', '');
      if (!answers[name]) answers[name] = [];
      if (inp.checked) answers[name].push(inp.value);
    });
    const formatted = {};
    Object.keys(answers).forEach(k=>{
      const qid = k.split('_')[1];
      formatted[qid] = answers[k];
    });
    const res = await fetch('/api/submit/' + quizId, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({answers: formatted})});
    const data = await res.json();
    const statusEl = document.getElementById('submitStatus');
    
    if (data.ok){
      statusEl.textContent = `Đã nộp! Điểm: ${data.score}/${data.total}`;
      statusEl.className = 'text-success';
      // Hiện modal xác nhận
      const modalHtml = `
        <div class="modal fade" id="resultModal" tabindex="-1">
          <div class="modal-dialog">
            <div class="modal-content">
              <div class="modal-header bg-success text-white">
                <h5 class="modal-title">Nộp bài thành công</h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
              </div>
              <div class="modal-body">
                <p>Bạn đã hoàn thành bài thi.</p>
                <p><strong>Điểm: ${data.score}/${data.total}</strong></p>
              </div>
              <div class="modal-footer">
                <button class="btn btn-primary" data-bs-dismiss="modal">Đóng</button>
              </div>
            </div>
          </div>
        </div>`;
      document.body.insertAdjacentHTML('beforeend', modalHtml);
      new bootstrap.Modal(document.getElementById('resultModal')).show();
    } else {
      statusEl.textContent = data.error || 'Lỗi nộp bài';
      statusEl.className = 'text-danger';
    }

    submitBtn.disabled = false;
  };
  // anti-cheat: show small banner on blur (non-blocking) and send log to server
  function showAntiCheat(msg){
    try {
      const banner = document.getElementById('anticheat-banner');
      if (!banner) return;
      banner.innerHTML = '<div class="alert alert-warning shadow-sm mb-0" role="alert">' + msg + '</div>';
      banner.style.display = 'block';
      setTimeout(()=>{ banner.style.display='none'; banner.innerHTML=''; }, 3000);
    } catch(e){}
  }
  window.addEventListener('blur', function(){
    showAntiCheat('⚠️ Bạn vừa rời khỏi tab — hành động có thể bị ghi nhận.');
    try {
      fetch('/log_event', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({quiz_id: QUIZ_ID, student: STUDENT_NAME, event: 'blur'})
      }).catch(()=>{});
    } catch(e){}
  });
  document.addEventListener("contextmenu", e=>e.preventDefault());
  document.onkeydown = function(e){ if(e.keyCode==123 || (e.ctrlKey&&e.shiftKey&&e.keyCode==73)) { e.preventDefault(); return false; } };
}

// Initialize index class list when page loads
try { if (document.readyState !== 'loading') loadClasses(); else document.addEventListener('DOMContentLoaded', loadClasses); } catch(e){}
