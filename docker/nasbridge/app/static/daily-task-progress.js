const expandedRunProgress=new Set();
function dailyTaskProgressHtml(run){
  const progress=run.progress;
  if(!progress||!Array.isArray(progress.stages))return '<p class="muted small">旧版记录没有逐阶段进度；新执行的任务将显示完整阶段。</p>';
  const statusLabel=value=>dailyTaskStatusLabel(value);
  const stopped=run.status!=='running';
  const stages=progress.stages.map(stage=>{
    const state=stopped&&stage.status==='running'?run.status:stage.status;
    return '<li><strong>'+esc(stage.label)+'</strong> <span class="'+(state==='success'?'ok':['failed','interrupted','timed_out'].includes(state)?'critical':'muted')+'">'+esc(statusLabel(state))+'</span>'+
      (stage.message?'<p class="muted small">'+esc(stage.message)+'</p>':'')+
      (stage.item?'<p class="muted small">'+esc(stage.item)+'</p>':'')+
      (Number.isFinite(stage.percent)?'<p class="muted small">进度 '+esc(stage.percent)+'%</p>':'')+
      (stage.started_at?'<p class="muted small">开始 '+dailyTaskDate(stage.started_at)+(stage.finished_at?' · 结束 '+dailyTaskDate(stage.finished_at):'')+'</p>':'')+'</li>';
  }).join('');
  return '<p class="muted small">最近心跳：'+dailyTaskDate(progress.heartbeat_at)+' · 最近进展：'+dailyTaskDate(progress.last_progress_at)+'</p><ol class="daily-stages">'+stages+'</ol>';
}
function dailyTaskRunProgress(run){
  if(!run.progress)return run.status_message?'<p class="muted small">'+esc(run.status_message)+'</p>':'';
  const stage=(run.progress.stages||[]).find(item=>item.id===run.progress.current_stage);
  const title=run.status==='running'?'当前阶段：'+(stage?.label||'准备任务'):'查看已执行阶段';
  return (run.status_message?'<p class="muted small">'+esc(run.status_message)+'</p>':'')+
    '<details data-run-progress="'+esc(run.run_id)+'" '+(expandedRunProgress.has(run.run_id)?'open':'')+'><summary>'+esc(title)+'</summary>'+dailyTaskProgressHtml(run)+'</details>';
}
function renderDailyTaskProgress(data){
  document.querySelectorAll('details[data-run-progress]').forEach(node=>{
    if(node.open)expandedRunProgress.add(node.dataset.runProgress);else expandedRunProgress.delete(node.dataset.runProgress);
  });
  const panel=document.getElementById('dailyTaskProgressPanel');
  const run=(data.runs||[]).find(row=>!row.hidden);
  panel.hidden=data.status!=='ok'||!run;
  if(panel.hidden)return;
  const stage=(run.progress?.stages||[]).find(item=>item.id===run.progress.current_stage);
  const heading=run.status==='running'?'当前阶段：'+(stage?.label||'旧版任务，未记录阶段'):'最近执行：'+dailyTaskStatusLabel(run.status);
  panel.innerHTML='<h2>'+esc(heading)+'</h2>'+(run.status_message?'<p class="status-line">'+esc(run.status_message)+'</p>':'')+dailyTaskProgressHtml(run);
}
