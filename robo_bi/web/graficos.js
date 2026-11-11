/* Biblioteca mínima de gráficos SVG (sem dependências). */
const CORES = ["var(--s1)","var(--s2)","var(--s3)","var(--s4)","var(--s5)","var(--s6)","var(--s7)","var(--s8)"];
const nf0 = new Intl.NumberFormat("pt-BR",{maximumFractionDigits:0});
const nf1 = new Intl.NumberFormat("pt-BR",{minimumFractionDigits:1,maximumFractionDigits:1});
const nf2 = new Intl.NumberFormat("pt-BR",{minimumFractionDigits:2,maximumFractionDigits:2});
const MESES_PT = ["jan","fev","mar","abr","mai","jun","jul","ago","set","out","nov","dez"];
const F = {
  int: v => v==null||!isFinite(v) ? "—" : nf0.format(v),
  num: v => v==null||!isFinite(v) ? "—" : (Math.abs(v)>=1000 ? nf0.format(v) : nf2.format(v)),
  pct: v => v==null||!isFinite(v) ? "—" : nf1.format(v*100)+"%",
  pp:  v => v==null||!isFinite(v) ? "—" : (v>=0?"+":"")+nf1.format(v*100)+" p.p.",
  var: v => v==null||!isFinite(v) ? "—" : (v>=0?"+":"")+nf1.format(v*100)+"%",
  brl: v => v==null||!isFinite(v) ? "—" : "R$ "+nf2.format(v),
  brl0:v => v==null||!isFinite(v) ? "—" : "R$ "+nf0.format(v),
  brlc:v => { if(v==null||!isFinite(v)) return "—"; const a=Math.abs(v);
    if(a>=1e9) return "R$ "+nf2.format(v/1e9)+" bi"; if(a>=1e6) return "R$ "+nf2.format(v/1e6)+" mi";
    if(a>=1e4) return "R$ "+nf1.format(v/1e3)+" mil"; return "R$ "+nf2.format(v); },
  comp:v => { if(v==null||!isFinite(v)) return "—"; const a=Math.abs(v);
    if(a>=1e9) return nf2.format(v/1e9)+" bi"; if(a>=1e6) return nf2.format(v/1e6)+" mi";
    if(a>=1e4) return nf1.format(v/1e3)+" mil"; return a>=100?nf0.format(v):nf2.format(v); },
  mes: s => { const [y,m]=s.split("-"); return MESES_PT[+m-1]+"/"+y.slice(2); },
};
const esc = s => String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const div0 = (a,b) => b ? a/b : null;

/* ---------- tooltip ---------- */
const TIP = (()=>{ const d=document.createElement("div"); d.id="tip"; document.addEventListener("DOMContentLoaded",()=>document.body.appendChild(d)); return d; })();
function tipShow(ev, html){ TIP.innerHTML=html; TIP.style.display="block";
  const w=TIP.offsetWidth, h=TIP.offsetHeight; let x=ev.clientX+14, y=ev.clientY+14;
  if(x+w>innerWidth-8) x=ev.clientX-w-14; if(y+h>innerHeight-8) y=ev.clientY-h-14;
  TIP.style.left=x+"px"; TIP.style.top=y+"px"; }
function tipHide(){ TIP.style.display="none"; }
function ligarTips(el){ el.querySelectorAll("[data-tip]").forEach(n=>{
  n.addEventListener("mousemove",e=>tipShow(e,n.getAttribute("data-tip")));
  n.addEventListener("mouseleave",tipHide); }); }

function escala(min,max,ticks=4){
  if(min===max){ max=min+1; min=Math.min(0,min); }
  const passoBruto=(max-min)/ticks, mag=Math.pow(10,Math.floor(Math.log10(passoBruto)));
  const passo=[1,2,2.5,5,10].map(k=>k*mag).find(p=>p>=passoBruto);
  const lo=Math.floor(min/passo)*passo, hi=Math.ceil(max/passo)*passo, t=[];
  for(let v=lo; v<=hi+passo/2; v+=passo) t.push(+v.toFixed(10));
  return {lo,hi,t};
}

function largura(el){ const w=el.clientWidth||el.parentElement?.clientWidth||0; return Math.max(320, w||760); }
function ticksX(labels,larg){ const n=labels.length, cab=Math.max(2,Math.floor(larg/62)), passo=Math.max(1,Math.ceil(n/cab)), out=[];
  for(let i=0;i<n;i+=passo) out.push(i);
  if(out[out.length-1]!==n-1){ if(n-1-out[out.length-1]<passo*0.6) out.pop(); out.push(n-1); }
  return out; }

