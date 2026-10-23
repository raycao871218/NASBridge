const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const nodes=Object.fromEntries(['dailyTaskRun','dailyTaskManual','dailyTaskManualHint','dailyTaskRunStatus','dailyTaskRetryTitle','dailyTaskRetryDetail','dailyTaskRetryTime','dailyTaskShutdownGuard'].map(id=>[id,{}]));
let calls=0,resolveRequest;
const ctx=vm.createContext({document:{getElementById:id=>nodes[id]},fetch:()=>{calls++;return new Promise(resolve=>{resolveRequest=resolve;});},loadDailyTasks:async()=>{vm.runInContext('renderDailyTaskControl({status:"ok",manual_control:{show:true,can_trigger:false,running:true}})',ctx);}});
vm.runInContext(fs.readFileSync(__dirname+'/app/static/daily-task-control.js','utf8'),ctx);
(async()=>{
  const display=control=>vm.runInContext('renderDailyTaskControl('+JSON.stringify({status:'ok',manual_control:control})+')',ctx);
  display({retry:{available:true,status:'waiting',retry_at:'2026-10-24T11:00:00+08:00'},observed_at:'2026-10-24T02:30:00Z',shutdown_protected:true});
  assert.match(nodes.dailyTaskRetryTitle.textContent,/等待 11:00/);
  assert.match(nodes.dailyTaskRetryTime.textContent,/30 分钟/);
  assert.match(nodes.dailyTaskShutdownGuard.textContent,/已暂停/);
  display({retry:{available:true,status:'waiting',retry_at:'2026-10-24T11:00:00+08:00'},observed_at:'2026-10-24T03:10:00Z',running:true,shutdown_protected:true});
  assert.match(nodes.dailyTaskRetryTime.textContent,/等待当前任务结束/);
  for(const [status,label] of [['retrying','执行中'],['completed','已结束'],['cancelled','已取消'],['none','暂无']]){
    display({retry:{available:true,status},shutdown_protected:false});
    assert.match(nodes.dailyTaskRetryTitle.textContent,new RegExp(label));
    assert.doesNotMatch(nodes.dailyTaskShutdownGuard.textContent,/已暂停/);
  }
  display({retry:{available:true,status:'completed',returncode:1}});
  assert.match(nodes.dailyTaskRetryTitle.textContent,/未成功/);
  display({retry:{available:false,status:'unknown'},shutdown_protected:null});
  assert.match(nodes.dailyTaskRetryTitle.textContent,/待确认/);
  assert.match(nodes.dailyTaskShutdownGuard.textContent,/待确认/);
  vm.runInContext('renderDailyTaskControl({status:"ok",manual_control:{show:false,can_trigger:false}})',ctx);
  assert.equal(nodes.dailyTaskManual.hidden,false);assert.equal(nodes.dailyTaskRun.disabled,true);
  vm.runInContext('renderDailyTaskControl({status:"ok",manual_control:{show:true,can_trigger:false,running:true}})',ctx);
  assert.equal(nodes.dailyTaskRun.disabled,true);assert.equal(nodes.dailyTaskRun.textContent,'执行中…');
  vm.runInContext('renderDailyTaskControl({status:"error"})',ctx);assert.equal(nodes.dailyTaskManual.hidden,false);assert.equal(nodes.dailyTaskRun.disabled,true);
  assert.match(nodes.dailyTaskManualHint.textContent,/读取失败/);
  vm.runInContext('renderDailyTaskControl({status:"ok",manual_control:{show:true,can_trigger:true}})',ctx);
  const first=vm.runInContext('triggerDailyTask()',ctx);await vm.runInContext('triggerDailyTask()',ctx);
  assert.equal(calls,1);assert.equal(nodes.dailyTaskRun.disabled,true);
  resolveRequest({ok:true,json:async()=>({message:'已提交每日任务'})});await first;
  assert.equal(nodes.dailyTaskRunStatus.textContent,'已提交每日任务');assert.equal(nodes.dailyTaskRun.disabled,true);
  vm.runInContext('renderDailyTaskControl({status:"ok",manual_control:{show:true,can_trigger:true}})',ctx);
  const failed=vm.runInContext('triggerDailyTask()',ctx);resolveRequest({ok:false,json:async()=>({detail:'任务正在执行'})});await failed;
  assert.match(nodes.dailyTaskRunStatus.textContent,/任务正在执行/);assert.equal(nodes.dailyTaskRun.disabled,true);
  console.log('PASS: control stays visible, busy/error disables, duplicate click prevented, accepted/conflict refreshed');
})().catch(error=>{console.error(error);process.exitCode=1;});
