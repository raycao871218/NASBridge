const vm = require('node:vm');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const html = fs.readFileSync(__dirname + '/app/static/index.html', 'utf8');
const source = html.slice(html.indexOf('let mihomoConfigRevision='), html.indexOf('async function controlMihomo('));
const nodes = {mihomoConfigText: {value: ''}, mihomoConfigStatus: {textContent: ''}};
const requests = [];
let conflict = false;
const context = vm.createContext({
  document: {getElementById: id => nodes[id]}, confirm: () => true, load: async () => {},
  fetch: async (url, options) => {
    requests.push({url, options});
    const write = options.method === 'PUT';
    return {ok: !(write && conflict), json: async () => write
      ? conflict ? {detail: '配置已变更'} : {revision: 'revision-B', backup: '/backup'}
      : {content: 'external-controller: original', revision: 'revision-A', size: 29}};
  }
});
vm.runInContext(source, context);
(async () => {
  await vm.runInContext('loadMihomoConfig()', context);
  nodes.mihomoConfigText.value = 'external-controller: edited';
  await vm.runInContext('applyMihomoConfig()', context);
  assert.equal(JSON.parse(requests[1].options.body).expected_revision, 'revision-A');
  conflict = true;
  await vm.runInContext('applyMihomoConfig()', context);
  assert.equal(JSON.parse(requests[2].options.body).expected_revision, 'revision-B');
  assert.match(nodes.mihomoConfigStatus.textContent, /配置已变更/);
  assert.equal(nodes.mihomoConfigText.value, 'external-controller: edited');
  console.log('PASS: config revision read, save, update and conflict preserve edits');
})().catch(error => {console.error(error); process.exitCode = 1;});
