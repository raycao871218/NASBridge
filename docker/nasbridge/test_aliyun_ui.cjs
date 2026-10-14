const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const source=fs.readFileSync(__dirname+'/app/static/aliyun.js','utf8');
function setup(fetch){
 const nodes=Object.fromEntries(['aliStatus','aliActionStatus','aliApply','aliPreview','aliRegion','aliGroup','aliPorts','aliSource','aliProtocol','aliPriority','aliDescription','aliRules'].map(id=>[id,{value:'',textContent:'',innerHTML:'',disabled:false}]));
 Object.assign(nodes.aliRegion,{value:'cn-chengdu'});nodes.aliGroup.value='sg-test';nodes.aliPorts.value='18001';nodes.aliSource.value='0.0.0.0/0';nodes.aliProtocol.value='tcp';nodes.aliPriority.value='1';nodes.aliApply.disabled=true;
 const ctx=vm.createContext({document:{getElementById:id=>id==='aliyunView'?{querySelectorAll:()=>Object.values(nodes)}:nodes[id]},fetch,AbortController,URLSearchParams,setTimeout,clearTimeout,esc:String});vm.runInContext(source,ctx);return {ctx,nodes};
}
const response=(data,status=200)=>({ok:status===200,status,text:async()=>JSON.stringify(data)});
(async()=>{
 let calls=[];
 let t=setup(async(url,opts)=>{calls.push([url,opts]);return response(url.includes('rules?')?{items:[]}:(JSON.parse(opts.body).confirm?{submitted_rules:1,request_id:'test'}:{items:[{IpProtocol:'tcp',PortRange:'18001/18001',SourceCidrIp:'0.0.0.0/0',Priority:'1'}]}));});
 await vm.runInContext('previewAliyunPorts()',t.ctx);assert.equal(t.nodes.aliApply.disabled,false);
 await vm.runInContext('submitAliyunPorts()',t.ctx);assert.equal(calls.length,3);assert.equal(JSON.parse(calls[1][1].body).confirm,true);assert.match(t.nodes.aliActionStatus.textContent,/提交成功/);assert.equal(t.nodes.aliApply.textContent,'确认开放端口');assert.equal(vm.runInContext('aliyunBusy',t.ctx),false);
 for(const kind of ['denied','nonjson','timeout']){
  t=setup(async()=>{if(kind==='timeout'){const e=new Error();e.name='AbortError';throw e;}if(kind==='nonjson')return{ok:false,status:502,text:async()=>'<html>bad gateway</html>'};return response({detail:'RAM denied'},502);});
  vm.runInContext('aliyunPending=aliPayload()',t.ctx);await vm.runInContext('submitAliyunPorts()',t.ctx);
  assert.equal(vm.runInContext('aliyunBusy',t.ctx),false);assert.equal(t.nodes.aliApply.textContent,'确认开放端口');assert.equal(t.nodes.aliRegion.disabled,false);assert.ok(t.nodes.aliActionStatus.textContent);assert.equal(t.nodes.aliApply.disabled,true);
 }
 const html=fs.readFileSync(__dirname+'/app/static/index.html','utf8');
 for(const name of ['submitAliyunPorts','previewAliyunPorts','loadAliyunRules']){assert.ok(html.includes(`onclick="${name}()"`));assert.ok(!html.includes(`id="${name}"`));}
 console.log('PASS: preview, confirmed submission, readback, denial, non-JSON, timeout recovery, handler names');
})().catch(e=>{console.error(e);process.exit(1)});