/* ---------- linha (1+ séries, linha de referência opcional) ---------- */
function grafLinha(el,{labels,series,fmt=F.num,fmtEixo,ref=null,refRot="",zero=false,h=300}){
  const vals=series.flatMap(s=>s.v).filter(v=>v!=null&&isFinite(v));
  if(!vals.length){ el.innerHTML='<div class="vazio">Sem dados no período</div>'; return; }
  fmtEixo=fmtEixo||fmt;
  let mn=Math.min(...vals,ref??Infinity), mx=Math.max(...vals,ref??-Infinity);
  if(zero) mn=Math.min(0,mn); const pad=(mx-mn)*0.08||Math.abs(mx)*0.1||1;
  const sc=escala(zero&&mn>=0?0:(mn>=0?Math.max(0,mn-pad):mn-pad), mx+pad);
  const W=largura(el),H=h,L=58,R=70,T=14,B=30, n=labels.length;
  const X=i=> L+(n<=1?(W-L-R)/2:i*(W-L-R)/(n-1)), Y=v=> T+(H-T-B)*(1-(v-sc.lo)/(sc.hi-sc.lo));
  let s=`<svg viewBox="0 0 ${W} ${H}" role="img"><g class="grid">`;
  sc.t.forEach(t=>{ s+=`<line x1="${L}" x2="${W-R}" y1="${Y(t)}" y2="${Y(t)}"/><text x="${L-8}" y="${Y(t)+4}" text-anchor="end">${fmtEixo(t)}</text>`; });
  s+="</g>";
  ticksX(labels,W-L-R).forEach(i=>{ s+=`<text x="${X(i)}" y="${H-8}" text-anchor="middle">${esc(labels[i])}</text>`; });
  if(ref!=null) s+=`<line x1="${L}" x2="${W-R}" y1="${Y(ref)}" y2="${Y(ref)}" stroke="var(--ink-2)" stroke-width="1.5" stroke-dasharray="7 5"/><text x="${L+6}" y="${Y(ref)-6}" style="fill:var(--ink-2)">${esc(refRot)}</text>`;
  series.forEach((se,k)=>{
    const cor=se.cor||CORES[k]; let d="", aberto=false;
    se.v.forEach((v,i)=>{ if(v==null||!isFinite(v)){aberto=false;return;} d+=(aberto?"L":"M")+X(i).toFixed(1)+","+Y(v).toFixed(1); aberto=true; });
    s+=`<path d="${d}" fill="none" stroke="${cor}" stroke-width="2.25" stroke-linejoin="round" stroke-linecap="round"/>`;
    let ult=-1; se.v.forEach((v,i)=>{ if(v!=null&&isFinite(v)) ult=i; });
    if(ult>=0){ s+=`<circle cx="${X(ult)}" cy="${Y(se.v[ult])}" r="4.5" fill="${cor}" stroke="var(--surface)" stroke-width="2"/>`;
      if(series.length<=4) s+=`<text x="${X(ult)+9}" y="${Y(se.v[ult])+4}" style="fill:var(--ink);font-weight:700">${fmt(se.v[ult])}</text>`; }
  });
  s+=`<line class="cruz" x1="0" x2="0" y1="${T}" y2="${H-B}" stroke="var(--muted)" stroke-width="1" style="display:none"/>`;
  s+=`<rect class="hit" x="${L}" y="${T}" width="${W-L-R}" height="${H-T-B}" fill="transparent"/></svg>`;
  let leg=""; if(series.length>1) leg='<div class="legenda">'+series.map((se,k)=>`<span><i style="background:${se.cor||CORES[k]}"></i>${esc(se.nome)}</span>`).join("")+"</div>";
  el.innerHTML=leg+s;
  const svg=el.querySelector("svg"), cruz=svg.querySelector(".cruz");
  svg.querySelector(".hit").addEventListener("mousemove",e=>{
    const r=svg.getBoundingClientRect(), x=(e.clientX-r.left)*W/r.width;
    const i=Math.max(0,Math.min(n-1,Math.round(n<=1?0:(x-L)/((W-L-R)/(n-1)))));
    cruz.setAttribute("x1",X(i)); cruz.setAttribute("x2",X(i)); cruz.style.display="";
    let html=`<div style="color:var(--ink-2)">${esc(labels[i])}</div>`;
    series.forEach((se,k)=>{ html+=`<div><i style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${se.cor||CORES[k]};margin-right:6px"></i>${esc(se.nome)}: <b>${fmt(se.v[i])}</b></div>`; });
    if(ref!=null) html+=`<div style="color:var(--muted)">${esc(refRot)}</div>`;
    tipShow(e,html);
  });
  svg.querySelector(".hit").addEventListener("mouseleave",()=>{cruz.style.display="none";tipHide();});
}

