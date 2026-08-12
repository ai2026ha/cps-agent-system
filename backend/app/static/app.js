const $ = s => document.querySelector(s);
function beijingTodayString(){
  const parts=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());
  const map=Object.fromEntries(parts.filter(p=>p.type!=='literal').map(p=>[p.type,p.value]));
  return `${map.year}-${map.month}-${map.day}`;
}
let token = localStorage.getItem('cps_token') || '';
let actorType = localStorage.getItem('cps_actor_type') || 'admin';
let currentView = 'dashboard';
let currentUser = null;
let agentSearch = {agent_account:'', public_agent_id:'', parent:''};
let playerSearch = {account:'', role:'', parent:''};
let platformOrderSearch = {order_no:'', account:'', payment_method:'', status:'', start_date:'', end_date:''};
let mallOrderSearch = {account:'', product:''};
let settlementSearch = {account:'', public_agent_id:'', agent_level:'', start_date:'', end_date:''};
let systemMetricsTimer = null;
let systemMetricsLoading = false;
let paymentTestState = {players:[], selectedAccount:'', order:null};
let playerBehaviorTestState = {characters:[], selectedCharacterId:0, keyword:'', gifts:[], cards:[], cumulative:{rules:[]}, searchMessage:''};

const titles = {
 dashboard:['数据总览','CPS 运营核心指标'], agents:['下级渠道','管理当前账号直属下级渠道'], settlements:['渠道结算','查看下级代理真实支付总流水或按北京时间日期区间查询'],
 players:['玩家列表','玩家通过代理专属注册地址注册后自动进入列表'], playerBehaviorTest:['玩家行为测试','按真实角色模拟礼包购买、特权卡购买与累充领取'], platformOrders:['平台币订单','玩家充值支付自动生成的订单记录'], paymentTest:['支付测试','仅超级管理员模拟玩家平台币充值完整流程'], mallOrders:['商城订单','玩家中心使用平台币购买礼包后自动生成的订单记录'],
 shipments:['发货查询','商城订单发货状态'], gifts:['礼包列表','礼包类商品'], products:['商品列表','普通商城商品'], privilegeCards:['特权卡配置','周卡、月卡、年卡价格与每日奖励'], cdk:['兑换码列表','CDK 批次与兑换统计'],
 rechargeRules:['累充列表','累计充值奖励规则'], claims:['领取记录','玩家累充奖励领取情况'], sendMail:['发送邮件','向玩家或区服发送游戏邮件'], mailRecords:['发送记录','历史邮件发送记录'],
 profileSettings:['个人信息','查看当前后台账号并修改登录密码'], adminManagers:['管理员','新增和查看超级管理员账号'], systemEditor:['系统编辑','修改后台与玩家中心显示名称'], ipWhitelist:['白名单','限制后台访问IP并管理登录拉黑名单']
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
    const labels={username:'登录账号',password:'登录密码',agent_name:'代理名称',agent_level:'代理等级',subagent_limit:'可开通下级代理数量',commission_rate:'佣金比例',status:'后台状态',parent_agent_id:'更改归属',owner_agent_id:'玩家归属',coin_action:'平台币操作',coin_amount:'平台币数量'};
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
function showLogin(){
  $('#app').classList.add('hidden');
  $('#login').classList.remove('hidden');
}
function logout(){ stopSystemMetricsPolling();token='';actorType='admin';currentUser=null;localStorage.removeItem('cps_token');localStorage.removeItem('cps_actor_type');showLogin(); }
$('#logoutBtn').onclick=logout;
$('#loginForm').onsubmit=async e=>{e.preventDefault();const btn=$('#loginForm button[type="submit"]');const feedback=$('#loginError');feedback.textContent='';feedback.classList.remove('success');if(btn)btn.disabled=true;try{const r=await api('/api/auth/login',{method:'POST',body:JSON.stringify({username:$('#loginUser').value,password:$('#loginPass').value})});token=r.access_token;actorType=r.actor_type||'admin';currentUser=r;localStorage.setItem('cps_token',token);localStorage.setItem('cps_actor_type',actorType);currentView=firstAllowedView();feedback.textContent='登录成功';feedback.classList.add('success');await new Promise(resolve=>setTimeout(resolve,420));await showApp();}catch(err){feedback.classList.remove('success');feedback.textContent=err.message}finally{if(btn)btn.disabled=false}};
function hasPermission(code){return Boolean(currentUser?.permissions?.includes(code));}
const viewPermissions={dashboard:'dashboard.view',agents:'channels.view',settlements:'settlements.view',players:'players.view',playerBehaviorTest:'payment.test',platformOrders:'orders.view',paymentTest:'payment.test',mallOrders:'orders.view',shipments:'shipments.view',gifts:'products.view',products:'products.view',privilegeCards:'privilege.manage',cdk:'cdk.view',rechargeRules:'recharge.view',claims:'claims.view',sendMail:'mail.send',mailRecords:'mail.view',profileSettings:'system.settings',adminManagers:'system.admins.manage',systemEditor:'system.branding.manage',ipWhitelist:'system.ip_access.manage'};
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
let brandIconOptions=[];
function resolveBrandIcon(data){
  if(Array.isArray(data?.available_icons)&&data.available_icons.length)brandIconOptions=data.available_icons;
  const id=String(data?.backend_logo||'dragon-spiral');
  return brandIconOptions.find(x=>x.id===id)||brandIconOptions.find(x=>x.id==='dragon-spiral')||{id:'dragon-spiral',path:'/static/brand-icons/dragon-spiral.svg'};
}
function renderSidebarBrandName(brand,backendName){
  if(!brand)return;
  brand.replaceChildren();
  const source=String(backendName||'CPS');
  const regex=/CPS/ig;
  let cursor=0;
  let match;
  while((match=regex.exec(source))!==null){
    if(match.index>cursor){
      const part=document.createElement('span');
      part.className='brand-name-segment';
      part.textContent=source.slice(cursor,match.index);
      brand.append(part);
    }
    const accent=document.createElement('span');
    accent.className='brand-name-segment brand-cps-accent';
    accent.textContent=match[0];
    brand.append(accent);
    cursor=match.index+match[0].length;
  }
  if(cursor<source.length){
    const tail=document.createElement('span');
    tail.className='brand-name-segment';
    tail.textContent=source.slice(cursor);
    brand.append(tail);
  }
  if(!brand.childNodes.length){
    const fallback=document.createElement('span');
    fallback.className='brand-name-segment';
    fallback.textContent=source;
    brand.append(fallback);
  }
  const visualLength=[...source].length;
  brand.classList.toggle('brand-name-long',visualLength>=9);
  brand.classList.toggle('brand-name-xlong',visualLength>=12);
  brand.title=source;
}
function applySystemBranding(data){
  if(!data)return;
  const backendName=String(data.backend_name||'CPS').trim()||'CPS';
  const brand=$('#sidebarBrandName');
  renderSidebarBrandName(brand,backendName);
  const logo=$('#sidebarBrandLogo');
  if(logo){
    const icon=resolveBrandIcon(data);
    logo.style.setProperty('--brand-logo-url',`url("${icon.path}")`);
    logo.title=icon.name||'后台图标';
  }
  document.title=`${backendName} · 管理后台`;
}
async function loadSystemBranding(){
  try{const data=await api('/api/public/system-branding');applySystemBranding(data);return data}catch(_){return null}
}
async function showApp(){
  if(!currentUser){ currentUser=await api('/api/auth/me'); actorType=currentUser.actor_type||'admin'; localStorage.setItem('cps_actor_type',actorType); }
  await loadSystemBranding();
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

// 首屏不先渲染登录页：先检查本地登录令牌，确认后再显示后台或登录界面。
// 这样刷新时不会出现“登录页 -> 后台”的闪跳。
if(token) showApp().catch(()=>showLogin());
else showLogin();

function esc(v){return String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
function percent(v){const n=Number(v);if(!Number.isFinite(n))return '-';const p=n*100;const text=Number.isInteger(p)?String(p):p.toFixed(2).replace(/0+$/,'').replace(/\.$/,'');return `${text}%`}
function badge(v){const s=String(v??'');const c=['paid','sent','success','active','redeemed','claimed'].includes(s)?'ok':['pending','waiting','queued','unused'].includes(s)?'warn':['failed','disabled'].includes(s)?'bad':'';return `<span class="badge ${c}">${esc(s)}</span>`}
function table(rows, cols){if(!rows?.length)return '<div class="empty">暂无数据</div>';return `<table><thead><tr>${cols.map(c=>`<th>${c[0]}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${cols.map(c=>`<td>${c[2]?c[2](r[c[1]],r):esc(r[c[1]])}</td>`).join('')}</tr>`).join('')}</tbody></table>`}
function panel(title, body, actions=''){return `<div class="panel"><div class="panel-head"><h3>${title}</h3><div class="actions">${actions}</div></div>${body}</div>`}

async function loadView(view){if(!canView(view)){currentView=firstAllowedView();return loadView(currentView)}if(view!=='dashboard')stopSystemMetricsPolling();const [t,s]=titles[view]||['',''];$('#pageTitle').textContent=t;$('#pageSub').textContent=s;$('#content').innerHTML='<div class="panel"><div class="empty">加载中...</div></div>';try{
 if(view==='dashboard') return renderDashboard();
 if(view==='agents') return renderAgents();
 if(view==='settlements') return renderSettlements();
 if(view==='players') return renderPlayers();
 if(view==='playerBehaviorTest') return renderPlayerBehaviorTest();
 if(view==='platformOrders') return renderPlatformOrders();
 if(view==='paymentTest') return renderPaymentTest();
 if(view==='mallOrders') return renderMallOrders();
 if(view==='shipments') return renderList('/api/shipments',shipmentCols,'发货查询',hasPermission('shipments.manage')?()=>openForm('更新发货',forms.shipment):null);
 if(view==='gifts') return renderProducts('gift'); if(view==='products') return renderProducts('product');
 if(view==='privilegeCards') return renderPrivilegeCards();
 if(view==='cdk') return renderCDK();
 if(view==='rechargeRules') return renderList('/api/recharge-rules',ruleCols,'累充列表',hasPermission('recharge.manage')?()=>openForm('新增累充规则',forms.rule):null);
 if(view==='claims') return renderList('/api/claims',claimCols,'领取记录',hasPermission('claims.manage')?()=>openForm('新增领取记录',forms.claim):null);
 if(view==='sendMail') return renderSendMail(); if(view==='mailRecords') return renderList('/api/mails',mailCols,'发送记录');
 if(view==='profileSettings') return renderProfileSettings();
 if(view==='adminManagers') return renderAdminManagers();
 if(view==='systemEditor') return renderSystemEditor();
 if(view==='ipWhitelist') return renderIPWhitelist();
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
 return `<section class="registration-link-card dashboard-registration-card"><div class="registration-link-main"><strong>${title}</strong><div class="registration-link-row"><span>${note}</span><div class="registration-link-actions"><input value="${esc(url)}" readonly id="dashboardRegistrationUrl"><button class="btn primary" id="copyDashboardRegistration">复制地址</button></div></div></div></section>`;
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
function playerSearchQuery(){
 const p=new URLSearchParams();
 if(playerSearch.account)p.set('account',playerSearch.account);
 if(playerSearch.role)p.set('role',playerSearch.role);
 if(playerSearch.parent)p.set('parent',playerSearch.parent);
 const qs=p.toString();
 return qs?`?${qs}`:'';
}
function playerSearchBar(){
 return `<div class="player-search-bar">
   <div class="query-field"><label>账号查询</label><input id="playerAccountQuery" value="${esc(playerSearch.account)}" placeholder="输入玩家账号"></div>
   <div class="query-field"><label>角色查询</label><input id="playerRoleQuery" value="${esc(playerSearch.role)}" placeholder="输入角色名"></div>
   <div class="query-field"><label>上级代理查询</label><input id="playerParentQuery" value="${esc(playerSearch.parent)}" placeholder="代理ID/账号/名称"></div>
   <div class="query-actions"><button class="btn primary" id="playerQueryBtn">查询</button><button class="btn" id="playerResetBtn">重置</button></div>
 </div>`;
}
function bindPlayerSearch(){
 const run=()=>{playerSearch={account:$('#playerAccountQuery')?.value.trim()||'',role:$('#playerRoleQuery')?.value.trim()||'',parent:$('#playerParentQuery')?.value.trim()||''};renderPlayers()};
 $('#playerQueryBtn').onclick=run;
 $('#playerResetBtn').onclick=()=>{playerSearch={account:'',role:'',parent:''};renderPlayers()};
 ['#playerAccountQuery','#playerRoleQuery','#playerParentQuery'].forEach(sel=>{const el=$(sel);if(el)el.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();run()}})});
}
async function renderPlayers(){
 const rows=await api('/api/players'+playerSearchQuery());
 $('#content').innerHTML=panel('玩家列表',`${playerSearchBar()}<div class="table-scroll player-table-scroll ${hasPermission('players.manage')?'player-editable':''}">${table(rows,playerColumns())}</div>`);
 bindPlayerSearch();
}

function platformOrderSearchQuery(){
 const p=new URLSearchParams();
 Object.entries(platformOrderSearch).forEach(([k,v])=>{if(v)p.set(k,v)});
 const qs=p.toString();return qs?`?${qs}`:'';
}
function platformOrderSearchBar(){
 return `<div class="platform-order-search-bar">
   <div class="query-field"><label>订单号查询</label><input id="platformOrderNoQuery" value="${esc(platformOrderSearch.order_no)}" placeholder="输入订单号"></div>
   <div class="query-field"><label>账号查询</label><input id="platformAccountQuery" value="${esc(platformOrderSearch.account)}" placeholder="输入玩家账号"></div>
   <div class="query-field"><label>支付方式</label><select id="platformPaymentMethodQuery">
     <option value="" ${platformOrderSearch.payment_method===''?'selected':''}>全部</option>
     <option value="wechat" ${platformOrderSearch.payment_method==='wechat'?'selected':''}>微信</option>
     <option value="alipay" ${platformOrderSearch.payment_method==='alipay'?'selected':''}>支付宝</option>
   </select></div>
   <div class="query-field"><label>状态</label><select id="platformStatusQuery">
     <option value="" ${platformOrderSearch.status===''?'selected':''}>全部</option>
     <option value="unpaid" ${platformOrderSearch.status==='unpaid'?'selected':''}>未支付</option>
     <option value="paid" ${platformOrderSearch.status==='paid'?'selected':''}>已支付</option>
   </select></div>
   <div class="query-field platform-date-field"><label>开始日期</label><input id="platformStartDateQuery" type="date" value="${esc(platformOrderSearch.start_date)}"></div>
   <div class="query-field platform-date-field"><label>结束日期</label><input id="platformEndDateQuery" type="date" value="${esc(platformOrderSearch.end_date)}"></div>
   <div class="query-actions"><button class="btn primary" id="platformQueryBtn">查询</button><button class="btn" id="platformResetBtn">重置</button></div>
 </div>`;
}
function bindPlatformOrderSearch(){
 const run=()=>{
   platformOrderSearch={
     order_no:$('#platformOrderNoQuery')?.value.trim()||'',account:$('#platformAccountQuery')?.value.trim()||'',
     payment_method:$('#platformPaymentMethodQuery')?.value||'',status:$('#platformStatusQuery')?.value||'',
     start_date:$('#platformStartDateQuery')?.value||'',end_date:$('#platformEndDateQuery')?.value||''
   };renderPlatformOrders();
 };
 $('#platformQueryBtn').onclick=run;
 $('#platformResetBtn').onclick=()=>{platformOrderSearch={order_no:'',account:'',payment_method:'',status:'',start_date:'',end_date:''};renderPlatformOrders()};
 ['#platformOrderNoQuery','#platformAccountQuery'].forEach(sel=>{const el=$(sel);if(el)el.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();run()}})});
}
async function renderPlatformOrders(){
 const rows=await api('/api/orders/platform'+platformOrderSearchQuery());
 $('#content').innerHTML=panel('平台币订单',`${platformOrderSearchBar()}<div class="query-scope-note">订单由玩家充值/支付流程自动生成，后台不提供手工新增。默认显示全部订单并按创建时间从新到旧排序；日期仅在主动查询时生效。只有真实支付成功的订单进入流水；补发不会重复增加流水或分佣。</div><div class="table-scroll platform-order-table-scroll">${table(rows,platformCols)}</div>`);
 bindPlatformOrderSearch();
}
function mallOrderSearchQuery(){
 const p=new URLSearchParams();
 if(mallOrderSearch.account)p.set('account',mallOrderSearch.account);
 if(mallOrderSearch.product)p.set('product',mallOrderSearch.product);
 const qs=p.toString();return qs?`?${qs}`:'';
}
function mallOrderSearchBar(){
 return `<div class="mall-order-search-bar">
   <div class="query-field"><label>账号查询</label><input id="mallAccountQuery" value="${esc(mallOrderSearch.account)}" placeholder="输入玩家账号"></div>
   <div class="query-field"><label>商品查询</label><input id="mallProductQuery" value="${esc(mallOrderSearch.product)}" placeholder="输入礼包名称 / SKU"></div>
   <div class="query-actions"><button class="btn primary" id="mallQueryBtn">查询</button><button class="btn" id="mallResetBtn">重置</button></div>
 </div>`;
}
function bindMallOrderSearch(){
 const run=()=>{mallOrderSearch={account:$('#mallAccountQuery')?.value.trim()||'',product:$('#mallProductQuery')?.value.trim()||''};renderMallOrders()};
 $('#mallQueryBtn').onclick=run;
 $('#mallResetBtn').onclick=()=>{mallOrderSearch={account:'',product:''};renderMallOrders()};
 ['#mallAccountQuery','#mallProductQuery'].forEach(sel=>{const el=$(sel);if(el)el.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();run()}})});
}
async function renderMallOrders(){
 const rows=await api('/api/orders/mall'+mallOrderSearchQuery());
 $('#content').innerHTML=panel('商城订单',`${mallOrderSearchBar()}<div class="query-scope-note">商城订单仅由玩家中心使用平台币购买礼包自动生成。角色名与区服为购买瞬间的订单快照，玩家后续切换角色、改名或转服不会改写历史订单。</div><div class="table-scroll mall-order-table-scroll">${table(rows,mallCols)}</div>`);
 bindMallOrderSearch();
}

