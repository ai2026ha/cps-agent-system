const $ = s => document.querySelector(s);
let token = localStorage.getItem('cps_token') || '';
let actorType = localStorage.getItem('cps_actor_type') || 'admin';
let currentView = 'dashboard';
let currentUser = null;
let agentSearch = {agent_account:'', public_agent_id:'', parent:''};
let systemMetricsTimer = null;
let systemMetricsLoading = false;

const titles = {
 dashboard:['数据总览','CPS 运营核心指标'], agents:['下级渠道','管理当前账号直属下级渠道'], settlements:['渠道结算','按周期计算代理佣金'],
 players:['玩家列表','玩家通过代理专属注册地址注册后自动进入列表'], platformOrders:['平台币订单','平台币充值订单记录'], mallOrders:['商城订单','商城购买订单记录'],
 shipments:['发货查询','商城订单发货状态'], gifts:['礼包列表','礼包类商品'], products:['商品列表','普通商城商品'], cdk:['兑换码列表','CDK 批次与兑换统计'],
 rechargeRules:['累充列表','累计充值奖励规则'], claims:['领取记录','玩家累充奖励领取情况'], sendMail:['发送邮件','向玩家或区服发送游戏邮件'], mailRecords:['发送记录','历史邮件发送记录']
};


function ensureToastRoot(){
  let root=document.getElementById('toastRoot');
  if(!root){
    root=document.createElement('div');
    root.id='toastRoot';
    root.className='toast-root';
    root.setAttribute('aria-live','polite');
    root.setAttribute('aria-atomic','true');
    document.body.appendChild(root);
  }
  return root;
}
function showToast(message,type='success',duration=2400){
  const root=ensureToastRoot();
  const el=document.createElement('div');
  el.className=`toast toast-${type}`;
  const icon=type==='success'?'✓':type==='error'?'!':'…';
  el.innerHTML=`<span class="toast-icon">${icon}</span><span class="toast-message">${esc(message)}</span>`;
  root.appendChild(el);
  requestAnimationFrame(()=>el.classList.add('show'));
  const remove=()=>{el.classList.remove('show');setTimeout(()=>el.remove(),180)};
  if(duration>0)setTimeout(remove,duration);
  return {remove};
}
function refreshCurrentViewQuietly(){
  // 渠道新增/编辑成功后不要先清空页面再显示“加载中”，直接后台刷新列表。
  if(currentView==='agents') return renderAgents();
  return loadView(currentView);
}
function nextPaint(){return new Promise(resolve=>requestAnimationFrame(()=>resolve()))}

function formatApiError(detail){
  if(!detail) return '请求失败';
  if(typeof detail==='string') return detail;
  if(Array.isArray(detail)){
    const labels={username:'登录账号',password:'登录密码',agent_name:'代理名称',agent_level:'代理等级',subagent_limit:'可开通下级代理数量',commission_rate:'佣金比例',status:'后台状态',parent_agent_id:'更改归属'};
    return detail.map(item=>{
      if(typeof item==='string') return item;
      const key=Array.isArray(item?.loc)?item.loc[item.loc.length-1]:'';
      const label=labels[key]||key||'参数';
      const type=String(item?.type||'');
      if(key==='password' && type.includes('string_too_short')) return '登录密码至少需要 8 位';
      if(key==='commission_rate' && (type.includes('less_than_equal')||type.includes('greater_than_equal'))) return '佣金比例必须在 0% 到 100% 之间';
      return `${label}：${item?.msg||'参数格式错误'}`;
    }).join('\n');
  }
  if(typeof detail==='object') return detail.message||detail.msg||JSON.stringify(detail);
  return String(detail);
}

async function api(path, options={}){
  const headers = {'Content-Type':'application/json', ...(options.headers||{})};
  if(token) headers.Authorization = `Bearer ${token}`;
  const r = await fetch(path,{...options,headers});
  if(r.status===401 && path!='/api/auth/login'){ logout(); throw new Error('登录已失效'); }
  let data; try{data=await r.json()}catch{data={detail:'请求失败'}}
  if(!r.ok) throw new Error(formatApiError(data.detail));
  return data;
}
function logout(){ stopSystemMetricsPolling();token='';actorType='admin';currentUser=null;localStorage.removeItem('cps_token');localStorage.removeItem('cps_actor_type');$('#app').classList.add('hidden');$('#login').classList.remove('hidden'); }
$('#logoutBtn').onclick=logout;
$('#loginForm').onsubmit=async e=>{e.preventDefault();$('#loginError').textContent='';try{const r=await api('/api/auth/login',{method:'POST',body:JSON.stringify({username:$('#loginUser').value,password:$('#loginPass').value})});token=r.access_token;actorType=r.actor_type||'admin';currentUser=r;localStorage.setItem('cps_token',token);localStorage.setItem('cps_actor_type',actorType);currentView=firstAllowedView();await showApp();}catch(err){$('#loginError').textContent=err.message}};
function hasPermission(code){return Boolean(currentUser?.permissions?.includes(code));}
const viewPermissions={dashboard:'dashboard.view',agents:'channels.view',settlements:'settlements.view',players:'players.view',platformOrders:'orders.view',mallOrders:'orders.view',shipments:'shipments.view',gifts:'products.view',products:'products.view',cdk:'cdk.view',rechargeRules:'recharge.view',claims:'claims.view',sendMail:'mail.send',mailRecords:'mail.view'};
function canView(view){const code=viewPermissions[view];return !code||hasPermission(code);}
function firstAllowedView(){return Object.keys(viewPermissions).find(canView)||'dashboard';}
function applyRoleUI(){
  document.querySelectorAll('[data-permission]').forEach(el=>el.classList.toggle('hidden',!hasPermission(el.dataset.permission)));
  document.querySelectorAll('.nav-section').forEach(section=>{
    const own=section.dataset.permission;
    const children=[...section.querySelectorAll('[data-view]')].filter(x=>!x.classList.contains('hidden'));
    section.classList.toggle('hidden',(own&&!hasPermission(own))||children.length===0);
  });
}
function roleDisplayName(user){
  if(user?.actor_type==='admin' && user?.role==='superadmin') return '超级管理员';
  if(user?.actor_type==='admin') return '管理员';
  return agentLevelText(user?.agent_level)||'代理';
}
function updateIdentityBadge(){
  const el=$('#identityBadge');
  if(!el||!currentUser)return;
  el.innerHTML=`<strong>${esc(currentUser.username||'-')}</strong><span>${esc(roleDisplayName(currentUser))}</span>`;
}
async function showApp(){
  if(!currentUser){ currentUser=await api('/api/auth/me'); actorType=currentUser.actor_type||'admin'; localStorage.setItem('cps_actor_type',actorType); }
  applyRoleUI();updateIdentityBadge();if(!canView(currentView))currentView=firstAllowedView();$('#login').classList.add('hidden');$('#app').classList.remove('hidden');syncNavToView(currentView);loadView(currentView);
}

