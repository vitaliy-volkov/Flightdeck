(function enhanceFlightdeck(){
  const badge=document.querySelector('.live');
  const stamp=document.createElement('small');
  stamp.id='lastUpdate'; stamp.textContent='—'; badge.append(stamp);
  const systemText={
    'Remaining decisions run automatically in full mode':'Оставшиеся решения принимаются автоматически в полном режиме',
    'Recorded':'Зафиксировано'
  };
  const eventText={run_initialized:'запуск создан',automatic_decision:'автоматическое решение',artifact_stored:'артефакт сохранён',phase_advanced:'переход фазы'};
  const statusText={'in-spec':'в спецификации',active:'активен',complete:'завершён',full:'полный',semi:'полуавтоматический',manual:'ручной',interview:'интервью'};
  const originalRender=window.render;
  window.render=function enhancedRender(data){
    originalRender(data);
    const currentLanguage=document.documentElement.lang;
    if(currentLanguage==='ru'){
      const mode=document.getElementById('mode'),status=document.getElementById('status');
      mode.textContent=(statusText[String(data.mode)]||data.mode).toUpperCase();
      status.textContent=(statusText[String(data.status)]||data.status).toUpperCase();
      document.getElementById('assumptions').textContent=(data.assumptions||[]).map(value=>systemText[value]||value).join(' · ')||'Нет';
      document.querySelectorAll('#requirements li').forEach(item=>{const value=item.lastElementChild;if(value&&statusText[value.textContent])value.textContent=statusText[value.textContent]});
      document.querySelectorAll('#events tr').forEach(row=>{const cells=row.querySelectorAll('td');if(cells[1]){const key=cells[1].textContent.trim().replaceAll(' ','_');cells[1].textContent=eventText[key]||cells[1].textContent}if(cells[2]&&systemText[cells[2].textContent.trim()])cells[2].textContent=systemText[cells[2].textContent.trim()]});
    }
    const updatedAt=data.dashboard&&data.dashboard.updated_at?new Date(data.dashboard.updated_at):new Date();
    stamp.textContent=(currentLanguage==='ru'?'обновлено ':'updated ')+updatedAt.toLocaleTimeString(currentLanguage==='ru'?'ru-RU':'en-GB');
    badge.classList.remove('live-changed');document.querySelector('.metrics').classList.remove('state-changed');
    requestAnimationFrame(()=>{badge.classList.add('live-changed');document.querySelector('.metrics').classList.add('state-changed')});
  };
})();