async function resendPlatformOrder(id){
 if(!hasPermission('orders.manage'))return;
 if(!confirm('确认补发该平台币订单？补发仅针对已支付但发货失败的订单。'))return;
 try{
   const result=await api(`/api/orders/platform/${Number(id)}/resend`,{method:'POST'});
   showToast(result.message||'补发成功','success',2600);
   await renderPlatformOrders();
 }catch(e){showToast(e.message,'error',4200)}
}
window.resendPlatformOrder=resendPlatformOrder;

function paymentTestPlayerOptions(){
  if(!paymentTestState.players.length)return '<option value="">请先搜索玩家</option>';
  return `<option value="">请选择玩家</option>${paymentTestState.players.map(p=>`<option value="${esc(p.username)}" ${paymentTestState.selectedAccount===p.username?'selected':''}>${esc(p.username)} ｜ ${esc(p.player_id)} ｜ ${esc(p.owner_agent_id)} ｜ 余额 ${Number(p.platform_coin_balance||0).toLocaleString()}</option>`).join('')}`;
}
function paymentTestOrderCard(){
  const o=paymentTestState.order;
  if(!o)return '<div class="payment-test-empty">尚未创建测试订单。先选择玩家并点击“模拟下单”。</div>';
  const paid=o.status==='paid';
  return `<div class="payment-test-result">
    <div><span>测试订单号</span><strong>${esc(o.order_no)}</strong></div>
    <div><span>当前状态</span><strong>${esc(o.status==='unpaid'?'未支付':'已支付')}</strong></div>
    <div><span>发货状态</span><strong>${esc(o.delivery_status==='success'?'成功':o.delivery_status==='failed'?'失败':'待处理')}</strong></div>
    <div class="payment-test-result-actions">
      <button class="btn primary" type="button" id="paymentTestPayBtn" ${paid?'disabled':''}>${paid?'已模拟支付':'模拟支付成功'}</button>
      <button class="btn" type="button" id="paymentTestViewOrdersBtn">查看平台币订单</button>
    </div>
  </div>`;
}
function paymentTestPage(){
 return `<div class="payment-test-warning"><strong>支付测试会写入真实业务数据库。</strong> 点击“模拟支付成功”后会真实增加玩家平台币余额、真实支付流水与代理佣金，但不会增加累计充值；累计充值只由网页商城消费增加。只用于测试账号/测试金额，正式运营数据不要使用此功能造单。</div>
 <div class="payment-test-grid">
   <div class="payment-test-card">
     <h4>1. 选择玩家</h4>
     <div class="payment-test-search"><input id="paymentTestPlayerKeyword" placeholder="输入玩家账号或玩家ID"><button type="button" class="btn" id="paymentTestSearchBtn">搜索玩家</button></div>
     <label>玩家账号</label><select id="paymentTestPlayerSelect">${paymentTestPlayerOptions()}</select>
   </div>
   <div class="payment-test-card">
     <h4>2. 填写充值内容</h4>
     <div class="payment-test-fields">
       <div><label>商品名称</label><input id="paymentTestProductName" value="测试平台币充值" maxlength="120"></div>
       <div><label>金额（元）</label><input id="paymentTestAmount" type="number" min="0.01" step="0.01" value="1"></div>
       <div><label>平台币数量</label><input id="paymentTestCoin" type="number" min="1" step="1" value="100"></div>
       <div><label>支付方式</label><select id="paymentTestMethod"><option value="wechat">微信</option><option value="alipay">支付宝</option></select></div>
     </div>
     <button class="btn primary payment-test-create" type="button" id="paymentTestCreateBtn">模拟下单</button>
   </div>
 </div>
 <div class="payment-test-card payment-test-order-card"><h4>3. 模拟支付结果</h4>${paymentTestOrderCard()}</div>`;
}
async function searchPaymentTestPlayers(){
  const keyword=$('#paymentTestPlayerKeyword')?.value.trim()||'';
  try{
    paymentTestState.players=await api('/api/payment-test/players?'+new URLSearchParams({keyword}).toString());
    if(paymentTestState.players.length===1)paymentTestState.selectedAccount=paymentTestState.players[0].username;
    const select=$('#paymentTestPlayerSelect');
    if(select)select.innerHTML=paymentTestPlayerOptions();
    if(!paymentTestState.players.length)showToast('没有找到可充值的正常玩家','error',3200);
  }catch(e){showToast(e.message,'error',4200)}
}
async function createPaymentTestOrder(){
  const account=$('#paymentTestPlayerSelect')?.value||'';
  const product_name=$('#paymentTestProductName')?.value.trim()||'';
  const amount=Number($('#paymentTestAmount')?.value||0);
  const platform_coin=Number($('#paymentTestCoin')?.value||0);
  const payment_method=$('#paymentTestMethod')?.value||'wechat';
  if(!account)return showToast('请先选择玩家','error',3000);
  if(!product_name)return showToast('请输入商品名称','error',3000);
  if(!(amount>0))return showToast('请输入大于 0 的充值金额','error',3000);
  if(!Number.isInteger(platform_coin)||platform_coin<=0)return showToast('平台币数量必须是大于 0 的整数','error',3000);
  const btn=$('#paymentTestCreateBtn');if(btn){btn.disabled=true;btn.textContent='正在下单...'}
  try{
    const result=await api('/api/payment-test/orders',{method:'POST',body:JSON.stringify({player_account:account,product_name,amount,platform_coin,payment_method})});
    paymentTestState.selectedAccount=account;
    paymentTestState.order={...result,delivery_status:'pending'};
    showToast(`测试订单 ${result.order_no} 已创建`,'success',2800);
    await renderPaymentTest(false);
  }catch(e){showToast(e.message,'error',4200);if(btn){btn.disabled=false;btn.textContent='模拟下单'}}
}
async function payPaymentTestOrder(){
  const o=paymentTestState.order;if(!o)return;
  if(!confirm(`确认模拟订单 ${o.order_no} 支付成功？\n\n该操作会真实增加玩家余额，并计入充值、流水和佣金统计。`))return;
  const btn=$('#paymentTestPayBtn');if(btn){btn.disabled=true;btn.textContent='正在模拟支付...'}
  try{
    const result=await api(`/api/payment-test/orders/${encodeURIComponent(o.order_no)}/pay`,{method:'POST'});
    paymentTestState.order={...o,...result};
    showToast(result.message||'模拟支付成功','success',3200);
    await renderPaymentTest(false);
  }catch(e){showToast(e.message,'error',4200);if(btn){btn.disabled=false;btn.textContent='模拟支付成功'}}
}
function bindPaymentTest(){
  $('#paymentTestSearchBtn').onclick=searchPaymentTestPlayers;
  $('#paymentTestPlayerKeyword').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();searchPaymentTestPlayers()}});
  $('#paymentTestPlayerSelect').onchange=e=>{paymentTestState.selectedAccount=e.target.value||''};
  $('#paymentTestCreateBtn').onclick=createPaymentTestOrder;
  const pay=$('#paymentTestPayBtn');if(pay)pay.onclick=payPaymentTestOrder;
  const view=$('#paymentTestViewOrdersBtn');if(view)view.onclick=()=>{currentView='platformOrders';syncNavToView(currentView);loadView(currentView)};
}
async function renderPaymentTest(loadPlayers=true){
  if(loadPlayers && !paymentTestState.players.length){
    paymentTestState.players=await api('/api/payment-test/players');
  }
  $('#content').innerHTML=panel('支付测试',paymentTestPage());
  bindPaymentTest();
}