const navRoot = $('#nav');
const navViews = [...document.querySelectorAll('#nav [data-view]')];

function setSectionOpen(section, open){
  if(!section) return;
  section.classList.toggle('open', open);
  const parent = section.querySelector('.nav-parent');
  if(parent) parent.setAttribute('aria-expanded', open ? 'true' : 'false');
}

// 使用事件委托，确保一级菜单在任何登录状态下都能可靠展开/收起。
navRoot.addEventListener('click', e=>{
  const parent=e.target.closest('.nav-parent');
  if(parent && navRoot.contains(parent)){
    const section=parent.closest('.nav-section');
    setSectionOpen(section,!section.classList.contains('open'));
    return;
  }

  const viewBtn=e.target.closest('[data-view]');
  if(!viewBtn || !navRoot.contains(viewBtn) || viewBtn.classList.contains('hidden') || !canView(viewBtn.dataset.view)) return;
  navViews.forEach(x=>x.classList.remove('active'));
  viewBtn.classList.add('active');
  const section=viewBtn.closest('.nav-section');
  if(section) setSectionOpen(section,true);
  currentView=viewBtn.dataset.view;
  loadView(currentView);
});

function syncNavToView(view){
  const active=document.querySelector(`#nav [data-view="${view}"]`);
  if(!active) return;
  navViews.forEach(x=>x.classList.toggle('active',x===active));
  const section=active.closest('.nav-section');
  if(section) setSectionOpen(section,true);
}

$('#refreshBtn').onclick=()=>loadView(currentView);

// 必须在菜单 DOM 与事件全部初始化以后再恢复登录状态，避免 token 已存在时脚本提前中断。
if(token) showApp();

