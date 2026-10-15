const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const context=vm.createContext({});
vm.runInContext(fs.readFileSync(__dirname+'/app/static/date-format.js','utf8'),context);
const format=(value,now='2026-10-15T00:05:00',dateOnly=false)=>vm.runInContext(`formatPanelDate(${JSON.stringify(value)}, {now:new Date(${JSON.stringify(now)}),dateOnly:${dateOnly}})`,context);
assert.equal(format('2026-10-15T00:01:02'),'今天 00:01:02');
assert.equal(format('2026-10-14T23:59:58'),'昨天 23:59:58');
assert.equal(format('2026-10-13T01:02:03'),'前天 01:02:03');
assert.equal(format('2026-10-12T23:59:58'),'2026/10/12 23:59:58');
assert.equal(format('2026-10-16T01:02:03'),'2026/10/16 01:02:03');
assert.equal(format('2026-10-14'),'昨天');
assert.equal(format('2026-10-13T01:02:03',undefined,true),'前天');
assert.equal(format('2025-12-31T23:59:00','2026-01-01T00:01:00'),'昨天 23:59:00');
assert.equal(format('2024-02-29','2024-03-02T12:00:00'),'前天');
for(const value of [null,'','invalid'])assert.equal(format(value),'—');
// Calendar-day comparisons remain correct across daylight-saving transitions.
assert.equal(format('2026-03-07T23:59:00','2026-03-09T00:01:00'),'前天 23:59:00');
console.log('PASS: relative calendar dates, midnight, older/future dates, date-only, year/leap-day and DST boundaries');