function privilegeCardTypeText(v){return ({week:'周卡',month:'月卡',year:'年卡'})[v]||v||'-'}
function openPrivilegeCardForm(row=null){
 const cfg={...forms.privilege,defaults:row?{name:row.name,card_type:row.card_type,price_coins:row.price_coins,daily_reward_content:row.daily_reward_content,enabled:String(Boolean(row.enabled))}:{name:'',card_type:'week',price_coins:50,daily_reward_content:'',enabled:'true'}};
 if(row){cfg.path=`/api/privilege-cards/${Number(row.id)}`;cfg.method='PUT'}
 openForm(row?'编辑特权卡':'新增特权卡',cfg);
}
window.editPrivilegeCard=(id)=>{const row=(window.__privilegeRows||[]).find(x=>Number(x.id)===Number(id));if(row)openPrivilegeCardForm(row)};
async function renderPrivilegeCards(){
 const [rows,records,claims]=await Promise.all([api('/api/privilege-cards'),api('/api/privilege-card-records'),api('/api/privilege-card-claims')]);
 window.__privilegeRows=rows;
 const cols=[['名称','name'],['类型','card_type_name'],['有效天数','duration_days'],['平台币售价','price_coins'],['每日奖励','daily_reward_content'],['状态','enabled',v=>badge(v?'active':'disabled')],['操作','id',(v)=>`<button class="btn small" onclick="editPrivilegeCard(${Number(v)})">编辑</button>`]];
 const recordCols=[['玩家账号','player_account'],['区服','server_name'],['角色','role_name'],['特权卡','card_name'],['价格','price_coins'],['有效期','start_date',(v,r)=>`${esc(r.start_date)} 至 ${esc(r.end_date)}`],['状态','status',badge],['购买时间','created_at']];
 const claimColsLocal=[['玩家账号','player_account'],['区服','server_name'],['角色','role_name'],['特权卡','card_name'],['领取日期','claim_date'],['奖励内容','reward_content'],['领取时间','claimed_at']];
 $('#content').innerHTML=panel('特权卡配置',`${table(rows,cols)}<div class="section-gap"></div><h4>最近购买记录</h4>${table(records,recordCols)}<div class="section-gap"></div><h4>最近每日领取记录</h4>${table(claims,claimColsLocal)}`,'<button class="btn primary" id="addPrivilegeBtn">＋ 新增特权卡</button>');
 $('#addPrivilegeBtn').onclick=()=>openPrivilegeCardForm();
}