/* ---------- colunas verticais ---------- */
function grafColunas(el,{labels,v,fmt=F.num,cor="var(--s1)",h=260,destacarUltima=true,tips}){
  const vals=v.filter(x=>x!=null&&isFinite(x));
  if(!vals.length){ el.innerHTML='<div class="vazio">Sem dados no período</div>'; return; }
  const sc=escala(Math.min(0,...vals),Math.max(0,...vals));
  const W=largura(el),H=h,L=58,R=12,T=18,B=30,n=labels.length, bw=(W-L-R)/n, larg=Math.max(2,Math.min(38,bw-2)); const tk=new Set(ticksX(labels,W-L-R));
  const Y=x=>T+(H-T-B)*(1-(x-sc.lo)/(sc.hi-sc.lo));
  let s=`<svg viewBox="0 0 ${W} ${H}" role="img"><g class="grid">`;
  sc.t.forEach(t=>{ s+=`<line x1="${L}" x2="${W-R}" y1="${Y(t)}" y2="${Y(t)}"/><text x="${L-8}" y="${Y(t)+4}" text-anchor="end">${fmt(t)}</text>`; });
  s+="</g>";
  v.forEach((x,i)=>{ const cx=L+bw*i+bw/2;
    if(x!=null&&isFinite(x)){ const y0=Y(0), y1=Y(x), top=Math.min(y0,y1), hh=Math.max(1,Math.abs(y1-y0));
      const c=(destacarUltima&&i===n-1)?"var(--s2)":cor;
      s+=`<path d="${barraPath(cx-larg/2,top,larg,hh,x>=0)}" fill="${c}" data-tip="${esc(tips?tips[i]:labels[i]+': <b>'+fmt(x)+'</b>')}"/>`; }
    if(tk.has(i)) s+=`<text x="${cx}" y="${H-8}" text-anchor="middle">${esc(labels[i])}</text>`; });
  el.innerHTML=s+"</svg>"; ligarTips(el);
}
function barraPath(x,y,w,h,cima){ const r=Math.min(4,w/2,h);
  return cima ? `M${x},${y+h}V${y+r}Q${x},${y} ${x+r},${y}H${x+w-r}Q${x+w},${y} ${x+w},${y+r}V${y+h}Z`
              : `M${x},${y}V${y+h-r}Q${x},${y+h} ${x+r},${y+h}H${x+w-r}Q${x+w},${y+h} ${x+w},${y+h-r}V${y}Z`; }

/* ---------- barras horizontais (ranking) ---------- */
function grafBarrasH(el,{itens,fmt=F.num,cor="var(--s1)",extra,max}){
  if(!itens.length){ el.innerHTML='<div class="vazio">Sem dados</div>'; return; }
  const mx=max??Math.max(...itens.map(i=>Math.abs(i.v)),1e-9);
  el.innerHTML=`<table class="tab" style="font-size:12.5px"><tbody>`+itens.map(it=>`
    <tr data-tip="${esc(it.tip||(esc(it.r)+': <b>'+fmt(it.v)+'</b>'))}"><td style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(it.r)}">${esc(it.r)}</td>
    <td style="width:45%"><div class="bar-bg"><i style="width:${Math.max(0.5,Math.abs(it.v)/mx*100)}%;background:${it.cor||cor}"></i></div></td>
    <td class="n">${fmt(it.v)}</td>${extra?`<td class="n" style="color:var(--muted)">${extra(it)}</td>`:""}</tr>`).join("")+"</tbody></table>";
  ligarTips(el);
}

/* ---------- pirâmide etária ---------- */
function grafPiramide(el,{faixas,m,f,destaque=9}){
  const tot=m.reduce((a,b)=>a+b,0)+f.reduce((a,b)=>a+b,0);
  if(!tot){ el.innerHTML='<div class="vazio">Sem dados de sexo/idade</div>'; return; }
  const mx=Math.max(...m,...f,1), W=760, lh=31, T=30, H=T+faixas.length*lh+6, C=W/2, meio=38, larg=C-meio-60;
  let s=`<svg viewBox="0 0 ${W} ${H}" role="img">
   <text x="${C-meio-8}" y="16" text-anchor="end" style="fill:var(--s1);font-family:var(--sans);font-size:13px;font-weight:600">Masculino</text>
   <text x="${C+meio+8}" y="16" style="fill:var(--s2);font-family:var(--sans);font-size:13px;font-weight:600">Feminino</text>`;
  for(let k=faixas.length-1, r=0; k>=0; k--, r++){
    const y=T+r*lh, wm=m[k]/mx*larg, wf=f[k]/mx*larg, hb=lh-7;
    s+=`<rect x="${C-meio-wm}" y="${y}" width="${Math.max(wm,1)}" height="${hb}" rx="3" fill="var(--s1)" data-tip="Masculino ${faixas[k]}: <b>${F.int(m[k])}</b> (${F.pct(m[k]/tot)})"/>
        <rect x="${C+meio}" y="${y}" width="${Math.max(wf,1)}" height="${hb}" rx="3" fill="var(--s2)" data-tip="Feminino ${faixas[k]}: <b>${F.int(f[k])}</b> (${F.pct(f[k]/tot)})"/>
        <text x="${C}" y="${y+hb/2+4}" text-anchor="middle" style="fill:${k===destaque?'var(--crit)':'var(--ink)'};font-weight:700;font-size:12px">${faixas[k]}</text>
        <text x="${C-meio-wm-6}" y="${y+hb/2+4}" text-anchor="end">${F.int(m[k])}</text>
        <text x="${C+meio+wf+6}" y="${y+hb/2+4}">${F.int(f[k])}</text>`;
  }
  el.innerHTML=s+"</svg>"; ligarTips(el);
}

