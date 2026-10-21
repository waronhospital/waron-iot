/* Tooltips shared by device and temperature charts; no external dependencies. */
(() => {
  const charts=[...document.querySelectorAll('svg.chart')];
  if(!charts.length)return;
  const tooltip=document.createElement('div');
  tooltip.id='chart-tooltip';tooltip.className='chart-tooltip';tooltip.hidden=true;
  tooltip.setAttribute('role','tooltip');document.body.appendChild(tooltip);
  let active=null,pinned=false;
  function hide(){
    if(active){active.classList.remove('chart-point-active');active.removeAttribute('aria-describedby');}
    active=null;pinned=false;tooltip.hidden=true;
  }
  function show(point,x,y){
    if(active!==point){
      if(active){active.classList.remove('chart-point-active');active.removeAttribute('aria-describedby');}
      tooltip.replaceChildren();
      const parts=point.getAttribute('data-chart-label').split(' · ');
      parts.forEach((part,index)=>{const row=document.createElement(index?'div':'strong');row.textContent=part;tooltip.appendChild(row);});
      active=point;point.classList.add('chart-point-active');point.setAttribute('aria-describedby',tooltip.id);
    }
    tooltip.hidden=false;
    const box=tooltip.getBoundingClientRect(),margin=10;
    const left=Math.max(margin,Math.min(x+14,window.innerWidth-box.width-margin));
    const top=y+16+box.height<window.innerHeight-margin?y+16:Math.max(margin,y-box.height-14);
    tooltip.style.left=left+'px';tooltip.style.top=top+'px';
  }
  function nearest(chart,event){
    const matrix=chart.getScreenCTM();if(!matrix)return null;
    let selected=null,distance=30;
    chart.querySelectorAll('[data-chart-label]').forEach(point=>{
      const position=chart.createSVGPoint();
      position.x=Number(point.getAttribute('cx')||point.getAttribute('x'));
      position.y=Number(point.getAttribute('cy')||point.getAttribute('y'));
      const screen=position.matrixTransform(matrix);
      const d=Math.hypot(screen.x-event.clientX,screen.y-event.clientY);
      if(d<distance){distance=d;selected=point;}
    });
    return selected;
  }
  for(const chart of charts){
    function prepare(){
      if(active&&chart.contains(active)===false&&!active.isConnected)hide();
      chart.querySelectorAll('circle,text').forEach(point=>{
        const title=point.querySelector('title');if(!title)return;
        const label=title.textContent.trim();if(!label)return;
        point.setAttribute('data-chart-label',label);point.setAttribute('aria-label',label);
        point.setAttribute('tabindex','0');title.remove();
      });
    }
    new MutationObserver(prepare).observe(chart,{childList:true,subtree:true});prepare();
    chart.addEventListener('pointermove',event=>{
      if(event.pointerType==='touch'||pinned)return;
      const point=nearest(chart,event);if(point)show(point,event.clientX,event.clientY);else hide();
    });
    chart.addEventListener('pointerleave',()=>{if(!pinned)hide();});
    chart.addEventListener('pointerdown',event=>{
      if(event.pointerType!=='touch')return;
      const point=nearest(chart,event);if(!point){hide();return;}
      if(point===active&&pinned){hide();return;}show(point,event.clientX,event.clientY);pinned=true;
    });
    chart.addEventListener('focusin',event=>{
      const point=event.target.closest('[data-chart-label]');if(!point)return;
      const box=point.getBoundingClientRect();show(point,box.left+box.width/2,box.top+box.height/2);
    });
    chart.addEventListener('focusout',hide);
  }
  document.addEventListener('pointerdown',event=>{if(!event.target.closest('svg.chart'))hide();});
  document.addEventListener('keydown',event=>{if(event.key==='Escape')hide();});
  window.addEventListener('scroll',hide,{passive:true,capture:true});
  window.addEventListener('resize',hide,{passive:true});
})();