function behaviorCharacterOptions(){const rows=playerBehaviorTestState.characters||[];return `<option value="">请选择玩家角色 / 区服</option>${rows.map(x=>`<option value="${Number(x.character_id)}" ${Number(x.character_id)===Number(playerBehaviorTestState.selectedCharacterId)?'selected':''}>${esc(x.username)} ｜ ${esc(x.server_name)} ｜ ${esc(x.role_name)} ｜ 余额 ${Number(x.platform_coin_balance||0).toLocaleString()}</option>`).join('')}`}
async function loadBehaviorCumulative(){const id=Number(playerBehaviorTestState.selectedCharacterId||0);playerBehaviorTestState.cumulative=id?await api(`/api/player-behavior-test/cumulative?character_id=${id}`):{rules:[]}}
function playerBehaviorTestPage(){
 const s=playerBehaviorTestState,c=s.cumulative||{rules:[]},selected=s.characters.find(x=>Number(x.character_id)===Number(s.selectedCharacterId));
 return `<div class="payment-test-warning"><strong>这里执行的是真实业务链路。</strong> 礼包购买会真实扣平台币并生成商城订单、增加该角色累充；特权卡购买会真实扣平台币并生成特权卡记录；累充领取会真实写入领取记录。请只使用测试玩家/测试角色。</div>
 <div class="payment-test-card"><h4>1. 选择玩家角色</h4><div class="payment-test-search"><input id="behaviorKeyword" value="${esc(s.keyword)}" placeholder="玩家账号 / 玩家ID / 角色名 / 区服"><button type="button" class="btn" id="behaviorSearchBtn">搜索</button></div>${s.searchMessage?`<div class="query-scope-note behavior-search-message">${esc(s.searchMessage)}</div>`:''}<label>玩家角色 / 区服</label><select id="behaviorCharacterSelect">${behaviorCharacterOptions()}</select>${selected?`<div class="query-scope-note">当前：${esc(selected.username)} · ${esc(selected.server_name)} · ${esc(selected.role_name)} · 平台币余额 ${Number(selected.platform_coin_balance||0).toLocaleString()}</div>`:''}</div>
 <div class="payment-test-grid">
   <div class="payment-test-card"><h4>2A. 真实模拟购买礼包</h4><label>礼包</label><select id="behaviorGiftSelect"><option value="">请选择礼包</option>${s.gifts.map(x=>`<option value="${Number(x.id)}">${esc(x.name)} · ${Number(x.price).toLocaleString()} 平台币</option>`).join('')}</select><button class="btn primary payment-test-create" id="behaviorGiftBtn">购买礼包</button></div>
   <div class="payment-test-card"><h4>2B. 真实模拟购买特权卡</h4><label>特权卡</label><select id="behaviorCardSelect"><option value="">请选择特权卡</option>${s.cards.filter(x=>x.enabled).map(x=>`<option value="${Number(x.id)}">${esc(x.name)} · ${Number(x.price_coins).toLocaleString()} 平台币</option>`).join('')}</select><button class="btn primary payment-test-create" id="behaviorCardBtn">购买特权卡</button></div>
 </div>
 <div class="payment-test-card"><h4>2C. 真实模拟领取累充</h4>${s.selectedCharacterId?`<div class="query-scope-note">当日累充：${Number(c.today_recharge||0).toLocaleString()} 平台币 ｜ 永久累充：${Number(c.total_recharge||0).toLocaleString()} 平台币</div>`:'<div class="payment-test-empty">请先选择角色。</div>'}<label>当前可领取奖励</label><select id="behaviorRuleSelect"><option value="">请选择累充奖励</option>${(c.rules||[]).map(x=>`<option value="${Number(x.id)}">${esc(x.name)} · 门槛 ${Number(x.threshold_amount||0).toLocaleString()}</option>`).join('')}</select><button class="btn primary payment-test-create" id="behaviorClaimBtn">领取累充奖励</button></div>`;
}
async function searchBehaviorCharacters(){
 const keyword=$('#behaviorKeyword')?.value.trim()||'';
 playerBehaviorTestState.keyword=keyword;
 playerBehaviorTestState.searchMessage='';
 if(!keyword){
   playerBehaviorTestState.characters=[];playerBehaviorTestState.selectedCharacterId=0;playerBehaviorTestState.cumulative={rules:[]};
   playerBehaviorTestState.searchMessage='请输入玩家账号、玩家ID、角色名或区服后再搜索';
   await renderPlayerBehaviorTest(false);return;
 }
 const btn=$('#behaviorSearchBtn');if(btn)btn.disabled=true;
 try{
   const q=new URLSearchParams({keyword});
   const result=await api('/api/player-behavior-test/character-search?'+q.toString());
   playerBehaviorTestState.characters=Array.isArray(result.items)?result.items:[];
   if(!playerBehaviorTestState.characters.some(x=>Number(x.character_id)===Number(playerBehaviorTestState.selectedCharacterId)))playerBehaviorTestState.selectedCharacterId=0;
   playerBehaviorTestState.cumulative={rules:[]};
   if(playerBehaviorTestState.characters.length){
     playerBehaviorTestState.searchMessage=`找到 ${playerBehaviorTestState.characters.length} 个角色，请在下方选择`;
   }else if((result.unbound_players||[]).length){
     const names=result.unbound_players.slice(0,3).map(x=>x.username).join('、');
     playerBehaviorTestState.searchMessage=`已找到玩家 ${names}，但该玩家尚未绑定角色 / 区服`;
   }else{
     playerBehaviorTestState.searchMessage='未找到匹配的玩家角色';
   }
   await renderPlayerBehaviorTest(false);
 }finally{if(btn&&document.body.contains(btn))btn.disabled=false}
}
function bindPlayerBehaviorTest(){
 const searchBtn=$('#behaviorSearchBtn'),keywordInput=$('#behaviorKeyword');
 const runSearch=()=>searchBehaviorCharacters().catch(e=>showToast(e.message,'error',4200));
 if(searchBtn){searchBtn.disabled=false;searchBtn.addEventListener('click',e=>{e.preventDefault();e.stopPropagation();runSearch()})}
 if(keywordInput)keywordInput.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();runSearch()}});
 $('#behaviorCharacterSelect').onchange=async e=>{playerBehaviorTestState.selectedCharacterId=Number(e.target.value||0);try{await loadBehaviorCumulative();await renderPlayerBehaviorTest(false)}catch(err){showToast(err.message,'error',4200)}};
 $('#behaviorGiftBtn').onclick=async()=>{const character_id=Number(playerBehaviorTestState.selectedCharacterId||0),product_id=Number($('#behaviorGiftSelect').value||0);if(!character_id||!product_id)return showToast('请先选择角色和礼包','error',3000);if(!confirm('确认执行真实礼包购买测试？会真实扣除该玩家平台币。'))return;try{const d=await api('/api/player-behavior-test/mall-purchase',{method:'POST',body:JSON.stringify({character_id,product_id})});showToast(`${d.message}：${d.order_no}`,'success',3200);await searchBehaviorCharacters()}catch(e){showToast(e.message,'error',4200)}};
 $('#behaviorCardBtn').onclick=async()=>{const character_id=Number(playerBehaviorTestState.selectedCharacterId||0),card_id=Number($('#behaviorCardSelect').value||0);if(!character_id||!card_id)return showToast('请先选择角色和特权卡','error',3000);if(!confirm('确认执行真实特权卡购买测试？会真实扣除该玩家平台币。'))return;try{const d=await api('/api/player-behavior-test/privilege-purchase',{method:'POST',body:JSON.stringify({character_id,card_id})});showToast(d.message||'特权卡购买成功','success',3200);await searchBehaviorCharacters()}catch(e){showToast(e.message,'error',4200)}};
 $('#behaviorClaimBtn').onclick=async()=>{const character_id=Number(playerBehaviorTestState.selectedCharacterId||0),rule_id=Number($('#behaviorRuleSelect').value||0);if(!character_id||!rule_id)return showToast('请先选择角色和可领取累充奖励','error',3000);if(!confirm('确认执行真实累充领取测试？领取后不能再次领取同一档奖励。'))return;try{const d=await api('/api/player-behavior-test/cumulative-claim',{method:'POST',body:JSON.stringify({character_id,rule_id})});showToast(d.message||'领取成功','success',3000);await loadBehaviorCumulative();await renderPlayerBehaviorTest(false)}catch(e){showToast(e.message,'error',4200)}};
}
async function renderPlayerBehaviorTest(load=true){if(load){const [gifts,cards]=await Promise.all([api('/api/products?category=gift'),api('/api/privilege-cards')]);playerBehaviorTestState.characters=[];playerBehaviorTestState.selectedCharacterId=0;playerBehaviorTestState.keyword='';playerBehaviorTestState.searchMessage='请先搜索玩家，再选择具体角色 / 区服';playerBehaviorTestState.cumulative={rules:[]};playerBehaviorTestState.gifts=gifts;playerBehaviorTestState.cards=cards}$('#content').innerHTML=panel('玩家行为测试',playerBehaviorTestPage());bindPlayerBehaviorTest()}

