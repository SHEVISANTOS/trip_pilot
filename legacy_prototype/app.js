const $=id=>document.getElementById(id);
const fmt=(n,c='USD')=>new Intl.NumberFormat('en-US',{style:'currency',currency:c,maximumFractionDigits:0}).format(n);
const demo={
visa:{type:"Tourist entry requirement",method:"Official immigration requirements must be verified for the traveller's nationality before departure.",stay:"Check permitted length of stay",cost:60,tag:"VERIFY"},
flights:[
{airline:"Turkish Airlines",route:"DAR → IST → DAR",stops:"1 stop",duration:"Approx. 11–15 hrs each way",price:1650,label:"Recommended"},
{airline:"Qatar Airways",route:"DAR → DOH → IST → DAR",stops:"1 stop",duration:"Approx. 13–17 hrs each way",price:1480,label:"Cheapest"},
{airline:"Ethiopian Airlines",route:"DAR → ADD → IST → DAR",stops:"1 stop",duration:"Approx. 12–16 hrs each way",price:1540,label:"Alternative"}],
hotels:[
{name:"Sultanahmet Comfort Hotel",area:"Sultanahmet",rating:"4★",night:135,total:1200,desc:"Central location, breakfast included, family-friendly."},
{name:"Taksim City Residence",area:"Taksim",rating:"4★",night:120,total:1080,desc:"Modern rooms with easy access to restaurants and transport."},
{name:"Old City Boutique Hotel",area:"Fatih",rating:"3★",night:85,total:760,desc:"Value option near major historic attractions."}],
attractions:[
{name:"Hagia Sophia & Sultanahmet",cost:30,desc:"Historic district and landmark visit."},
{name:"Topkapı Palace",cost:45,desc:"Palace complex and museum experience."},
{name:"Bosphorus Cruise",cost:55,desc:"Half-day sightseeing cruise."},
{name:"Grand Bazaar",cost:0,desc:"Shopping and cultural experience."},
{name:"Galata Tower",cost:20,desc:"City views and historic neighbourhood."},
{name:"Cappadocia Day Experience",cost:180,desc:"Optional premium excursion; transport/tour varies."}]
};