/* ---------- tabela ordenável ---------- */
function tabela(el,{cols,linhas,ordem=null,limite=null}){
  if(!linhas.length){ el.innerHTML='<div class="vazio">Nenhum registro</div>'; return; }
  let st={k:ordem?ordem[0]:null, d:ordem?ordem[1]:-1};
  const desenhar=()=>{
    let ls=linhas.slice();
    if(st.k!=null){ const c=cols.find(c=>c.k===st.k); const val=(c&&c.ord)||(r=>r[st.k]);
      ls.sort((a,b)=>{ const x=val(a), y=val(b); if(x==null) return 1; if(y==null) return -1; return (x>y?1:x<y?-1:0)*st.d; }); }
    if(limite) ls=ls.slice(0,limite);
    el.innerHTML=`<table class="tab"><thead><tr>${cols.map(c=>`<th class="ord ${c.n?'n':''}" data-k="${c.k}">${esc(c.t)}${st.k===c.k?(st.d>0?" ▲":" ▼"):""}</th>`).join("")}</tr></thead><tbody>`+
      ls.map(r=>`<tr>${cols.map(c=>`<td class="${c.n?'n':''}">${c.f?c.f(r[c.k],r):esc(r[c.k])}</td>`).join("")}</tr>`).join("")+"</tbody></table>";
    el.querySelectorAll("th").forEach(th=>th.onclick=()=>{ const k=th.dataset.k; st.d=st.k===k?-st.d:-1; st.k=k; desenhar(); });
    ligarTips(el);
  }; desenhar();
}

function pillStatus(st){ const m={OK:"st-ok",ALERTA:"st-alerta",ERRO:"st-erro",INFO:"st-info"};
  const ic={OK:"✓",ALERTA:"!",ERRO:"✕",INFO:"i"}; return `<span class="status-pill ${m[st]||'st-info'}">${ic[st]||""} ${esc(st)}</span>`; }

function painelValidacao(el,regras){
  tabela(el,{cols:[
    {k:"status",t:"Status",f:v=>pillStatus(v),ord:r=>({ERRO:0,ALERTA:1,INFO:2,OK:3})[r.status]},
    {k:"grupo",t:"Base"},{k:"regra",t:"Regra"},{k:"qtd",t:"Qtd",n:1,f:v=>v?F.int(v):"—"},
    {k:"detalhe",t:"Resultado",f:(v,r)=>esc(v)+(r.amostra&&r.amostra.length?`<details><summary>ver exemplos</summary><div style="font-family:var(--mono);font-size:11.5px;color:var(--muted)">${r.amostra.map(esc).join("<br>")}</div></details>`:"")}],
    linhas:regras, ordem:["status",1]});
}

function abas(nav,onTroca){
  nav.querySelectorAll("button").forEach(b=>b.addEventListener("click",()=>{
    nav.querySelectorAll("button").forEach(x=>x.setAttribute("aria-selected",x===b));
    document.querySelectorAll(".aba").forEach(a=>a.classList.toggle("ativa",a.id===b.dataset.aba));
    try{ localStorage.setItem("aba:"+document.title,b.dataset.aba); history.replaceState(null,"","#/"+b.dataset.aba);}catch(e){}
    onTroca&&onTroca(b.dataset.aba);
  }));
  let ini=location.hash?location.hash.replace(/^#\/?/,""):null;
  if(!ini){ try{ ini=localStorage.getItem("aba:"+document.title);}catch(e){} }
  const alvo=nav.querySelector(`[data-aba="${ini}"]`)||nav.querySelector("button"); alvo.click();
}

function aoRedimensionar(fn){ let t; addEventListener("resize",()=>{ clearTimeout(t); t=setTimeout(fn,200); }); }