async function renderProfileSettings(){
 const d=await api('/api/system/profile');
 $('#content').innerHTML=`<div class="settings-layout">
   <div class="panel settings-profile-card">
     <div class="panel-head"><h3>账号信息</h3></div>
     <div class="profile-info-grid">
       <div><span>登录账号</span><strong>${esc(d.username)}</strong></div>
       <div><span>账号角色</span><strong>${esc(d.role_name)}</strong></div>
       <div><span>账号状态</span><strong>${d.enabled?'正常':'停用'}</strong></div>
       <div><span>创建时间</span><strong>${esc(d.created_at||'-')}</strong></div>
     </div>
   </div>
   <div class="panel settings-password-card">
     <div class="panel-head"><h3>修改登录密码</h3></div>
     <div class="form-hint">修改的是当前登录后台账号的密码。新密码至少 8 位。</div>
     <form id="profilePasswordForm" autocomplete="off">
       <div class="settings-password-fields">
         <div><label>当前密码</label><input name="current_password" type="password" autocomplete="current-password" required></div>
         <div><label>新密码</label><input name="new_password" type="password" minlength="8" autocomplete="new-password" required></div>
         <div><label>确认新密码</label><input name="confirm_password" type="password" minlength="8" autocomplete="new-password" required></div>
       </div>
       <div class="settings-form-actions"><button class="btn primary" type="submit">保存新密码</button></div>
     </form>
   </div>
 </div>`;
 const form=$('#profilePasswordForm');
 form.onsubmit=async e=>{
   e.preventDefault();
   const btn=e.submitter||form.querySelector('button[type="submit"]');
   const fd=new FormData(form);
   const body=Object.fromEntries(fd.entries());
   if(body.new_password!==body.confirm_password)return showToast('两次输入的新密码不一致','error',3600);
   if(String(body.new_password||'').length<8)return showToast('新密码至少需要 8 位','error',3600);
   const oldText=btn.textContent;btn.disabled=true;btn.textContent='保存中…';
   try{
     const r=await api('/api/system/profile/password',{method:'PATCH',body:JSON.stringify(body)});
     form.reset();
     showToast(r.message||'密码修改成功','success',3000);
   }catch(err){showToast(err.message,'error',4200)}finally{btn.disabled=false;btn.textContent=oldText}
 };
}

const adminManagerCols=[
 ['ID','id'],
 ['管理员账号','username'],
 ['角色','role_name'],
 ['状态','enabled',(v)=>badge(v?'active':'disabled')],
 ['创建时间','created_at']
];
async function renderAdminManagers(){
 const rows=await api('/api/system/admins');
 $('#content').innerHTML=panel('管理员',`<div class="query-scope-note">这里创建的账号均为超级管理员，登录后拥有与当前超管相同的系统权限。</div><div class="table-scroll">${table(rows,adminManagerCols)}</div>`,'<button class="btn primary" id="addAdminBtn">＋ 新增超管</button>');
 $('#addAdminBtn').onclick=()=>openForm('新增超级管理员',forms.admin);
}

async function renderSystemEditor(){
 const d=await api('/api/system/branding');
 const icons=Array.isArray(d.available_icons)?d.available_icons:[];
 const currentLogo=String(d.backend_logo||'dragon-spiral');
 const iconCards=icons.map(icon=>`<button type="button" class="brand-icon-option ${icon.id===currentLogo?'selected':''}" data-logo-id="${esc(icon.id)}" title="${esc(icon.name)}">
   <span class="brand-icon-option-art" style="--brand-logo-url:url('${esc(icon.path)}')"></span>
   <span>${esc(icon.name)}</span>
 </button>`).join('');
 $('#content').innerHTML=`<div class="settings-layout settings-editor-layout">
   <div class="panel settings-password-card settings-branding-card">
     <div class="panel-head"><h3>系统品牌</h3></div>
     <div class="form-hint">后台名称、玩家中心名称和左侧顶部图标都可以在这里修改。图标已内置到源码，不依赖外部网站。仅超级管理员可以编辑。</div>
     <form id="systemBrandingForm" autocomplete="off">
       <div class="settings-password-fields settings-branding-fields">
         <div><label>后台名称</label><input name="backend_name" maxlength="40" value="${esc(d.backend_name||'CPS')}" required placeholder="例如 天龙八部CPS后台"></div>
         <div><label>玩家中心名称</label><input name="player_center_name" maxlength="40" value="${esc(d.player_center_name||'玩家中心')}" required placeholder="例如 玩家中心"></div>
       </div>
       <div class="brand-icon-field">
         <label>后台图标</label>
         <input type="hidden" name="backend_logo" id="backendLogoInput" value="${esc(currentLogo)}">
         <div class="brand-icon-picker">${iconCards||'<div class="empty">暂无可用图标</div>'}</div>
         <div class="brand-icon-help">点击图标即可选择，保存后左侧顶部立即切换。</div>
       </div>
       <div class="settings-form-actions"><button class="btn primary" type="submit">保存系统设置</button></div>
     </form>
   </div>
 </div>`;
 const form=$('#systemBrandingForm');
 document.querySelectorAll('.brand-icon-option').forEach(btn=>btn.onclick=()=>{
   document.querySelectorAll('.brand-icon-option').forEach(x=>x.classList.remove('selected'));
   btn.classList.add('selected');
   $('#backendLogoInput').value=btn.dataset.logoId||'dragon-spiral';
 });
 form.onsubmit=async e=>{
   e.preventDefault();
   const btn=e.submitter||form.querySelector('button[type="submit"]');
   const body=Object.fromEntries(new FormData(form).entries());
   body.backend_name=String(body.backend_name||'').trim();
   body.player_center_name=String(body.player_center_name||'').trim();
   body.backend_logo=String(body.backend_logo||'dragon-spiral').trim();
   if(!body.backend_name)return showToast('后台名称不能为空','error',3200);
   if(!body.player_center_name)return showToast('玩家中心名称不能为空','error',3200);
   const oldText=btn.textContent;btn.disabled=true;btn.textContent='保存中…';
   try{
     const r=await api('/api/system/branding',{method:'PATCH',body:JSON.stringify(body)});
     applySystemBranding(r);
     showToast(r.message||'系统品牌保存成功','success',3000);
   }catch(err){showToast(err.message,'error',4200)}finally{btn.disabled=false;btn.textContent=oldText}
 };
}

