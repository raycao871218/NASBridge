// Compare local calendar days, rather than elapsed 24-hour periods.
function formatPanelDate(value, {dateOnly=false, now=new Date()}={}) {
  if(value==null||value==='')return '—';
  const plainDate=typeof value==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(value);
  const date=plainDate?new Date(value+'T00:00:00'):new Date(value);
  if(Number.isNaN(date.getTime()))return '—';
  const day=d=>Date.UTC(d.getFullYear(),d.getMonth(),d.getDate());
  const age=(day(now)-day(date))/86400000;
  const relative=['今天','昨天','前天'][age];
  const label=relative||date.toLocaleDateString('zh-CN');
  if(dateOnly||plainDate)return label;
  return label+' '+date.toLocaleTimeString('zh-CN',{hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'});
}
