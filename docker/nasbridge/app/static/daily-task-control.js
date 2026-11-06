let dailyTaskSubmitting=false,dailyTaskCanTrigger=false;
let dailyTaskControl={},dailyTaskDayRequest=0;
const dailyTaskDateInput=document.getElementById('dailyTaskDateInput');
function updateDailyTaskButton(){
  const button=document.getElementById('dailyTaskRun'),day=dailyTaskDateInput.value;
  const historical=day<dailyTaskControl.today;
  button.disabled=dailyTaskSubmitting||!dailyTaskCanTrigger||(!dailyTaskDateInput.disabled&&!dailyTaskDateInput.validity.valid)
    ||(historical&&(!dailyTaskControl.backfill||!dailyTaskControl.can_backfill));
  button.textContent=dailyTaskSubmitting?'正在提交…':dailyTaskControl.running?'执行中…':historical?'补抓所选日期':'手动执行';
  document.getElementById('dailyTaskManualHint').textContent=historical&&dailyTaskCanTrigger
    ?!dailyTaskControl.backfill?'执行端尚未启用往日补抓'
      :!dailyTaskControl.can_backfill?'自动重试正在执行，请稍后补抓':'按视频归档日期补抓并处理所选日期的视频'
    :dailyTaskControl.message||'任务状态读取失败，请刷新记录后重试';
}
function renderDailyTaskControl(data){
  const control=dailyTaskControl=data.manual_control||{};
  renderDailyTaskRetry(data.status==='ok'?control:{});
  document.getElementById('dailyTaskManual').hidden=false;
  dailyTaskCanTrigger=data.status==='ok'&&control.can_trigger===true;
  dailyTaskDateInput.disabled=!control.today;
  if(control.today){
    dailyTaskDateInput.min=control.first_date;
    dailyTaskDateInput.max=control.today;
    if(!dailyTaskDateInput.value)dailyTaskDateInput.value=control.today;
  }
  updateDailyTaskButton();
  void loadDailyTaskDay();
}
async function loadDailyTaskDay(){
  if(!document.getElementById('rhinoView').classList.contains('active'))return;
  const request=++dailyTaskDayRequest,day=dailyTaskDateInput.value,status=document.getElementById('dailyTaskDayStatus');
  if(dailyTaskDateInput.disabled||!dailyTaskDateInput.validity.valid){status.textContent='';return;}
  if(status.dataset.date!==day){status.textContent='正在查询所选日期…';status.className='status-line muted';}
  status.dataset.date=day;
  try{
    const response=await fetch('/api/daily-tasks/day?date='+encodeURIComponent(day),{cache:'no-store'});
    if(!response.ok)throw Error('读取失败');
    const data=await response.json();
    if(request!==dailyTaskDayRequest)return;
    const run=data.latest_run;
    const archived=data.archived_count>0?'已成功归档 '+data.archived_count+' 部视频'
      :run?.matched_count===0?'已检查，当天没有发现公开视频':'尚无已归档视频';
    status.textContent=day+' · '+archived+(run?' · 最近任务：'+dailyTaskStatusLabel(run.status):' · 无运行记录');
    status.className='status-line '+(data.archived_count>0?'ok':'muted');
  }catch{
    if(request!==dailyTaskDayRequest)return;
    status.textContent='暂时无法确认所选日期的抓取记录，请刷新重试';
    status.className='status-line error';
  }
}
function renderDailyTaskRetry(control){
  const retry=control.retry||{},known=retry.available===true;
  const title=document.getElementById('dailyTaskRetryTitle');
  const detail=document.getElementById('dailyTaskRetryDetail');
  const time=document.getElementById('dailyTaskRetryTime');
  const guard=document.getElementById('dailyTaskShutdownGuard');
  const date=value=>{
    const parsed=new Date(value);
    return value&&Number.isFinite(parsed.getTime())?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(parsed):'待确认';
  };
  let heading='自动重试状态待确认',message='暂时无法读取执行端状态，请稍后刷新。',timing='';
  if(known){
    if(retry.status==='waiting'){
      heading='等待 11:00 自动重试';message='本轮未发现新视频，已安排再检查一次。';
      timing='预计再次检查：'+date(retry.retry_at)+'（北京时间）';
      const seconds=(Date.parse(retry.retry_at)-Date.parse(control.observed_at))/1000;
      if(Number.isFinite(seconds))timing+=seconds>0?' · 约 '+Math.ceil(seconds/60)+' 分钟后':control.running?' · 已到时间，等待当前任务结束':' · 已到时间，等待执行端启动';
    }else if(retry.status==='retrying'){
      heading='自动重试执行中';message='正在执行再次检查、下载及后续处理，本次结束后不再自动重试。';
      timing='重试开始：'+date(retry.attempted_at)+'（北京时间）';
    }else if(retry.status==='completed'){
      const failed=typeof retry.returncode==='number'?retry.returncode!==0:retry.service_result&&!['success','unknown'].includes(retry.service_result);
      heading=failed?'自动重试未成功':'自动重试已结束';
      message='本次自动重试已结束，不会因没有新视频继续重试。';
      timing='结束时间：'+date(retry.finished_at)+'（北京时间）';
    }else if(retry.status==='cancelled'){
      heading='自动重试已取消';message='后续检查已发现新视频，取消尚未执行的重试。';
      timing=retry.date?'原安排日期：'+retry.date:'';
    }else if(retry.status==='none'){
      heading='暂无自动重试安排';message='10 点确认没有新视频时，将安排 11 点再检查一次。';
    }
  }
  title.textContent=heading;detail.textContent=message;time.textContent=timing;
  guard.textContent=control.shutdown_protected===true?'自动关机已暂停：任务正在执行，或仍在等待自动重试。':control.shutdown_protected===false?'当前没有任务或重试等待保护，自动关机仍遵循原有时段及空闲判断。':'自动关机保护状态待确认';
  guard.className='status-line '+(control.shutdown_protected===true?'ok':'muted');
}
async function triggerDailyTask(){
  if(document.getElementById('dailyTaskRun').disabled)return;
  const day=dailyTaskDateInput.value;
  dailyTaskSubmitting=true;const button=document.getElementById('dailyTaskRun'),status=document.getElementById('dailyTaskRunStatus');
  button.disabled=true;button.textContent='正在提交…';status.textContent='正在提交每日任务…';
  try{
    const response=await fetch('/api/daily-tasks/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(day?{date:day}:{})});
    const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'提交失败，请检查日期并刷新记录');
    status.textContent=data.message;status.className='status-line ok';
  }catch(error){status.textContent=error.message+'；请刷新记录确认状态后再操作';status.className='status-line error';}
  finally{dailyTaskSubmitting=false;await loadDailyTasks();}
}
document.getElementById('dailyTaskRun').onclick=()=>void triggerDailyTask();
dailyTaskDateInput.onchange=()=>{updateDailyTaskButton();void loadDailyTaskDay();};