function ipWhitelistRows(rows=[]){
 if(!rows.length)return '<div class="empty">尚未添加白名单IP。添加第一条后，后台IP访问限制立即启用。</div>';
 return `<div class="table-scroll"><table><thead><tr><th>IP地址</th><th>备注</th><th>添加人</th><th>添加时间</th><th>操作</th></tr></thead><tbody>${rows.map(r=>`<tr><td><code class="ip-code">${esc(r.ip_address)}${r.is_current?' <span class="badge ok">当前IP</span>':''}</code></td><td>${esc(r.note||'-')}</td><td>${esc(r.created_by||'-')}</td><td>${esc(r.created_at||'-')}</td><td><button class="btn danger ghost ip-remove-btn" data-id="${r.id}" type="button">删除</button></td></tr>`).join('')}</tbody></table></div>`;
}
function ipBlacklistRows(rows=[]){
 if(!rows.length)return '<div class="empty">当前没有被拉黑的后台登录IP</div>';
 return `<div class="table-scroll"><table><thead><tr><th>IP地址</th><th>失败次数</th><th>拉黑原因</th><th>最后失败</th><th>拉黑时间</th><th>操作</th></tr></thead><tbody>${rows.map(r=>`<tr><td><code class="ip-code">${esc(r.ip_address)}</code></td><td>${esc(r.failure_count)}</td><td>${esc(r.reason||'-')}</td><td>${esc(r.last_failed_at||'-')}</td><td>${esc(r.blocked_at||'-')}</td><td><button class="btn primary ip-unblock-btn" data-id="${r.id}" type="button">解除拉黑</button></td></tr>`).join('')}</tbody></table></div>`;
}
async function renderIPWhitelist(){
 const d=await api('/api/system/ip-access');
 const status=d.whitelist_enabled
   ? '<span class="badge ok">已启用</span> 仅白名单中的IP可以打开后台'
   : '<span class="badge warn">待启用</span> 当前尚无白名单；添加第一条后立即启用IP限制';
 $('#content').innerHTML=`<div class="settings-ip-layout">
   <div class="panel">
     <div class="panel-head"><h3>后台IP白名单</h3></div>
     <div class="ip-security-summary">
       <div><span>白名单状态</span><strong>${status}</strong></div>
       <div><span>当前访问IP</span><strong><code class="ip-code">${esc(d.current_ip||'无法识别')}</code></strong></div>
     </div>
     <div class="form-hint ip-warning">安全提示：一旦添加第一条白名单，其他IP将无法打开后台。最后一条白名单不能直接删除，需要先添加替代IP。</div>
     <form id="ipWhitelistForm" class="ip-add-form" autocomplete="off">
       <div><label>IP地址</label><input id="whitelistIpInput" name="ip_address" maxlength="64" required placeholder="例如 203.0.113.10"></div>
       <div><label>备注</label><input name="note" maxlength="120" placeholder="例如 公司办公室 / 家里"></div>
       <div class="ip-add-actions"><button class="btn" id="useCurrentIpBtn" type="button">填入当前IP</button><button class="btn primary" type="submit">添加白名单</button></div>
     </form>
     <div id="ipWhitelistTable">${ipWhitelistRows(d.whitelist)}</div>
   </div>
   <div class="panel">
     <div class="panel-head"><h3>登录拉黑名单</h3></div>
     <div class="form-hint">同一IP在 ${esc(d.login_failure_window_minutes)} 分钟内连续登录失败达到 ${esc(d.login_failure_limit)} 次会自动拉黑。拉黑不会自动过期，需要超级管理员手动解除。</div>
     <div id="ipBlacklistTable">${ipBlacklistRows(d.blacklist)}</div>
   </div>
 </div>`;
 const currentIp=String(d.current_ip||'');
 $('#useCurrentIpBtn').onclick=()=>{if(!currentIp)return showToast('当前IP无法识别','error',3200);$('#whitelistIpInput').value=currentIp};
 const form=$('#ipWhitelistForm');
 form.onsubmit=async e=>{
   e.preventDefault();
   const body=Object.fromEntries(new FormData(form).entries());
   body.ip_address=String(body.ip_address||'').trim();body.note=String(body.note||'').trim();
   if(!body.ip_address)return showToast('请输入IP地址','error',3200);
   if(!d.whitelist_enabled && currentIp && body.ip_address!==currentIp && !confirm(`这是第一条白名单，但你填写的IP不是当前访问IP（当前：${currentIp}）。保存后当前IP可能立即无法继续访问后台。确认添加吗？`))return;
   const btn=e.submitter||form.querySelector('button[type="submit"]'),old=btn.textContent;btn.disabled=true;btn.textContent='添加中…';
   try{const r=await api('/api/system/ip-access/whitelist',{method:'POST',body:JSON.stringify(body)});showToast(r.message||'白名单添加成功','success',3200);await renderIPWhitelist()}catch(err){showToast(err.message,'error',4500)}finally{btn.disabled=false;btn.textContent=old}
 };
 document.querySelectorAll('.ip-remove-btn').forEach(btn=>btn.onclick=async()=>{
   if(!confirm('确认删除这个白名单IP？删除后该IP将无法访问后台。'))return;
   try{const r=await api(`/api/system/ip-access/whitelist/${btn.dataset.id}`,{method:'DELETE'});showToast(r.message||'白名单已删除','success',2800);await renderIPWhitelist()}catch(err){showToast(err.message,'error',4500)}
 });
 document.querySelectorAll('.ip-unblock-btn').forEach(btn=>btn.onclick=async()=>{
   if(!confirm('确认解除这个IP的登录拉黑？解除后如果它同时在白名单中，将可以再次访问后台。'))return;
   try{const r=await api(`/api/system/ip-access/blacklist/${btn.dataset.id}`,{method:'DELETE'});showToast(r.message||'已解除拉黑','success',2800);await renderIPWhitelist()}catch(err){showToast(err.message,'error',4500)}
 });
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

function allowedSettlementLevelValues(){
 if(currentUser?.actor_type==='admin')return ['1','2','3'];
 if(Number(currentUser?.agent_level)===1)return ['2','3'];
 if(Number(currentUser?.agent_level)===2)return ['3'];
 return [];
}
function settlementSearchQuery(){
 const p=new URLSearchParams();
 if(settlementSearch.account)p.set('account',settlementSearch.account);
 if(settlementSearch.public_agent_id)p.set('public_agent_id',settlementSearch.public_agent_id);
 const selectedLevel=String(settlementSearch.agent_level||'');
 if(selectedLevel&&allowedSettlementLevelValues().includes(selectedLevel))p.set('agent_level',selectedLevel);
 if(settlementSearch.start_date)p.set('start_date',settlementSearch.start_date);
 if(settlementSearch.end_date)p.set('end_date',settlementSearch.end_date);
 const qs=p.toString();
 return qs?`?${qs}`:'';
}
function settlementLevelOptions(){
 const level=String(settlementSearch.agent_level||'');
 const labels={'1':'一级代理','2':'二级代理','3':'三级代理'};
 const options=[{value:'',label:'不限等级'},...allowedSettlementLevelValues().map(value=>({value,label:labels[value]}))];
 return options.map(o=>`<option value="${o.value}" ${level===o.value?'selected':''}>${o.label}</option>`).join('');
}
function settlementSearchBar(){
 return `<div class="settlement-search-bar">
   <div class="query-field query-account"><label>账号查询</label><input id="settlementAccountQuery" value="${esc(settlementSearch.account)}" placeholder="代理登录账号"></div>
   <div class="query-field query-agent-id"><label>ID查询</label><input id="settlementIdQuery" value="${esc(settlementSearch.public_agent_id)}" placeholder="例如 A1"></div>
   <div class="query-field query-level"><label>等级查询</label><select id="settlementLevelQuery">${settlementLevelOptions()}</select></div>
   <div class="query-field query-date-range"><label>日期选择</label><div class="settlement-date-range"><input id="settlementStartDateQuery" type="date" value="${esc(settlementSearch.start_date||'')}" aria-label="开始日期"><span>至</span><input id="settlementEndDateQuery" type="date" value="${esc(settlementSearch.end_date||'')}" aria-label="结束日期"></div></div>
   <div class="query-actions"><button class="btn primary" id="settlementQueryBtn">查询</button><button class="btn" id="settlementResetBtn">重置</button></div>
 </div>`;
}
function readSettlementSearch(){
 return {
   account:$('#settlementAccountQuery')?.value.trim()||'',
   public_agent_id:$('#settlementIdQuery')?.value.trim()||'',
   agent_level:$('#settlementLevelQuery')?.value||'',
   start_date:$('#settlementStartDateQuery')?.value||'',
   end_date:$('#settlementEndDateQuery')?.value||''
 };
}
function settlementColumns(data){
 const turnoverTitle=data?.period_type==='day'?'当日流水':data?.period_type==='range'?'区间流水':'总流水';
 return [
   ['代理ID','agent_id'],
   ['代理账号','username'],
   ['代理等级','agent_level',agentLevelText],
   ['代理名称','agent_name'],
   [turnoverTitle,'turnover',v=>`¥ ${Number(v||0).toFixed(2)}`],
   ['佣金比例','commission_rate',percent],
   ['日期','period_label']
 ];
}
async function renderSettlements(){
 const data=await api('/api/channel-settlements'+settlementSearchQuery());
 const rows=Array.isArray(data.rows)?data.rows:[];
 const scopeNote=currentUser?.actor_type==='admin'
   ? '超级管理员查看全部一级、二级、三级代理；默认显示历史总流水，选择开始/结束日期后显示所选日期范围流水，并按流水从高到低排列。'
   : '当前账号仅查看自己代理树中的下级代理，不包含自己；默认显示历史总流水，选择开始/结束日期后显示所选日期范围流水，并按流水从高到低排列。';
 $('#content').innerHTML=panel('渠道结算',`${settlementSearchBar()}<div class="query-scope-note">${scopeNote} 流水仅统计真实支付成功的平台币订单。</div><div class="table-scroll settlement-table-scroll">${table(rows,settlementColumns(data))}</div>`);
 const run=async()=>{settlementSearch=readSettlementSearch();await renderSettlements();};
 $('#settlementQueryBtn').onclick=run;
 $('#settlementResetBtn').onclick=async()=>{settlementSearch={account:'',public_agent_id:'',agent_level:'',start_date:'',end_date:''};await renderSettlements();};
 ['settlementAccountQuery','settlementIdQuery'].forEach(id=>{const el=$('#'+id);if(el)el.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();run()}})});
 ['settlementStartDateQuery','settlementEndDateQuery'].forEach(id=>{
   const dateInput=$('#'+id);
   if(dateInput)dateInput.addEventListener('click',()=>{try{dateInput.showPicker?.()}catch{}});
 });
}

