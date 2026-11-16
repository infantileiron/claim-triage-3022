import {state} from '../stores/session.js';
import {esc} from '../utils/html.js';
import {table} from '../components/common/table.js';
import {badge} from '../components/common/status.js';
import {field} from '../components/common/fields.js';
import {STALE_REASONS,STATUS_TEXT,EVENT_TEXT,LINK_WINDOW_HOURS} from '../constants/enums.js';

const money=v=>Number(v||0).toFixed(2);
const when=v=>v?String(v).replace('T',' ').slice(0,16):'—';
const statusBadge=g=>badge(STATUS_TEXT[g.status]||g.status)+(g.stale?` ${badge('依据过期 · '+(STALE_REASONS[g.stale_reason]||g.stale_reason))}`:'');

function groupCard(g){
 const members=g.members||[];
 const chain=g.chain||[];
 const events=g.events||[];
 return `<article class=rec data-group="${g.id}">
  <h3>组 #${g.id} · ${g.member_count} 笔 · 合计 ${money(g.total_amount)} 元 ${statusBadge(g)}</h3>
  <p>${esc(g.members?.[0]?.claimant||'')} / ${esc(g.members?.[0]?.merchant||'')} · 版本 v${g.review_version}${g.carried_from?` · 沿用自组 #${g.carried_from}`:''}</p>
  <div class=group-grid>
   <div><h4>成员</h4>${table(['样本','编号','金额','消费时间'],members.map(m=>[m.id,m.code,money(m.amount),when(m.occurred_at)]))}</div>
   <div><h4>关联链（≤${LINK_WINDOW_HOURS}小时连边）</h4>${table(['从','到','间隔/小时'],chain.map(c=>[`#${c.from_id} ${c.from_code}`,`#${c.to_id} ${c.to_code}`,c.gap_hours]))}</div>
  </div>
  <h4>历次变化</h4>${table(['时间','事件','说明','操作人'],events.map(e=>[when(e.created),EVENT_TEXT[e.event]||e.event,eventDetail(e.detail),e.actor]))}
  ${g.is_current!==false&&g.status!=='below_limit'&&!g.stale?`<p class=inline-form><select data-decision="${g.id}"><option value="valid">核验正常</option><option value="flagged">需进一步核实</option></select> <input data-note="${g.id}" placeholder="理由与证据（至少6字）" maxlength=2000> <button data-review="${g.id}" data-version="${g.review_version}">保存组级复核</button></p>`:''}
  ${g.is_current!==false&&state.user.role==='admin'&&!g.stale&&g.status==='draft'?`<button data-approve-group="${g.id}" data-version="${g.review_version}">负责人签核组</button>`:''}
  ${g.stale?`<p class=notice>该组依据已过期，原结论保留作为留档；请在最新筛查版本中复核新组。</p>`:''}
  ${g.is_current===false?`<p class=notice>该组来自历史筛查版本，仅供追溯；请在当前版本中复核。</p>`:''}
 </article>`}
function eventDetail(d){
 if(!d||typeof d!=='object')return '';
 if(d.decision)return `${d.decision} · ${d.note||''}`;
 const bits=[];
 if(d.claim_ids)bits.push('样本 '+d.claim_ids.map(i=>'#'+i).join('、'));
 if(d.new_group_ids)bits.push('新组 '+d.new_group_ids.map(i=>'#'+i).join('、'));
 if(d.stale_reason)bits.push('原因 '+(STALE_REASONS[d.stale_reason]||d.stale_reason));
 if(d.total_amount!==undefined)bits.push('合计 '+money(d.total_amount));
 return bits.join('；');
}

export function render(){
 const list=state.screenings||[];
 const sel=state.selectedScreening;
 const groups=(sel?.groups||[]).slice().sort((a,b)=>b.total_amount-a.total_amount);
 const over=groups.filter(g=>g.status!=='below_limit');
 const below=groups.filter(g=>g.status==='below_limit');
 const incomplete=sel?.incomplete||[];
 return `<div class=split><section class=panel>
  <h2>拆单关联筛查</h2>
  <p class=notice>逐票评分容易漏掉拆单报销：这里补录报销人、商户、消费时间，同人同商户且消费时间相差不超过 ${LINK_WINDOW_HOURS} 小时的样本连边，按传递关系聚合。组内金额合计严格超过限额才进入组级复核。</p>
  <h3>筛查版本</h3>${table(['版本','限额','触发','状态','组数','超限组','信息不全','提交人','完成时间'],list.map(s=>[`#${s.id}`,money(s.limit_amount),s.trigger==='claim_update'?'补录重算':'手动',STATUS_TEXT[s.state]||s.state,s.group_count,s.over_limit_count,s.incomplete_count,esc(s.submitted_by),when(s.finished_at)]))}
  ${list.filter(s=>s.state==='failed').map(s=>`<p class=notice>版本 #${s.id} 失败：${esc(s.error||'未知原因')} <button data-retry="${s.id}">重试</button></p>`).join('')}
  <h3>切换筛查版本</h3>${list.map(s=>`<button class=study data-screening="${s.id}">#${s.id} · 限额 ${money(s.limit_amount)} · ${STATUS_TEXT[s.state]||s.state}${s.is_current?' · 当前':' · 历史'}</button>`).join('')||'<p>暂无筛查版本。</p>'}
  ${sel?`<h3>版本 #${sel.id} 的关联组 ${sel.is_current===false?badge('历史版本'):badge('当前版本')}</h3>
   <p class=eyebrow>可参与连边样本 ${sel.eligible_count} 笔 · 关联组 ${sel.group_count} 个 · 超限 ${sel.over_limit_count} 组 · 信息不全 ${sel.incomplete_count} 笔</p>
   ${over.length?over.map(groupCard).join(''):'<p>当前版本没有合计超限额的关联组。</p>'}
   ${below.length?`<h3>未超限额（保留分组，不进入组级复核）</h3>${below.map(groupCard).join('')}`:''}
   <h3>信息不全的旧样本（单列，不参与连边）</h3>${table(['样本','编号','金额','报销人','商户','消费时间','缺失字段'],incomplete.map(c=>[c.id,c.code,money(c.amount),c.claimant||'—',c.merchant||'—',when(c.occurred_at),c.missing.map(m=>({claimant:'报销人',merchant:'商户',occurred_at:'消费时间'}[m]||m)).join('、')]))}
  `:''}
 </section><aside class=side><h2>发起关联筛查</h2>
  <form id="screening">${field('组级复核限额 / 元','limit_amount','number','required min=0.01 max=10000000 step=0.01 value=5000')}
   <p>同人同商户、消费时间相差不超过 ${LINK_WINDOW_HOURS} 小时连边；传递关联后合计严格超过该限额的组进入复核。</p>
   <button class=primary>开始筛查</button></form>
  <h3>样本字段补录</h3><form id="claim">
   ${field('记录ID（修订时填写）','record_id','number','min=1')}
   ${field('报销编号','code','text','required')}
   ${field('金额 / 元','amount','number','required min=0 max=100000 step=0.01')}
   ${field('里程 / km','distance','number','required min=0 max=10000 step=0.1')}
   ${field('报销人','claimant','text','maxlength=100')}
   ${field('商户','merchant','text','maxlength=200')}
   ${field('消费时间','occurred_at','datetime-local')}
   ${field('人工标签','label','number','min=0 max=1 step=1')}
   <label>说明<textarea name=note rows=3></textarea></label>
   <button class=primary>保存并重算分组</button></form>
  <p>补录或更正可能让已签核组拆分或合并，保存后自动生成新的筛查版本。</p>
 </aside></div>`}
