const $ = s => document.querySelector(s);
let token = localStorage.getItem('cps_token') || '';
let actorType = localStorage.getItem('cps_actor_type') || 'admin';
let currentView = actorType === 'agent' ? 'agents' : 'dashboard';
let currentUser = null;
let agentSearch = {agent_account:'', public_agent_id:'', parent:''};

const titles = {
 dashboard:['数据总览','CPS 运营核心指标'], agents:['下级渠道','管理当前账号直属下级渠道'], settlements:['渠道结算','按周期计算代理佣金'],
 players:['玩家列表','账号、角色、区服与充值数据'], platformOrders:['平台币订单','平台币充值订单记录'], mallOrders:['商城订单','商城购买订单记录'],
 shipments:['发货查询','商城订单发货状态'], gifts:['礼包列表','礼包类商品'], products:['商品列表','普通商城商品'], cdk:['兑换码列表','CDK 批次与兑换统计'],
 rechargeRules:['累充列表','累计充值奖励规则'], claims:['领取记录','玩家累充奖励领取情况'], sendMail:['发送邮件','向玩家或区服发送游戏邮件'], mailRecords:['发送记录','历史邮件发送记录']
};

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
function logout(){ token='';actorType='admin';currentUser=null;localStorage.removeItem('cps_token');localStorage.removeItem('cps_actor_type');$('#app').classList.add('hidden');$('#login').classList.remove('hidden'); }
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

async function loadView(view){if(!canView(view)){currentView=firstAllowedView();return loadView(currentView)}const [t,s]=titles[view]||['',''];$('#pageTitle').textContent=t;$('#pageSub').textContent=s;$('#content').innerHTML='<div class="panel"><div class="empty">加载中...</div></div>';try{
 if(view==='dashboard') return renderDashboard();
 if(view==='agents') return renderAgents();
 if(view==='settlements') return renderList('/api/settlements',settleCols,'结算记录',hasPermission('settlements.manage')?()=>openForm('生成结算单',forms.settlement):null);
 if(view==='players') return renderList('/api/players',playerCols,'玩家列表',hasPermission('players.manage')?()=>openForm('新增玩家',forms.player):null);
 if(view==='platformOrders') return renderList('/api/orders/platform',platformCols,'平台币订单',hasPermission('orders.manage')?()=>openForm('新增平台币订单',forms.platform):null);
 if(view==='mallOrders') return renderList('/api/orders/mall',mallCols,'商城订单',hasPermission('orders.manage')?()=>openForm('新增商城订单',forms.mall):null);
 if(view==='shipments') return renderList('/api/shipments',shipmentCols,'发货查询',hasPermission('shipments.manage')?()=>openForm('更新发货',forms.shipment):null);
 if(view==='gifts') return renderProducts('gift'); if(view==='products') return renderProducts('product');
 if(view==='cdk') return renderCDK();
 if(view==='rechargeRules') return renderList('/api/recharge-rules',ruleCols,'累充列表',hasPermission('recharge.manage')?()=>openForm('新增累充规则',forms.rule):null);
 if(view==='claims') return renderList('/api/claims',claimCols,'领取记录',hasPermission('claims.manage')?()=>openForm('新增领取记录',forms.claim):null);
 if(view==='sendMail') return renderSendMail(); if(view==='mailRecords') return renderList('/api/mails',mailCols,'发送记录');
 }catch(e){$('#content').innerHTML=`<div class="panel"><div class="empty error">${esc(e.message)}</div></div>`}}

async function renderDashboard(){const [d,a]=await Promise.all([api('/api/dashboard'),api('/api/intelligence/alerts')]);$('#content').innerHTML=`<div class="metrics">
 ${[['代理数量',d.agents],['玩家数量',d.players],['今日流水','¥ '+d.today_turnover.toFixed(2)],['待发货/异常',d.pending_shipments],['平台币订单',d.platform_orders],['商城订单',d.mall_orders],['未兑换CDK',d.cdk_unused],['已兑换CDK',d.cdk_redeemed]].map(x=>`<div class="metric"><div class="label">${x[0]}</div><strong>${x[1]}</strong></div>`).join('')}
 </div>${panel('智能运营提醒',`<div class="alerts">${a.map(x=>`<div class="alert ${x.level}">${esc(x.message)}</div>`).join('')}</div>`,hasPermission('system.rebuild')?`<button class="btn" onclick="rebuildTurnover()">重算代理流水</button>`:'')}`}
window.rebuildTurnover=async()=>{try{alert((await api('/api/agents/rebuild-turnover',{method:'POST'})).message);loadView('dashboard')}catch(e){alert(e.message)}};

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

const agentCols=[['代理ID','agent_id'],['代理等级','agent_level',agentLevelText],['代理名称','agent_name'],['账号','username'],['邀请码','invite_code'],['上级代理','parent_agent_display'],['今日流水','today_turnover'],['昨日流水','yesterday_turnover'],['总流水','total_turnover'],['佣金比例','commission_rate',percent],['状态','status',agentStatusBadge],['操作','id',(_,r)=>hasPermission('channels.edit_basic')||hasPermission('channels.edit_full')?`<button class="btn compact" onclick="openAgentEdit(${Number(r.id)})">编辑</button>`:'-']];
const playerCols=[['玩家ID','player_id'],['账号','username'],['角色名','role_name'],['区服','server_name'],['代理PK','agent_id'],['今日充值','today_recharge'],['总充值','total_recharge'],['最后登录','last_login_at'],['登录IP','last_login_ip']];
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
 player:{path:'/api/players',fields:[['player_id','玩家ID'],['username','账号'],['password','密码','password'],['role_name','角色名'],['server_name','区服'],['agent_id','所属代理PK','number',false],['last_login_ip','登录IP','text',false]]},
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
function openForm(title,cfg){$('#modalTitle').textContent=title;const defaults=cfg.defaults||{};$('#modalForm').setAttribute('autocomplete','off');$('#modalForm').innerHTML=`${cfg.note?`<div class="form-hint">${esc(cfg.note)}</div>`:''}<div class="form-grid">${cfg.fields.map(f=>{const [name,label,type='text',required=true,meta=null]=f;const val=defaults[name]??'';return `<div class="${type==='textarea'?'full':''}"><label>${label}</label>${fieldControl(name,type,val,required,meta)}</div>`}).join('')}<div class="form-actions"><button type="button" class="btn" id="cancelForm">取消</button><button class="btn primary">提交</button></div></div>`;$('#modal').classList.remove('hidden');$('#cancelForm').onclick=closeModal;$('#modalForm').onsubmit=async e=>{e.preventDefault();let obj={};new FormData(e.target).forEach((v,k)=>{if(v==='')return;const f=cfg.fields.find(x=>x[0]===k);obj[k]=f?.[2]==='number'||(f?.[2]==='select'&&f?.[4]?.valueType==='number')?Number(v):v});try{if(cfg.transform)obj=cfg.transform(obj);let path=cfg.path;if(title==='生成兑换码'){path=`/api/redemption-batches/${obj.batch_id}/generate`;delete obj.batch_id}const r=await api(path,{method:cfg.method||'POST',body:JSON.stringify(obj)});alert(r.message||`成功${r.generated?`，已生成 ${r.generated} 个`:''}`);closeModal();loadView(currentView)}catch(err){alert(err.message)}}}
function closeModal(){$('#modal').classList.add('hidden')}$('#closeModal').onclick=closeModal;$('#modal').onclick=e=>{if(e.target===$('#modal'))closeModal()};