function statusText(v){return String(v)==='disabled'?'封禁':'正常'}
function agentStatusBadge(v){const disabled=String(v)==='disabled';return `<span class="badge ${disabled?'bad':'ok'}">${disabled?'封禁':'正常'}</span>`}
window.openAgentEdit=async(agentPk)=>{
  try{
    const data=await api(`/api/agents/${agentPk}/edit-options`);
    const row=data.agent;
    const isThird=Number(row.agent_level)===3;
    const fullEdit=Boolean(data.can_full_edit);
    const currentParentDisplay=row.parent_agent_display||'超管';
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
        fields.push(['parent_agent_id','更改归属','search-select',false,{options:data.parent_options||[],valueType:'string',emptyLabel:'保持当前归属（不修改）',searchPlaceholder:'搜索代理ID / 账号 / 名称'}]);
      }
    }
    const note=fullEdit
      ? `代理ID：${row.agent_id} ｜ 代理等级：${agentLevelText(row.agent_level)} ｜ 当前归属：${currentParentDisplay}。超管可修改完整代理资料；修改密码留空、归属保持默认均表示不修改。`
      : `代理ID：${row.agent_id} ｜ 代理等级：${agentLevelText(row.agent_level)}。当前账号仅可修改直属下级的代理名称和佣金比例。`;
    const defaults={
      agent_name:row.agent_name||'',
      commission_rate:Number(row.commission_rate||0)*100
    };
    if(fullEdit){
      Object.assign(defaults,{
        password:'',status:row.status||'active',subagent_limit:isThird?0:Number(row.subagent_limit||0),parent_agent_id:''
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
          if(data.can_change_parent&&obj.parent_agent_id)out.parent_agent_id=obj.parent_agent_id;
        }
        return out;
      }
    });
  }catch(e){alert(e.message)}
};

window.openPlayerEdit=async(playerPk)=>{
 if(!hasPermission('players.manage'))return;
 try{
   const data=await api(`/api/players/${playerPk}/edit`);
   openForm(`编辑玩家 · ${data.player_id}`,{
     path:`/api/players/${playerPk}`,
     method:'PATCH',
     pendingText:'保存中…',
     note:`玩家账号：${data.username} ｜ 当前归属：${data.owner_display||'超管'} ｜ 当前平台币余额：${Number(data.platform_coin_balance||0).toLocaleString()}。修改密码留空、归属保持默认、平台币操作留空均表示不修改。手工发放/收回仅调整余额，不计真实流水、代理分佣或累计充值。`,
     defaults:{password:'',status:data.status||'active',owner_agent_id:'',coin_action:'',coin_amount:''},
     fields:[
       ['password','修改密码','password',false,{autocomplete:'new-password',placeholder:'留空则不修改；至少 8 位'}],
       ['owner_agent_id','修改归属','search-select',false,{options:data.owner_options||[],valueType:'string',emptyLabel:'保持当前归属（不修改）',searchPlaceholder:'搜索代理ID / 账号 / 名称'}],
       ['status','账号状态','select',true,{options:[{value:'active',label:'正常'},{value:'disabled',label:'封禁'}]}],
       ['coin_action','平台币操作','select',false,{options:[{value:'',label:'不操作'},{value:'issue',label:'手工补偿/发放平台币'},{value:'reclaim',label:'收回平台币'}]}],
       ['coin_amount','平台币数量','number',false,{min:1,max:2000000000,step:1,placeholder:'仅调整余额，不计流水/分佣/累计充值'}]
     ],
     transform:obj=>{
       const out={status:obj.status};
       if(obj.owner_agent_id)out.owner_agent_id=obj.owner_agent_id;
       if(obj.password)out.password=obj.password;
       if(obj.coin_action){
         const amount=Number(obj.coin_amount||0);
         if(!Number.isInteger(amount)||amount<=0)throw new Error('发放/收回平台币时，请填写大于 0 的整数数量');
         out.coin_action=obj.coin_action;out.coin_amount=amount;
       }
       return out;
     }
   });
 }catch(e){showToast(e.message,'error',4200)}
};

async function renderProducts(cat){const rows=await api('/api/products?category='+cat);const manage=hasPermission('products.manage');const cols=cat==='gift'?productCols.map(c=>c[1]==='price'?['平台币售价','price']:c[1]==='description'?['礼包内容','description']:c):productCols;$('#content').innerHTML=panel(cat==='gift'?'礼包列表':'商品列表',table(rows,cols),manage?'<button class="btn primary" id="addBtn">＋ 新增</button>':'');if(manage)$('#addBtn').onclick=()=>{const cfg={...forms.product,defaults:{category:cat}};if(cat==='gift')cfg.fields=forms.product.fields.map(f=>f[0]==='price'?['price','平台币售价','number',true,{min:1,step:1,placeholder:'请输入整数平台币价格'}]:f[0]==='description'?['description','礼包内容','textarea',false,{placeholder:'每行填写一个道具，例如：元宝 × 1000'}]:f);openForm(cat==='gift'?'新增礼包':'新增商品',cfg)}}
async function renderCDK(){const rows=await api('/api/redemption-batches');const manage=hasPermission('cdk.manage');$('#content').innerHTML=panel('兑换码批次',table(rows,cdkCols),manage?'<button class="btn primary" id="addBtn">＋ 新建批次</button> <button class="btn" id="genBtn">生成CDK</button>':'');if(manage){$('#addBtn').onclick=()=>openForm('新建CDK批次',forms.cdk);$('#genBtn').onclick=()=>openForm('生成兑换码',forms.generateCDK)}}
function renderSendMail(){$('#content').innerHTML=panel('发送游戏邮件','<p style="color:#7c879d">当前第一版会完整记录发送任务；接入你的游戏服邮件 API 后即可改为真实投递。</p><button class="btn primary" id="mailBtn">发送邮件</button>');$('#mailBtn').onclick=()=>openForm('发送邮件',forms.mail)}

