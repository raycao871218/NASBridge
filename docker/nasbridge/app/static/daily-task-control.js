let dailyTaskSubmitting=false,dailyTaskCanTrigger=false;
function renderDailyTaskControl(data){
  const control=data.manual_control||{},button=document.getElementById('dailyTaskRun');
  document.getElementById('dailyTaskManual').hidden=false;
  dailyTaskCanTrigger=data.status==='ok'&&control.can_trigger===true;
  button.disabled=dailyTaskSubmitting||!dailyTaskCanTrigger;
  button.textContent=dailyTaskSubmitting?'正在提交…':control.running?'执行中…':'手动执行';
  document.getElementById('dailyTaskManualHint').textContent=control.message||(data.status!=='ok'?'任务状态读取失败，请刷新记录后重试':'正在确认任务状态…');
}
async function triggerDailyTask(){
  if(dailyTaskSubmitting||!dailyTaskCanTrigger)return;
  dailyTaskSubmitting=true;const button=document.getElementById('dailyTaskRun'),status=document.getElementById('dailyTaskRunStatus');
  button.disabled=true;button.textContent='正在提交…';status.textContent='正在提交每日任务…';
  try{
    const response=await fetch('/api/daily-tasks/run',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    const data=await response.json();if(!response.ok)throw Error(data.detail||'提交失败，请刷新记录确认任务状态');
    status.textContent=data.message;status.className='status-line ok';
  }catch(error){status.textContent=error.message+'；请刷新记录确认状态后再操作';status.className='status-line error';}
  finally{dailyTaskSubmitting=false;await loadDailyTasks();}
}
document.getElementById('dailyTaskRun').onclick=()=>void triggerDailyTask();