function symbol(c){return {USD:'$',TZS:'TSh',EUR:'€',GBP:'£'}[c]||'$'}
function sampleNames(destination){
 const d=destination.toLowerCase();
 if(d.includes('istanbul')||d.includes('turk')) return demo;
 return {
  visa:{type:"Destination entry requirements",method:"Check official immigration authority for nationality-specific requirements.",stay:"Verify before booking",cost:60,tag:"VERIFY"},
  flights:demo.flights.map(x=>({...x,route:`Your departure → ${destination} → Return`})),
  hotels:demo.hotels.map((x,i)=>({...x,name:[`${destination} Central Hotel`,`Grand ${destination} Residence`,`${destination} Boutique Stay`][i]})),
  attractions:demo.attractions.map(x=>({...x,name:x.name.replace(/Hagia Sophia|Sultanahmet|Topkapı Palace|Bosphorus Cruise|Grand Bazaar|Galata Tower|Cappadocia Day Experience/, destination+" sightseeing experience")}))
 };
}
function calculatePlan(){
 const dep=$('departure').value||'Departure';
 const dest=$('destination').value||'Destination';
 const adults=+$('adults').value||1, children=+$('children').value||0;
 const people=adults+children;
 const budget=+$('budget').value||0, currency=$('currency').value;
 const data=sampleNames(dest);
 let start=new Date($('startDate').value), end=new Date($('endDate').value);
 let nights=8;
 if(!isNaN(start)&&!isNaN(end)&&end>start) nights=Math.max(1,Math.round((end-start)/86400000));
 const style=$('style').value;
 const mult=style==='budget'?0.82:style==='luxury'?1.55:1;
 const flight=Math.round(1650*mult*(people/3));
 const hotel=Math.round(135*nights*mult);
 const transfer=Math.round(80*mult);
 const transport=Math.round(25*nights*mult);
 const food=Math.round(75*nights*people*mult);
 const attractions=Math.round((30+45+55+20)*mult);
 const insurance=Math.round(100*(people/3));
 const sim=50;
 const emergency=Math.round(Math.max(200,(flight+hotel+food)*0.12));
 const visa=data.visa.cost*people;
 const items=[
 ['Visa',data.visa.type,visa],
 ['Flights',`${data.flights[0].airline} — ${data.flights[0].route}`,flight],
 ['Hotel',`${data.hotels[0].name} — ${nights} nights`,hotel],
 ['Airport transfers','Airport → Hotel → Airport',transfer],
 ['Local transport','Public transport + taxi allowance',transport],
 ['Food','Restaurants + daily meal allowance',food],
 ['Attractions','Named attractions and activities',attractions],
 ['Travel insurance','Estimated travel cover',insurance],
 ['SIM/eSIM','Local connectivity package',sim],
 ['Emergency reserve','Unallocated contingency fund',emergency]
 ];
 const total=items.reduce((a,x)=>a+x[2],0), remain=budget-total;
 return {dep,dest,adults,children,people,budget,currency,data,nights,items,total,remain,flight,hotel,transfer,transport,food,attractions,insurance,sim,emergency,visa};
}
function render(){
 const p=calculatePlan(), C=p.currency, data=p.data;
 $('results').classList.remove('hidden');
 $('tripHeading').textContent=p.dest;
 $('tripMeta').textContent=`${p.dep} • ${p.people} traveller${p.people!==1?'s':''} • ${p.nights} nights • ${$('purpose').value}`;
 $('budgetKpi').textContent=fmt(p.budget,C); $('totalKpi').textContent=fmt(p.total,C); $('remainingKpi').textContent=fmt(p.remain,C); $('remainingKpi').className=p.remain>=0?'good':'';
 $('usedKpi').textContent=(p.budget?Math.max(0,p.total/p.budget*100):0).toFixed(1)+'%';
 $('visaTag').textContent=data.visa.tag;
 $('visaContent').innerHTML=`<ul class="small-list"><li><span><b>Type</b><br>${data.visa.type}</span></li><li><span><b>Application</b><br>${data.visa.method}</span></li><li><span><b>Length of stay</b><br>${data.visa.stay}</span></li><li><span><b>Estimated planning allowance</b></span><b>${fmt(p.visa,C)}</b></li></ul>`;
 $('budgetTable').innerHTML=`<table class="data-table"><tbody>${p.items.map(x=>`<tr><td><b>${x[0]}</b><br><small>${x[1]}</small></td><td>${fmt(x[2],C)}</td></tr>`).join('')}<tr><td><b>TOTAL</b></td><td>${fmt(p.total,C)}</td></tr></tbody></table>`;
 const within=p.remain>=0; $('budgetStatus').textContent=within?'WITHIN BUDGET':'OVER BUDGET'; $('budgetStatus').className='tag '+(within?'green':'');
 $('alertBox').className='alert '+(within?'info':'warn'); $('alertBox').textContent=within?`ℹ️ Your plan is currently within budget with ${fmt(p.remain,C)} remaining. Prices are illustrative until live APIs are connected.`:`⚠️ Your current plan is ${fmt(Math.abs(p.remain),C)} over budget. Use “Optimize for My Budget” to reduce costs.`;
 $('flights').innerHTML=data.flights.map((f,i)=>`<div class="flight"><div><span class="tag ${i===0?'green':''}">${f.label}</span><h4>${f.airline}</h4></div><div class="route"><b>${f.route}</b>${f.stops} • ${f.duration}</div><div><small>Estimated total</small><div class="price">${fmt(Math.round(f.price*(p.people/3)),C)}</div></div><button class="primary" data-book="${f.airline} flight">View / Book</button></div>`).join('');
 $('hotels').innerHTML=data.hotels.map((h,i)=>{let t=i===0?p.hotel:Math.round(h.total*(p.nights/8)*(p.people/3));return `<div class="card"><span class="tag ${i===0?'green':''}">${i===0?'RECOMMENDED':h.rating}</span><h4>${h.name}</h4><p>${h.area} • ${h.rating}</p><p>${h.desc}</p><div class="card-price">${fmt(t,C)} <small>total</small></div><p>Approx. ${fmt(Math.round(t/p.nights),C)}/night • ${p.nights} nights</p><button class="primary" data-book="${h.name} hotel">View / Book</button></div>`}).join('');
 $('transport').innerHTML=`<ul class="small-list"><li><span>Airport → ${data.hotels[0].name}<br><small>Private/shared transfer estimate</small></span><b>${fmt(p.transfer/2,C)}</b></li><li><span>${data.hotels[0].name} → attractions<br><small>Metro, tram and taxis</small></span><b>${fmt(p.transport,C)}</b></li><li><span>Hotel → Airport</span><b>${fmt(p.transfer/2,C)}</b></li><li><span><b>Total transport allocation</b></span><b>${fmt(p.transfer+p.transport,C)}</b></li></ul>`;
 const perDay=Math.round(p.food/p.nights);
 $('food').innerHTML=`<ul class="small-list"><li><span>Breakfast<br><small>${data.hotels[0].name} — where included</small></span><b>Included/varies</b></li><li><span>Lunch<br><small>Local restaurant allowance</small></span><b>${fmt(Math.round(perDay*.38),C)}/day</b></li><li><span>Dinner<br><small>Restaurant allowance</small></span><b>${fmt(Math.round(perDay*.52),C)}/day</b></li><li><span>Snacks & drinks</span><b>${fmt(Math.round(perDay*.1),C)}/day</b></li><li><span><b>Total food allocation</b></span><b>${fmt(p.food,C)}</b></li></ul>`;
 $('attractions').innerHTML=data.attractions.map((a,i)=>`<div class="card"><span class="tag">${a.cost===0?'FREE':'ACTIVITY'}</span><h4>${a.name}</h4><p>${a.desc}</p><div class="card-price">${fmt(Math.round(a.cost*(p.people/3)),C)}</div><button class="secondary" data-book="${a.name} activity">View / Book</button></div>`).join('');
 const attr=data.attractions.slice(0,Math.min(5,p.nights)).map((a,i)=>({name:a.name,cost:Math.round(a.cost*(p.people/3))}));
 let days=[];
 days.push({title:'Day 1 — Arrival',rows:[['15:20','Arrive at destination','Flight arrival'],['16:30',`Transfer to ${data.hotels[0].name}`,fmt(p.transfer/2,C)],['19:00','Welcome dinner',fmt(Math.round(perDay*.55),C)]]});
 for(let i=1;i<p.nights;i++){let a=attr[(i-1)%attr.length];days.push({title:`Day ${i+1} — Explore`,rows:[['09:00','Breakfast / depart hotel','Included'],['10:00',a.name,fmt(a.cost,C)],['13:00','Lunch',fmt(Math.round(perDay*.38),C)],['18:30','Dinner / free time',fmt(Math.round(perDay*.52),C)]]});}
 days.push({title:`Day ${p.nights+1} — Departure`,rows:[['08:00','Breakfast and check-out','Included/varies'],['10:00','Transfer to airport',fmt(p.transfer/2,C)],['Departure','Return flight','Included in flight budget']]});
 $('itinerary').innerHTML=days.map(d=>`<div class="it-day"><h4>${d.title}</h4>${d.rows.map(r=>`<div class="timeline"><time>${r[0]}</time><span>${r[1]}</span><span class="cost">${r[2]}</span></div>`).join('')}</div>`).join('');
 const checks=['Verify visa and passport validity','Select and book flights','Select and book hotel','Arrange airport transfer','Purchase travel insurance','Arrange SIM/eSIM','Book priority attractions','Check-in online before departure','Prepare emergency cash and cards'];
 $('checklist').innerHTML=checks.map(x=>`<div class="check">☐ ${x}</div>`).join('');
 document.querySelectorAll('[data-book]').forEach(b=>b.onclick=()=>openModal('Booking: '+b.dataset.book,'This action is ready to connect to a live booking partner or provider.'));
 document.querySelectorAll('.check').forEach(c=>c.onclick=()=>{c.classList.toggle('done');c.textContent=(c.classList.contains('done')?'☑ ':'☐ ')+c.textContent.slice(2)});
 $('results').scrollIntoView({behavior:'smooth',block:'start'});
}
function openModal(t,txt){$('modalTitle').textContent=t;$('modalText').textContent=txt;$('modal').classList.remove('hidden')}
$('tripForm').addEventListener('submit',e=>{e.preventDefault();render()});
$('demoBtn').onclick=()=>{const now=new Date();const a=new Date(now.getFullYear()+1,4,10),b=new Date(now.getFullYear()+1,4,18);$('startDate').value=a.toISOString().slice(0,10);$('endDate').value=b.toISOString().slice(0,10);$('destination').value='Istanbul, Türkiye';$('budget').value=5000;render()};
$('clearBtn').onclick=()=>{$('tripForm').reset();$('budget').value=5000;$('adults').value=2;$('children').value=1;$('results').classList.add('hidden')};
$('currency').onchange=()=>{$('currencySymbol').textContent=symbol($('currency').value)};
$('optimizeBtn').onclick=()=>{const p=calculatePlan();if(p.remain<0){$('style').value='budget';$('hotelPref').value='3–4 Star Hotel';render();}else openModal('Budget optimization',`Your current plan is already within budget with ${fmt(p.remain,p.currency)} remaining. In a live version, the optimizer would compare real flights, hotels and activities.`)};
$('closeModal').onclick=()=>$('modal').classList.add('hidden');$('modalOk').onclick=()=>$('modal').classList.add('hidden');
(function(){const now=new Date();const a=new Date(now.getFullYear()+1,4,10),b=new Date(now.getFullYear()+1,4,18);$('startDate').value=a.toISOString().slice(0,10);$('endDate').value=b.toISOString().slice(0,10);$('currencySymbol').textContent='$';})();