function playerStatusBadge(v){return v==='active'?'<span class="badge ok">正常</span>':'<span class="badge bad">封禁</span>'}
function playerCharactersCell(characters){
 const roles=Array.isArray(characters)?characters:[];
 if(!roles.length)return '<span class="muted">未绑定</span>';
 const primary=roles.find(x=>x?.is_primary)||roles[0];
 const items=roles.map((item,index)=>{
   const role=esc(item?.role_name||'未命名角色');
   const server=esc(item?.server_name||'未知区服');
   const primaryTag=item?.is_primary?'<span class="role-primary-tag">主角色</span>':'';
   return `<div class="player-role-option ${index===0?'first':''}"><div class="player-role-option-name">${role}${primaryTag}</div><div class="player-role-option-server">${server}</div></div>`;
 }).join('');
 return `<details class="player-role-dropdown"><summary><span class="player-role-current">${esc(primary?.role_name||'未命名角色')}</span><span class="player-role-chevron" aria-hidden="true">⌄</span></summary><div class="player-role-menu">${items}</div></details>`;
}
const agentCols=[
 ['代理ID','agent_id'],
 ['代理等级','agent_level',agentLevelText],
 ['代理名称','agent_name'],
 ['账号','username'],
 ['邀请码','invite_code'],
 ['注册人数','registered_count'],
 ['佣金比例','commission_rate',percent],
 ['创建时间(北京时间)','created_at'],
 ['最近登录(北京时间)','last_login_at'],
 ['状态','status',agentStatusBadge],
 ['操作','id',(_,r)=>{
   const copy=`<button class="btn compact" onclick="copyRegistrationLink('${esc(r.agent_id)}')">复制注册地址</button>`;
   const edit=(hasPermission('channels.edit_basic')||hasPermission('channels.edit_full'))?`<button class="btn compact" onclick="openAgentEdit(${Number(r.id)})">编辑</button>`:'';
   return `<div class="table-action-buttons">${copy}${edit}</div>`;
 }]
];
const playerBaseCols=[['玩家ID','player_id'],['账号','username'],['角色','characters',playerCharactersCell],['所属代理','agent_public_id'],['平台币余额','platform_coin_balance'],['今日充值','today_recharge'],['累计充值','total_recharge'],['状态','status',playerStatusBadge],['注册时间(北京时间)','created_at'],['最后登录(北京时间)','last_login_at'],['登录IP','last_login_ip']];
function playerColumns(){return hasPermission('players.manage')?[...playerBaseCols,['操作','id',(_,r)=>`<button class="btn compact" onclick="openPlayerEdit(${Number(r.id)})">编辑</button>`]]:playerBaseCols}
function platformPaymentMethodText(v){return ({wechat:'微信',alipay:'支付宝'})[String(v||'').toLowerCase()]||'历史/未知'}
function platformOrderStatusBadge(v){
 const map={unpaid:['未支付','warn'],paid:['已支付','ok']};
 const [label,tone]=map[String(v||'')]||[String(v||'-'),''];
 return `<span class="badge ${tone}">${esc(label)}</span>`;
}
function platformDeliveryBadge(v,r){
 const map={success:['成功','ok'],failed:['失败','bad']};
 if(!map[String(v||'')])return '<span class="muted">-</span>';
 const [label,tone]=map[String(v)];
 const tip=esc((r&&r.delivery_message)||'');
 return `<span class="badge ${tone}" title="${tip}">${label}</span>`;
}
function platformResendCell(_,r){
 if(!hasPermission('orders.manage'))return '<span class="muted">-</span>';
 const canResend=r.status==='paid' && r.delivery_status==='failed';
 return `<button class="btn compact" ${canResend?'':'disabled'} onclick="resendPlatformOrder(${Number(r.id)})">补发</button>`;
}
const platformCols=[
 ['订单号','order_no'],['玩家账号','player_account'],['商品名称','product_name'],['金额（元）','amount'],
 ['支付方式','payment_method',platformPaymentMethodText],['状态','status',platformOrderStatusBadge],['发货','delivery_status',platformDeliveryBadge],
 ['创建时间','created_at'],['支付时间','paid_at'],['操作','id',platformResendCell]
];
const mallCols=[['订单号','order_no'],['玩家账号','player_account'],['角色名','role_name'],['区服','server_name'],['礼包名称','product_name'],['数量','quantity'],['平台币','coin_amount',v=>Number(v||0).toLocaleString()],['支付','pay_status',v=>v==='paid'?'<span class="badge ok">已支付</span>':badge(v)],['发货','delivery_status',v=>badge(({waiting:'待发货',sent:'已发货',success:'成功',failed:'失败'})[v]||v)],['创建时间','created_at']];
const shipmentCols=[['订单号','order_no'],['订单PK','mall_order_id'],['发货状态','delivery_status',badge],['服务商','provider'],['发货单号','tracking_no'],['任务状态','shipment_status',badge],['说明','message'],['发货时间','sent_at']];
const productCols=[['SKU','sku'],['名称','name'],['分类','category'],['价格','price'],['库存','stock'],['状态','enabled',v=>badge(v?'active':'disabled')],['说明','description']];
const cdkCols=[['CDK名称','name'],['总数','total_count'],['未兑换数','unused_count'],['已兑换数','redeemed_count'],['状态','enabled',v=>badge(v?'active':'disabled')],['创建时间','created_at']];
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
 admin:{path:'/api/system/admins',pendingText:'创建中…',note:'新建账号将直接获得超级管理员权限。账号至少 4 位，密码至少 8 位。',fields:[['username','管理员账号','text',true,{autocomplete:'off',placeholder:'请输入管理员账号',minLength:4}],['password','登录密码','password',true,{autocomplete:'new-password',placeholder:'至少 8 位',minLength:8}]]},
 mall:{path:'/api/orders/mall',fields:[['order_no','订单号'],['player_id','玩家PK','number'],['agent_id','代理PK','number',false],['product_id','商品PK','number'],['quantity','数量','number'],['amount','金额','number'],['pay_status','支付状态']]},
 shipment:{path:'/api/shipments',fields:[['mall_order_id','商城订单PK','number'],['provider','发货服务商'],['tracking_no','发货单号','text',false],['status','状态(sent/failed/success)'],['message','说明','textarea',false]]},
 product:{path:'/api/products',fields:[['sku','SKU'],['name','名称'],['category','分类(gift/product)'],['price','价格','number'],['stock','库存','number'],['description','说明','textarea',false]]},
 cdk:{path:'/api/redemption-batches',fields:[['name','CDK名称']]},
 generateCDK:{path:null,fields:[['batch_id','CDK批次PK','number'],['count','生成数量','number'],['prefix','前缀']]},
 settlement:{path:'/api/settlements',fields:[['agent_id','代理PK','number'],['period_start','开始日期','date'],['period_end','结束日期','date']]},
 rule:{path:'/api/recharge-rules',fields:[['name','规则名称'],['threshold_amount','累充门槛','number'],['reward_content','奖励内容','textarea']]},
 privilege:{path:'/api/privilege-cards',fields:[['name','特权卡名称'],['card_type','类型','select',true,{options:[{value:'week',label:'周卡（7天）'},{value:'month',label:'月卡（30天）'},{value:'year',label:'年卡（365天）'}]}],['price_coins','平台币售价','number',true,{min:1,step:1}],['daily_reward_content','每日奖励内容','textarea'],['enabled','状态','select',true,{options:[{value:'true',label:'启用'},{value:'false',label:'停用'}]}]],transform:o=>({...o,enabled:String(o.enabled)!=='false'})},
 claim:{path:'/api/claims',fields:[['player_id','玩家PK','number'],['rule_id','规则PK','number']]},
 mail:{path:'/api/mails',fields:[['title','邮件标题'],['content','邮件内容','textarea'],['target_type','目标类型(player/server/all)'],['target_value','玩家ID/区服，可空','text',false]]}
};
function inputAttrs(meta,type){
 const attrs=[];
 if(type==='number') attrs.push(`step="${meta?.step??'any'}"`);
 if(meta?.min!==undefined)attrs.push(`min="${meta.min}"`);
 if(meta?.max!==undefined)attrs.push(`max="${meta.max}"`);
 if(meta?.minLength!==undefined)attrs.push(`minlength="${meta.minLength}"`);
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
 if(type==='search-select'){
   const emptyLabel=meta?.emptyLabel||'保持当前归属（不修改）';
   const rawOptions=meta?.options||[];
   const options=[{value:'',label:emptyLabel},...rawOptions.filter(o=>String(o.value)!=='')]
     .map((o,index)=>`<option value="${esc(o.value)}" ${String(o.value)===String(val)?'selected':''} ${o.disabled?'disabled':''} data-noop="${index===0?'1':'0'}">${esc(o.label)}</option>`).join('');
   return `<div class="search-select-control" data-search-select><input type="search" class="search-select-input" placeholder="${esc(meta?.searchPlaceholder||'搜索代理ID / 账号 / 名称')}" autocomplete="off" aria-label="搜索可选代理"><select name="${name}">${options}</select><div class="search-select-help">默认不修改归属；输入关键词可筛选代理</div></div>`;
 }
 return `<input name="${name}" type="${type}" value="${esc(val)}" ${required?'required':''} ${inputAttrs(meta,type)}/>`;
}
function bindSearchSelects(root){
  root.querySelectorAll('[data-search-select]').forEach(wrap=>{
    const input=wrap.querySelector('.search-select-input');
    const select=wrap.querySelector('select');
    if(!input||!select)return;
    const all=[...select.options].map((o,index)=>({value:o.value,label:o.textContent||'',disabled:o.disabled,noop:index===0||o.dataset.noop==='1'}));
    const rebuild=()=>{
      const q=input.value.trim().toLowerCase();
      const selected=select.value;
      const visible=all.filter(o=>o.noop||!q||`${o.value} ${o.label}`.toLowerCase().includes(q));
      select.innerHTML=visible.map(o=>`<option value="${esc(o.value)}" ${o.disabled?'disabled':''}>${esc(o.label)}</option>`).join('');
      if(visible.some(o=>String(o.value)===String(selected)))select.value=selected;else select.value='';
      wrap.classList.toggle('search-select-filtering',Boolean(q));
    };
    input.addEventListener('input',rebuild);
    input.addEventListener('keydown',e=>{if(e.key==='ArrowDown'){e.preventDefault();select.focus();}});
  });
}
function openForm(title,cfg){
  $('#modalTitle').textContent=title;
  const defaults=cfg.defaults||{};
  const form=$('#modalForm');
  form.setAttribute('autocomplete','off');
  form.innerHTML=`${cfg.note?`<div class="form-hint">${esc(cfg.note)}</div>`:''}<div class="form-grid">${cfg.fields.map(f=>{const [name,label,type='text',required=true,meta=null]=f;const val=defaults[name]??'';return `<div class="${type==='textarea'||type==='search-select'?'full':''}"><label>${label}</label>${fieldControl(name,type,val,required,meta)}</div>`}).join('')}<div class="form-actions"><button type="button" class="btn" id="cancelForm">取消</button><button class="btn primary form-submit-btn">提交</button></div></div>`;
  $('#modal').classList.remove('hidden');
  bindSearchSelects(form);
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