function esc(v){return String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
function percent(v){const n=Number(v);if(!Number.isFinite(n))return '-';const p=n*100;const text=Number.isInteger(p)?String(p):p.toFixed(2).replace(/0+$/,'').replace(/\.$/,'');return `${text}%`}
function badge(v){const s=String(v??'');const c=['paid','sent','success','active','redeemed','claimed'].includes(s)?'ok':['pending','waiting','queued','unused'].includes(s)?'warn':['failed','disabled'].includes(s)?'bad':'';return `<span class="badge ${c}">${esc(s)}</span>`}
function table(rows, cols){if(!rows?.length)return '<div class="empty">暂无数据</div>';return `<table><thead><tr>${cols.map(c=>`<th>${c[0]}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${cols.map(c=>`<td>${c[2]?c[2](r[c[1]],r):esc(r[c[1]])}</td>`).join('')}</tr>`).join('')}</tbody></table>`}
function panel(title, body, actions=''){return `<div class="panel"><div class="panel-head"><h3>${title}</h3><div class="actions">${actions}</div></div>${body}</div>`}

async function loadView(view){if(!canView(view)){currentView=firstAllowedView();return loadView(currentView)}if(view!=='dashboard')stopSystemMetricsPolling();const [t,s]=titles[view]||['',''];$('#pageTitle').textContent=t;$('#pageSub').textContent=s;$('#content').innerHTML='<div class="panel"><div class="empty">加载中...</div></div>';try{
 if(view==='dashboard') return renderDashboard();
 if(view==='agents') return renderAgents();
 if(view==='settlements') return renderList('/api/settlements',settleCols,'结算记录',hasPermission('settlements.manage')?()=>openForm('生成结算单',forms.settlement):null);
 if(view==='players') return renderPlayers();
 if(view==='platformOrders') return renderList('/api/orders/platform',platformCols,'平台币订单',hasPermission('orders.manage')?()=>openForm('新增平台币订单',forms.platform):null);
 if(view==='mallOrders') return renderList('/api/orders/mall',mallCols,'商城订单',hasPermission('orders.manage')?()=>openForm('新增商城订单',forms.mall):null);
 if(view==='shipments') return renderList('/api/shipments',shipmentCols,'发货查询',hasPermission('shipments.manage')?()=>openForm('更新发货',forms.shipment):null);
 if(view==='gifts') return renderProducts('gift'); if(view==='products') return renderProducts('product');
 if(view==='cdk') return renderCDK();
 if(view==='rechargeRules') return renderList('/api/recharge-rules',ruleCols,'累充列表',hasPermission('recharge.manage')?()=>openForm('新增累充规则',forms.rule):null);
 if(view==='claims') return renderList('/api/claims',claimCols,'领取记录',hasPermission('claims.manage')?()=>openForm('新增领取记录',forms.claim):null);
 if(view==='sendMail') return renderSendMail(); if(view==='mailRecords') return renderList('/api/mails',mailCols,'发送记录');
 }catch(e){$('#content').innerHTML=`<div class="panel"><div class="empty error">${esc(e.message)}</div></div>`}}

const dashboardIcons={
  registrations_total:`<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/></svg>`,
  registrations_yesterday:`<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M19 8v6M16 11h6"/></svg>`,
  registrations_today:`<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="m17 11 2 2 4-4"/></svg>`,
  turnover_total:`<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 7V5a2 2 0 0 0-2-2H5a3 3 0 0 0 0 6h15v10a2 2 0 0 1-2 2H5a3 3 0 0 1-3-3V6"/><path d="M16 13h4"/></svg>`,
  turnover_yesterday:`<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 10h18M8 3v4M16 3v4M8 14h3"/></svg>`,
  turnover_today:`<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M16 8h-5a2 2 0 1 0 0 4h2a2 2 0 1 1 0 4H8M12 6v12"/></svg>`,
  commission_rate:`<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m19 5-14 14"/><circle cx="7" cy="7" r="2.5"/><circle cx="17" cy="17" r="2.5"/></svg>`,
  commission_yesterday:`<svg viewBox="0 0 24 24" aria-hidden="true"><ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v5c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 11v5c0 1.7 3.1 3 7 3s7-1.3 7-3v-5"/></svg>`,
  commission_today:`<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v18M17 7H9.5a3 3 0 0 0 0 6H14a3 3 0 0 1 0 6H6"/><path d="m18 4 2 2-2 2"/></svg>`,
  commission_total:`<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3h8l1 4h4l-3 5 1 9H5l1-9-3-5h4l1-4Z"/><path d="M9 15h6M12 12v6"/></svg>`,
  pending_abnormal:`<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 9 16H3L12 3Z"/><path d="M12 9v4M12 17h.01"/></svg>`,
  redeemed_cdk:`<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16a1 1 0 0 1 1 1v4a2.5 2.5 0 0 0 0 5v3a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-3a2.5 2.5 0 0 0 0-5V6a1 1 0 0 1 1-1Z"/><path d="m9 12 2 2 4-4"/></svg>`,
  cpu:`<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="2"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3"/><path d="M10 10h4v4h-4z"/></svg>`,
  memory:`<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="6" width="16" height="12" rx="2"/><path d="M8 10h8M8 14h5M7 3v3M11 3v3M15 3v3M19 3v3M7 18v3M11 18v3M15 18v3M19 18v3"/></svg>`,
  disk:`<svg viewBox="0 0 24 24" aria-hidden="true"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7"/><circle cx="16.5" cy="18.5" r=".5"/></svg>`
};
function dashboardMetric(label,value,kind='number',icon='registrations_total',tone='registration'){
 const shown=kind==='money'?`¥ ${Number(value||0).toFixed(2)}`:kind==='percent'?(value==null?'—':`${(Number(value)*100).toFixed(2).replace(/\.00$/,'')}%`):String(value??0);
 const svg=dashboardIcons[icon]||dashboardIcons.registrations_total;
 return `<div class="overview-metric tone-${tone}"><span class="overview-metric-icon">${svg}</span><div class="overview-metric-body"><div class="overview-metric-label">${label}</div><strong>${shown}</strong></div></div>`;
}
function dashboardGroup(title,subtitle,items,cols){
 return `<section class="overview-group"><div class="overview-group-head"><div><h3>${title}</h3><p>${subtitle}</p></div></div><div class="overview-grid cols-${cols}">${items.join('')}</div></section>`;
}
function systemMonitorTemplate(){
 return `<section class="system-monitor" id="systemMonitor">
   <div class="system-monitor-head"><div><h3>系统资源监控</h3><p>Render 服务实时运行状态 · 每 15 秒自动刷新</p></div><span class="system-monitor-updated" id="systemMetricsUpdated">正在读取...</span></div>
   <div class="system-monitor-grid">
     <div class="system-resource resource-cpu">
       <div class="system-resource-top"><span class="system-resource-icon">${dashboardIcons.cpu}</span><div><div class="system-resource-label">CPU 使用率</div><strong id="systemCpuValue">--%</strong></div></div>
       <div class="system-resource-bar"><i id="systemCpuBar"></i></div>
       <div class="system-resource-detail" id="systemCpuDetail">实时计算中</div>
     </div>
     <div class="system-resource resource-memory">
       <div class="system-resource-top"><span class="system-resource-icon">${dashboardIcons.memory}</span><div><div class="system-resource-label">内存使用率</div><strong id="systemMemoryValue">--%</strong></div></div>
       <div class="system-resource-bar"><i id="systemMemoryBar"></i></div>
       <div class="system-resource-detail" id="systemMemoryDetail">-- / -- MB</div>
     </div>
     <div class="system-resource resource-disk">
       <div class="system-resource-top"><span class="system-resource-icon">${dashboardIcons.disk}</span><div><div class="system-resource-label">硬盘使用率</div><strong id="systemDiskValue">--%</strong></div></div>
       <div class="system-resource-bar"><i id="systemDiskBar"></i></div>
       <div class="system-resource-detail" id="systemDiskDetail">-- / -- GB</div>
     </div>
   </div>
 </section>`;
}
function clampMetricPercent(v){const n=Number(v);return Number.isFinite(n)?Math.min(Math.max(n,0),100):null}
function setResourceMetric(prefix,pct,detail=''){
 const value=document.getElementById(`system${prefix}Value`);
 const bar=document.getElementById(`system${prefix}Bar`);
 const detailEl=document.getElementById(`system${prefix}Detail`);
 if(!value||!bar)return;
 const n=clampMetricPercent(pct);
 value.textContent=n==null?'--%':`${n.toFixed(1)}%`;
 bar.style.width=n==null?'0%':`${n}%`;
 if(detailEl&&detail)detailEl.textContent=detail;
}
function stopSystemMetricsPolling(){
 if(systemMetricsTimer){clearInterval(systemMetricsTimer);systemMetricsTimer=null}
 systemMetricsLoading=false;
}
async function refreshSystemMetrics(){
 if(systemMetricsLoading||currentView!=='dashboard'||!document.getElementById('systemMonitor'))return;
 systemMetricsLoading=true;
 try{
   const m=await api('/api/system/metrics');
   if(currentView!=='dashboard'||!document.getElementById('systemMonitor'))return;
   setResourceMetric('Cpu',m.cpu_percent,'Render 实例实时 CPU');
   const memoryDetail=(m.memory_used_mb!=null&&m.memory_total_mb!=null)?`${Number(m.memory_used_mb).toFixed(1)} / ${Number(m.memory_total_mb).toFixed(1)} MB`:'内存数据不可用';
   const diskDetail=(m.disk_used_gb!=null&&m.disk_total_gb!=null)?`${Number(m.disk_used_gb).toFixed(2)} / ${Number(m.disk_total_gb).toFixed(2)} GB`:'硬盘数据不可用';
   setResourceMetric('Memory',m.memory_percent,memoryDetail);
   setResourceMetric('Disk',m.disk_percent,diskDetail);
   const updated=document.getElementById('systemMetricsUpdated');
   if(updated){
     const d=m.updated_at?new Date(m.updated_at):null;
     updated.textContent=d&&!Number.isNaN(d.getTime())?`更新 ${d.toLocaleTimeString('zh-CN',{hour12:false})}`:'刚刚更新';
   }
 }catch(e){
   const updated=document.getElementById('systemMetricsUpdated');
   if(updated)updated.textContent=`监控暂时不可用${e?.message?`：${e.message}`:''}`;
   console.error('system metrics failed',e);
 }finally{systemMetricsLoading=false}
}
function startSystemMetricsPolling(){
 stopSystemMetricsPolling();
 refreshSystemMetrics();
 systemMetricsTimer=setInterval(refreshSystemMetrics,15000);
}
async function renderDashboard(){
 const d=await api('/api/dashboard');
 const registration=dashboardGroup('注册数据','玩家注册统计',[
   dashboardMetric('总注册',d.total_registrations,'number','registrations_total','registration'),
   dashboardMetric('昨日注册',d.yesterday_registrations,'number','registrations_yesterday','registration'),
   dashboardMetric('今日注册',d.today_registrations,'number','registrations_today','registration')
 ],3);
 const turnover=dashboardGroup('流水数据','仅统计已支付平台币订单',[
   dashboardMetric('总流水',d.total_turnover,'money','turnover_total','turnover'),
   dashboardMetric('昨日流水',d.yesterday_turnover,'money','turnover_yesterday','turnover'),
   dashboardMetric('今日流水',d.today_turnover,'money','turnover_today','turnover')
 ],3);

 if(d.dashboard_type==='superadmin'){
   const operations=dashboardGroup('运营数据','订单发货与兑换码状态',[
     dashboardMetric('待发货/异常',d.pending_abnormal,'number','pending_abnormal','alert'),
     dashboardMetric('已兑换CDK',d.redeemed_cdk,'number','redeemed_cdk','cdk')
   ],2);
   $('#content').innerHTML=`<div class="overview-groups">${registration}${turnover}${operations}${dashboardRegistrationCard()}${systemMonitorTemplate()}</div>`;
   bindDashboardRegistrationCopy();
   startSystemMetricsPolling();
   return;
 }

 stopSystemMetricsPolling();
 const commission=dashboardGroup('分佣数据','按当前代理佣金比例计算',[
   dashboardMetric('佣金比例',d.commission_rate,'percent','commission_rate','commission'),
   dashboardMetric('昨日分佣',d.yesterday_commission,'money','commission_yesterday','commission'),
   dashboardMetric('今日分佣',d.today_commission,'money','commission_today','commission'),
   dashboardMetric('总计分佣',d.total_commission,'money','commission_total','commission')
 ],4);
 $('#content').innerHTML=`<div class="overview-groups">${registration}${turnover}${commission}${dashboardRegistrationCard()}</div>`;
 bindDashboardRegistrationCopy();
}

function registrationUrl(agentId){return `${window.location.origin}/register/${encodeURIComponent(agentId)}`}
function currentRegistrationUrl(){
 const path=currentUser?.registration_path;
 if(path)return `${window.location.origin}${path}`;
 if(currentUser?.agent_id)return registrationUrl(currentUser.agent_id);
 return '';
}
function dashboardRegistrationCard(){
 const url=currentRegistrationUrl();
 if(!url)return '';
 const isAdmin=currentUser?.actor_type==='admin';
 const title=isAdmin?'超管专属注册地址':'我的专属注册地址';
 const note=isAdmin?'通过此地址注册的玩家直属总平台，并自动进入超管玩家列表。':`通过此地址注册的玩家会自动绑定代理 ${esc(currentUser.agent_id)}。`;
 return `<section class="registration-link-card dashboard-registration-card"><div><strong>${title}</strong><span>${note}</span></div><div class="registration-link-actions"><input value="${esc(url)}" readonly id="dashboardRegistrationUrl"><button class="btn primary" id="copyDashboardRegistration">复制地址</button></div></section>`;
}
function bindDashboardRegistrationCopy(){
 const btn=$('#copyDashboardRegistration');
 if(!btn)return;
 btn.onclick=async()=>{const url=currentRegistrationUrl();const ok=await copyText(url);showToast(ok?`注册地址已复制：${url}`:'复制失败，请手动复制注册地址',ok?'success':'error',ok?3200:4200)};
}
async function copyText(text){
 try{if(navigator.clipboard?.writeText){await navigator.clipboard.writeText(text);return true}}catch{}
 const ta=document.createElement('textarea');ta.value=text;ta.style.position='fixed';ta.style.opacity='0';document.body.appendChild(ta);ta.select();
 let ok=false;try{ok=document.execCommand('copy')}catch{}ta.remove();return ok;
}
window.copyRegistrationLink=async(agentId)=>{
 const url=registrationUrl(agentId);
 const ok=await copyText(url);
 showToast(ok?`注册地址已复制：${url}`:'复制失败，请手动复制注册地址',ok?'success':'error',ok?3200:4200);
};
async function renderPlayers(){
 const rows=await api('/api/players');
 $('#content').innerHTML=panel('玩家列表',`<div class="table-scroll">${table(rows,playerCols)}</div>`);
}

async function renderList(path, cols, title, addFn){const rows=await api(path);$('#content').innerHTML=panel(title,table(rows,cols),addFn?'<button class="btn primary" id="addBtn">＋ 新增</button>':'');if(addFn)$('#addBtn').onclick=addFn}
function agentLevelText(v){return ({1:'一级代理',2:'二级代理',3:'三级代理'})[Number(v)]||'-'}
function agentSearchQuery(){
  const p=new URLSearchParams();
  Object.entries(agentSearch).forEach(([k,v])=>{if(v)p.set(k,v)});
  const qs=p.toString();
  return qs?`?${qs}`:'';
}
function agentSearchBar(){
  return `<div class="agent-search-bar">
    <div class="query-field"><label>代理账号查询</label><input id="agentAccountQuery" value="${esc(agentSearch.agent_account)}" placeholder="输入代理登录账号"></div>
    <div class="query-field"><label>代理ID查询</label><input id="agentIdQuery" value="${esc(agentSearch.public_agent_id)}" placeholder="例如 A1"></div>
    <div class="query-field"><label>上级代理查询</label><input id="parentAgentQuery" value="${esc(agentSearch.parent)}" placeholder="代理ID/账号/名称"></div>
    <div class="query-actions"><button class="btn primary" id="agentQueryBtn">查询</button><button class="btn" id="agentResetBtn">重置</button></div>
  </div>`;
}
function readAgentSearch(){
  return {
    agent_account:$('#agentAccountQuery')?.value.trim()||'',
    public_agent_id:$('#agentIdQuery')?.value.trim()||'',
    parent:$('#parentAgentQuery')?.value.trim()||''
  };
}
async function renderAgents(){
  const [rows,caps]=await Promise.all([api('/api/agents'+agentSearchQuery()),api('/api/agents/capabilities')]);
  const quota = caps.current_level===0
    ? `<div class="quota-card"><strong>当前身份：超级管理员</strong><span>可开通：一级代理</span></div>`
    : `<div class="quota-card"><strong>当前等级：${esc(caps.current_level_name)}</strong><span>${caps.allowed_child_level_name?`可开通：${esc(caps.allowed_child_level_name)}`:'已到最高代理等级'}</span></div>`;
  const action=caps.can_create?'<button class="btn primary" id="addBtn">＋ 新增代理</button>':`<button class="btn" disabled title="${esc(caps.reason)}">${esc(caps.reason)}</button>`;
  const scopeNote=caps.current_level===0?'<div class="query-scope-note">超级管理员查询范围：全部一级、二级、三级代理；代理账号登录后仅查询自己的直属下级。</div>':'';
  $('#content').innerHTML=quota+panel('下级渠道',agentSearchBar()+scopeNote+`<div class="table-scroll agent-table-scroll">${table(rows,agentCols)}</div>`,action);
  $('#agentQueryBtn').onclick=async()=>{agentSearch=readAgentSearch();await renderAgents();};
  $('#agentResetBtn').onclick=async()=>{agentSearch={agent_account:'',public_agent_id:'',parent:''};await renderAgents();};
  ['agentAccountQuery','agentIdQuery','parentAgentQuery'].forEach(id=>{const el=$('#'+id);if(el)el.addEventListener('keydown',e=>{if(e.key==='Enter')$('#agentQueryBtn').click()})});
  if(caps.can_create) $('#addBtn').onclick=()=>openForm('新增代理',buildAgentForm(caps));
}

function statusText(v){return String(v)==='disabled'?'封禁':'正常'}
function agentStatusBadge(v){const disabled=String(v)==='disabled';return `<span class="badge ${disabled?'bad':'ok'}">${disabled?'封禁':'正常'}</span>`}
window.openAgentEdit=async(agentPk)=>{
  try{
    const data=await api(`/api/agents/${agentPk}/edit-options`);
    const row=data.agent;
    const isThird=Number(row.agent_level)===3;
    const fullEdit=Boolean(data.can_full_edit);
    const parentValue=Number(row.agent_level)===1?'SUPERADMIN':(row.parent_agent_id||'');
    const fields=[
      ['agent_name','代理名称','text',true,{placeholder:'请输入代理名称'}],
      ['commission_rate','佣金比例(%)','number',true,{min:0,max:100,step:0.01,placeholder:'例如：50 表示 50%'}]
    ];
    if(fullEdit){
      fields.push(
        ['password','修改密码','password',false,{autocomplete:'new-password',placeholder:'留空则不修改；至少 8 位'}],
        ['status','后台状态','select',true,{options:[{value:'active',label:'正常'},{value:'disabled',label:'封禁后台'}]}],
        ['subagent_limit','可开通下级代理数量','number',true,{min:0,max:9999,step:1,readonly:isThird,placeholder:isThird?'三级代理固定为 0':'例如：10'}]
      );
      if(data.can_change_parent){
        fields.push(['parent_agent_id','更改归属','select',true,{options:data.parent_options||[],valueType:'string'}]);
      }
    }
    const note=fullEdit
      ? `代理ID：${row.agent_id} ｜ 代理等级：${agentLevelText(row.agent_level)}。超管可修改完整代理资料；修改密码留空表示保持原密码。`
      : `代理ID：${row.agent_id} ｜ 代理等级：${agentLevelText(row.agent_level)}。当前账号仅可修改直属下级的代理名称和佣金比例。`;
    const defaults={
      agent_name:row.agent_name||'',
      commission_rate:Number(row.commission_rate||0)*100
    };
    if(fullEdit){
      Object.assign(defaults,{
        password:'',status:row.status||'active',subagent_limit:isThird?0:Number(row.subagent_limit||0),parent_agent_id:parentValue
      });
    }
    openForm(`编辑代理 · ${row.agent_id}`,{
      path:`/api/agents/${agentPk}`,
      method:'PATCH',
      note,
      defaults,
      fields,
      transform:obj=>{
        const name=String(obj.agent_name||'').trim();if(!name)throw new Error('代理名称不能为空');
        const p=Number(obj.commission_rate??0);if(!Number.isFinite(p)||p<0||p>100)throw new Error('佣金比例必须在 0% 到 100% 之间');
        const out={agent_name:name,commission_rate:p/100};
        if(fullEdit){
          const limit=Number(obj.subagent_limit??0);if(!Number.isInteger(limit)||limit<0||limit>9999)throw new Error('可开通下级代理数量必须是 0 到 9999 的整数');
          if(isThird&&limit!==0)throw new Error('三级代理不能继续开通下级代理，数量必须为 0');
          out.password=obj.password||'';
          if(!out.password)delete out.password;
          out.status=obj.status;
          out.subagent_limit=isThird?0:limit;
          if(data.can_change_parent)out.parent_agent_id=obj.parent_agent_id;
        }
        return out;
      }
    });
  }catch(e){alert(e.message)}
};

async function renderProducts(cat){const rows=await api('/api/products?category='+cat);const manage=hasPermission('products.manage');$('#content').innerHTML=panel(cat==='gift'?'礼包列表':'商品列表',table(rows,productCols),manage?'<button class="btn primary" id="addBtn">＋ 新增</button>':'');if(manage)$('#addBtn').onclick=()=>openForm(cat==='gift'?'新增礼包':'新增商品',{...forms.product,defaults:{category:cat}})}
async function renderCDK(){const rows=await api('/api/redemption-batches');const manage=hasPermission('cdk.manage');$('#content').innerHTML=panel('兑换码批次',table(rows,cdkCols),manage?'<button class="btn primary" id="addBtn">＋ 新建批次</button> <button class="btn" id="genBtn">生成CDK</button>':'');if(manage){$('#addBtn').onclick=()=>openForm('新建CDK批次',forms.cdk);$('#genBtn').onclick=()=>openForm('生成兑换码',forms.generateCDK)}}
function renderSendMail(){$('#content').innerHTML=panel('发送游戏邮件','<p style="color:#7c879d">当前第一版会完整记录发送任务；接入你的游戏服邮件 API 后即可改为真实投递。</p><button class="btn primary" id="mailBtn">发送邮件</button>');$('#mailBtn').onclick=()=>openForm('发送邮件',forms.mail)}

const agentCols=[['代理ID','agent_id'],['代理等级','agent_level',agentLevelText],['代理名称','agent_name'],['账号','username'],['邀请码','invite_code'],['上级代理','parent_agent_display'],['今日流水','today_turnover'],['昨日流水','yesterday_turnover'],['总流水','total_turnover'],['佣金比例','commission_rate',percent],['状态','status',agentStatusBadge],['注册地址','agent_id',(v)=>`<button class="btn compact" onclick="copyRegistrationLink('${esc(v)}')">复制地址</button>`],['操作','id',(_,r)=>hasPermission('channels.edit_basic')||hasPermission('channels.edit_full')?`<button class="btn compact" onclick="openAgentEdit(${Number(r.id)})">编辑</button>`:'-']];
const playerCols=[['玩家ID','player_id'],['账号','username'],['角色名','role_name'],['区服','server_name'],['所属代理','agent_public_id'],['今日充值','today_recharge'],['总充值','total_recharge'],['注册时间','created_at'],['最后登录','last_login_at'],['登录IP','last_login_ip']];
const platformCols=[['订单号','order_no'],['玩家PK','player_id'],['代理PK','agent_id'],['金额','amount'],['平台币','platform_coin'],['支付渠道','payment_channel'],['支付状态','pay_status',badge],['创建时间','created_at']];
const mallCols=[['订单号','order_no'],['玩家PK','player_id'],['商品PK','product_id'],['数量','quantity'],['金额','amount'],['支付','pay_status',badge],['发货','delivery_status',badge],['创建时间','created_at']];
const shipmentCols=[['订单号','order_no'],['订单PK','mall_order_id'],['发货状态','delivery_status',badge],['服务商','provider'],['发货单号','tracking_no'],['任务状态','shipment_status',badge],['说明','message'],['发货时间','sent_at']];
const productCols=[['SKU','sku'],['名称','name'],['分类','category'],['价格','price'],['库存','stock'],['状态','enabled',v=>badge(v?'active':'disabled')],['说明','description']];
const cdkCols=[['CDK名称','name'],['总数','total_count'],['未兑换数','unused_count'],['已兑换数','redeemed_count'],['状态','enabled',v=>badge(v?'active':'disabled')],['创建时间','created_at']];
const settleCols=[['结算单','settlement_no'],['代理PK','agent_id'],['开始','period_start'],['结束','period_end'],['流水','turnover'],['佣金比例','commission_rate',percent],['佣金','commission_amount'],['状态','status',badge]];
const ruleCols=[['名称','name'],['累充门槛','threshold_amount'],['奖励内容','reward_content'],['状态','enabled',v=>badge(v?'active':'disabled')]];
const claimCols=[['玩家PK','player_id'],['规则PK','rule_id'],['状态','status',badge],['领取时间','claimed_at']];
const mailCols=[['标题','title'],['目标类型','target_type'],['目标','target_value'],['状态','send_status',badge],['发送人','created_by'],['发送时间','sent_at']];

function buildAgentForm(caps){
 const allowed=Number(caps.allowed_child_level);
 // 代理等级必须由当前登录身份决定，但下拉框应当是真正可操作的。
 // 只展示当前账号有权开通的等级，避免把其他等级做成灰色禁用项造成“选择不了”的误解。
 const options=[
   {value:'',label:'请选择代理等级'},
   {value:allowed,label:agentLevelText(allowed)}
 ];
 const isThird=allowed===3;
 return {
  path:'/api/agents',
  note:`代理ID由系统按 A1/A2/A3… 自动生成，邀请码直接采用代理ID，上级归属也由系统自动处理。当前${esc(caps.current_level_name)}只能开通${agentLevelText(allowed)}。${isThird?'三级代理为末级，不能再开通下级。':'“可开通下级代理数量”是该新代理未来可以创建的直属下级上限。'} 佣金比例填写百分比，例如 50 表示 50%。`,
  defaults:{username:'',password:'',agent_name:'',agent_level:'',subagent_limit:isThird?0:1,commission_rate:''},
  fields:[
   ['username','登录账号','text',true,{autocomplete:'off'}],['password','登录密码','password',true,{autocomplete:'new-password'}],['agent_name','代理名称'],
   ['agent_level','代理等级','select',true,{options,valueType:'number'}],
   ['subagent_limit','可开通下级代理数量','number',true,{min:0,max:9999,step:1,readonly:isThird,placeholder:isThird?'三级代理固定为 0':'例如：10'}],
   ['commission_rate','佣金比例(%)','number',false,{min:0,max:100,step:0.01,placeholder:'例如：50 表示 50%'}]
  ],
  transform:obj=>{
    const p=obj.commission_rate??0;if(p<0||p>100)throw new Error('佣金比例必须在 0% 到 100% 之间');
    if(obj.agent_level===undefined||obj.agent_level===null||obj.agent_level==='')throw new Error('请选择代理等级');
    if(Number(obj.agent_level)!==allowed)throw new Error(`当前账号只能开通${agentLevelText(allowed)}`);
    const limit=Number(obj.subagent_limit??0);if(!Number.isInteger(limit)||limit<0||limit>9999)throw new Error('可开通下级代理数量必须是 0 到 9999 的整数');
    if(isThird && limit!==0)throw new Error('三级代理不能继续开通下级代理，数量必须为 0');
    return {...obj,agent_level:allowed,subagent_limit:isThird?0:limit,commission_rate:p/100};
  }
 };
}

const forms={
 platform:{path:'/api/orders/platform',fields:[['order_no','订单号'],['player_id','玩家PK','number'],['agent_id','代理PK','number',false],['amount','金额','number'],['platform_coin','平台币数量','number'],['payment_channel','支付渠道'],['pay_status','支付状态']]},
 mall:{path:'/api/orders/mall',fields:[['order_no','订单号'],['player_id','玩家PK','number'],['agent_id','代理PK','number',false],['product_id','商品PK','number'],['quantity','数量','number'],['amount','金额','number'],['pay_status','支付状态']]},
 shipment:{path:'/api/shipments',fields:[['mall_order_id','商城订单PK','number'],['provider','发货服务商'],['tracking_no','发货单号','text',false],['status','状态(sent/failed/success)'],['message','说明','textarea',false]]},
 product:{path:'/api/products',fields:[['sku','SKU'],['name','名称'],['category','分类(gift/product)'],['price','价格','number'],['stock','库存','number'],['description','说明','textarea',false]]},
 cdk:{path:'/api/redemption-batches',fields:[['name','CDK名称']]},
 generateCDK:{path:null,fields:[['batch_id','CDK批次PK','number'],['count','生成数量','number'],['prefix','前缀']]},
 settlement:{path:'/api/settlements',fields:[['agent_id','代理PK','number'],['period_start','开始日期','date'],['period_end','结束日期','date']]},
 rule:{path:'/api/recharge-rules',fields:[['name','规则名称'],['threshold_amount','累充门槛','number'],['reward_content','奖励内容','textarea']]},
 claim:{path:'/api/claims',fields:[['player_id','玩家PK','number'],['rule_id','规则PK','number']]},
 mail:{path:'/api/mails',fields:[['title','邮件标题'],['content','邮件内容','textarea'],['target_type','目标类型(player/server/all)'],['target_value','玩家ID/区服，可空','text',false]]}
};
function inputAttrs(meta,type){
 const attrs=[];
 if(type==='number') attrs.push(`step="${meta?.step??'any'}"`);
 if(meta?.min!==undefined)attrs.push(`min="${meta.min}"`);
 if(meta?.max!==undefined)attrs.push(`max="${meta.max}"`);
 if(meta?.placeholder)attrs.push(`placeholder="${esc(meta.placeholder)}"`);
 if(meta?.readonly)attrs.push('readonly');
 if(meta?.autocomplete)attrs.push(`autocomplete="${esc(meta.autocomplete)}"`);
 return attrs.join(' ');
}
function fieldControl(name,type,val,required,meta){
 if(type==='textarea') return `<textarea name="${name}" ${required?'required':''}>${esc(val)}</textarea>`;
 if(type==='select'){
   const options=(meta?.options||[]).map(o=>`<option value="${esc(o.value)}" ${String(o.value)===String(val)?'selected':''} ${o.disabled?'disabled':''}>${esc(o.label)}</option>`).join('');
   return `<select name="${name}" ${required?'required':''}>${options}</select>`;
 }
 return `<input name="${name}" type="${type}" value="${esc(val)}" ${required?'required':''} ${inputAttrs(meta,type)}/>`;
}
function openForm(title,cfg){
  $('#modalTitle').textContent=title;
  const defaults=cfg.defaults||{};
  const form=$('#modalForm');
  form.setAttribute('autocomplete','off');
  form.innerHTML=`${cfg.note?`<div class="form-hint">${esc(cfg.note)}</div>`:''}<div class="form-grid">${cfg.fields.map(f=>{const [name,label,type='text',required=true,meta=null]=f;const val=defaults[name]??'';return `<div class="${type==='textarea'?'full':''}"><label>${label}</label>${fieldControl(name,type,val,required,meta)}</div>`}).join('')}<div class="form-actions"><button type="button" class="btn" id="cancelForm">取消</button><button class="btn primary form-submit-btn">提交</button></div></div>`;
  $('#modal').classList.remove('hidden');
  $('#cancelForm').onclick=closeModal;
  form.onsubmit=async e=>{
    e.preventDefault();
    const submitBtn=e.submitter||form.querySelector('.form-submit-btn');
    const cancelBtn=$('#cancelForm');
    const originalText=submitBtn?.textContent||'提交';
    const pendingText=cfg.pendingText||(title.startsWith('编辑代理')?'保存中…':title==='新增代理'?'创建中…':'提交中…');
    if(submitBtn){submitBtn.disabled=true;submitBtn.classList.add('is-busy');submitBtn.textContent=pendingText;}
    if(cancelBtn)cancelBtn.disabled=true;
    form.setAttribute('aria-busy','true');
    // 先让“处理中”状态绘制出来，再发请求，避免用户感觉点击无反馈。
    await nextPaint();
    let obj={};
    new FormData(e.target).forEach((v,k)=>{if(v==='')return;const f=cfg.fields.find(x=>x[0]===k);obj[k]=f?.[2]==='number'||(f?.[2]==='select'&&f?.[4]?.valueType==='number')?Number(v):v});
    try{
      if(cfg.transform)obj=cfg.transform(obj);
      let path=cfg.path;
      if(title==='生成兑换码'){path=`/api/redemption-batches/${obj.batch_id}/generate`;delete obj.batch_id}
      const r=await api(path,{method:cfg.method||'POST',body:JSON.stringify(obj)});
      const message=r.message||`操作成功${r.generated?`，已生成 ${r.generated} 个`:''}`;
      // 请求一确认成功就立即关闭弹窗并给反馈；列表刷新放到后台，不阻塞成功提示。
      closeModal();
      showToast(message,'success',2600);
      Promise.resolve(refreshCurrentViewQuietly()).catch(err=>showToast(`数据刷新失败：${err.message}`,'error',4200));
    }catch(err){
      showToast(err.message,'error',4200);
      form.removeAttribute('aria-busy');
      if(submitBtn){submitBtn.disabled=false;submitBtn.classList.remove('is-busy');submitBtn.textContent=originalText;}
      if(cancelBtn)cancelBtn.disabled=false;
    }
  };
}
function closeModal(){$('#modal').classList.add('hidden')}$('#closeModal').onclick=closeModal;$('#modal').onclick=e=>{if(e.target===$('#modal'))closeModal()};
