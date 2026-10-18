'use strict';
window.renderMhizvaResult = function(result, openDoc) {
  const I=window.MhizvaI18n, t=I.t;
  const el=(tag, cls, text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;};
  const root=el('section','structured-result');
  const table=result.table;
  const rows=Array.isArray(table?.rows)?table.rows:[];
  const columns=Array.isArray(table?.columns)?table.columns:[];
  const rowSources=index=>Array.isArray(table?.sources?.[index])?table.sources[index].filter(s=>s&&typeof s.path==='string'):[];
  const sourcePanel=el('div','result-source-panel');sourcePanel.hidden=true;
  function sourcesFor(sources,label) {
    const unique=[...new Map(sources.map(source=>[source.path+'\n'+(source.quote||''),source])).values()];
    if(unique.length===1){openDoc(unique[0].path,unique[0].quote || '');return;}
    sourcePanel.hidden=false;sourcePanel.replaceChildren();
    const heading=el('h3','',t('sourceFor',{name:label}));heading.tabIndex=-1;sourcePanel.append(heading);
    if(!unique.length)sourcePanel.append(el('p','muted',t('noSource')));
    for(const source of unique){const button=el('button','source');button.type='button';button.append(el('strong','',source.path.split('/').pop().replace(/\.md$/i,'')),el('span','',source.quote || source.path));button.addEventListener('click',()=>openDoc(source.path,source.quote || ''));sourcePanel.append(button);}
    heading.focus();
  }
  function formatDate(value) {
    if(typeof value!=='string')return '';
    if(/^\d{4}-\d{2}-\d{2}$/.test(value))return new Intl.DateTimeFormat(I.locale,{year:'numeric',month:'short',day:'numeric',timeZone:'UTC'}).format(new Date(value+'T00:00:00Z'));
    const match=value.match(/^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{4})$/);
    if(match){const month=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'].indexOf(match[1]);return new Intl.DateTimeFormat(I.locale,{year:'numeric',month:'short',timeZone:'UTC'}).format(new Date(Date.UTC(Number(match[2]),month,1)));}
    return value;
  }
  const coverage=result.coverage;
  if(coverage){
    const wrap=el('div','result-coverage');
    const parts=[t('coverage',{used:coverage.documents_used ?? 0,total:coverage.documents_with_facts ?? 0})];
    if(coverage.date_range?.length===2)parts.push(coverage.date_range.map(formatDate).join(' – '));
    if(coverage.duplicates_merged)parts.push(t('duplicates',{count:coverage.duplicates_merged}));
    if(coverage.currency)parts.push(t('currency',{currency:coverage.currency}));
    wrap.append(el('p','',parts.join(' · ')));
    const notes=[];if(coverage.subset)notes.push(t('subset'));if(coverage.missing_dates)notes.push(t('missingDates',{count:coverage.missing_dates}));if(coverage.dates_from_document)notes.push(t('inferredDates',{count:coverage.dates_from_document}));
    if(notes.length)wrap.append(el('p','coverage-notes',notes.join(' · ')));
    root.append(wrap);
  }
  if(table&&columns.length){
    const toolbar=el('div','result-toolbar');toolbar.append(el('h3','',t(result.kind==='trend'?'trend':'table')));
    if(typeof result.id==='string'&&/^[a-f0-9]{12}$/i.test(result.id)) {
      const downloads=el('div','result-downloads');
      for(const format of ['xlsx','csv']) {
        const button=el('button','download-button',t(format));button.type='button';
        button.addEventListener('click',async()=>{
          button.disabled=true;button.textContent=t('downloading');
          try {
            const response=await fetch('/api/export?id='+encodeURIComponent(result.id)+'&format='+format);
            if(!response.ok)throw new Error('download');
            const url=URL.createObjectURL(await response.blob());const link=el('a');link.href=url;link.download='Mhizva.'+format;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
          }catch{const error=el('p','download-error',t('downloadError'));error.setAttribute('role','status');toolbar.querySelector('.download-error')?.remove();toolbar.append(error);}
          finally{button.disabled=false;button.textContent=t(format);}
        });downloads.append(button);
      }toolbar.append(downloads);
    }root.append(toolbar);
  }
  const chart=result.chart;
  if(chart&&Array.isArray(chart.points)) {
    const valid=chart.points.filter(p=>typeof p.value==='number'&&Number.isFinite(p.value));
    if(valid.length) {
      const wrap=el('div','result-chart-wrap');wrap.setAttribute('tabindex','0');wrap.setAttribute('aria-label',t('chart'));
      const count=chart.points.length,width=Math.max(640,count*(chart.type==='bar'?105:50)),height=300,left=78,right=24,top=22,bottom=70;
      const ns='http://www.w3.org/2000/svg';
      const svg=(tag,attrs={},text)=>{const node=document.createElementNS(ns,tag);for(const [key,value] of Object.entries(attrs))node.setAttribute(key,String(value));if(text!==undefined)node.textContent=text;return node;};
      const plot=svg('svg',{viewBox:'0 0 '+width+' '+height,width,height,role:'group','aria-label':t('chart')});
      plot.style.width=width+'px';plot.style.maxWidth='none';
      const low=valid.reduce((value,p)=>Math.min(value,p.value),0),high=valid.reduce((value,p)=>Math.max(value,p.value),0);const max=high===low?low+1:high;
      const y=value=>top+(max-value)/(max-low)*(height-top-bottom);
      const step=(width-left-right)/count;
      const x=index=>left+step*(index+.5);
      const number=new Intl.NumberFormat(I.locale,{maximumFractionDigits:2});
      for(let i=0;i<=4;i++) {const value=low+(max-low)*i/4;plot.append(svg('line',{x1:left,y1:y(value),x2:width-right,y2:y(value),class:'chart-grid'}),svg('text',{x:left-10,y:y(value)+4,'text-anchor':'end',class:'chart-tick'},new Intl.NumberFormat(I.locale,{notation:'compact',maximumFractionDigits:1}).format(value)));}
      plot.append(svg('text',{x:left,y:12,class:'chart-unit'},chart.y?.unit || chart.y?.name || ''));
      if(chart.type==='line') {
        let path='';let connected=false;
        chart.points.forEach((point,index)=>{if(typeof point.value!=='number'||!Number.isFinite(point.value)){connected=false;return;}path+=(connected?' L ':' M ')+x(index)+' '+y(point.value);connected=true;});
        plot.append(svg('path',{d:path,fill:'none',class:'chart-line'}));
      }
      chart.points.forEach((point,index)=>{
        const numeric=typeof point.value==='number'&&Number.isFinite(point.value);
        const valueLabel=numeric?number.format(point.value)+(chart.y?.unit?' '+chart.y.unit:''):t('missingValue');
        const label=t('chartValue',{name:point.x,value:valueLabel});
        if(numeric){
          const pointSources=(Array.isArray(point.rows)?point.rows:[]).flatMap(rowSources);
          const group=svg('g',{class:'chart-point',role:pointSources.length?'button':'group','aria-label':label});
          group.append(svg('title',{},label));
          if(pointSources.length){group.setAttribute('tabindex','0');group.addEventListener('click',()=>sourcesFor(pointSources,String(point.x)));group.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();sourcesFor(pointSources,String(point.x));}});}
          if(chart.type==='line')group.append(svg('circle',{cx:x(index),cy:y(point.value),r:5,class:'chart-dot'}));
          else group.append(svg('rect',{x:x(index)-Math.min(step*.3,32),y:Math.min(y(point.value),y(0)),width:Math.min(step*.6,64),height:Math.max(1,Math.abs(y(point.value)-y(0))),rx:3,class:'chart-bar'}));
          plot.append(group);
        }
        const labelText=String(point.x??'');const labelNode=svg('text',{x:x(index),y:height-bottom+22,'text-anchor':'middle',class:'chart-tick'},labelText.length>16?labelText.slice(0,15)+'…':labelText);labelNode.append(svg('title',{},labelText));plot.append(labelNode);
      });
      wrap.append(plot);root.append(wrap,el('p','chart-hint',t('chartHint')));
    }else root.append(el('p','muted',t('chartUnavailable')));
  }
  if(table&&columns.length) {
    const scroll=el('div','result-table-wrap');scroll.tabIndex=0;scroll.setAttribute('aria-label',t('table'));
    const grid=el('table','result-table');const head=el('thead'),headRow=el('tr'),body=el('tbody');head.append(headRow);grid.append(head,body);scroll.append(grid);root.append(scroll);
    let order=rows.map((_,index)=>index),sortColumn=-1,ascending=true,limit=100;
    const footer=el('div','table-footer');const counter=el('span');const more=el('button','download-button',t('showMore'));more.type='button';footer.append(counter,more);root.append(footer);
    function display(row,col) {
      const value=rows[row]?.[col],column=columns[col];
      if(value===null||value===undefined)return '—';
      if(I.language==='zh') {
        if(column.type==='money'&&typeof value==='number')return new Intl.NumberFormat(I.locale,{style:'currency',currency:column.currency || 'AUD',currencyDisplay:'code',maximumFractionDigits:2}).format(value);
        if(column.type==='percent'&&typeof value==='number')return (value>0?'+':'')+new Intl.NumberFormat(I.locale,{maximumFractionDigits:1}).format(value)+'%';
        if(column.type==='date')return formatDate(value);
        if(column.name==='Trend')return t(String(value));
      }
      return table.text?.[row]?.[col] ?? String(value);
    }
    function drawRows() {
      body.replaceChildren();
      for(const row of order.slice(0,limit)) {
        const tr=el('tr');
        columns.forEach((column,col)=>{const td=el('td',column.type==='money'||column.type==='percent'?'numeric':'');const text=display(row,col),sources=rowSources(row);if(sources.length){const button=el('button','source-cell',text);button.type='button';button.setAttribute('aria-label',t('cellSource',{value:text}));button.addEventListener('click',()=>sourcesFor(sources,text));td.append(button);}else td.textContent=text;if(rows[row]?.[col]===null)td.setAttribute('aria-label',t('missingValue'));tr.append(td);});
        body.append(tr);
      }
      if(!rows.length){const tr=el('tr'),td=el('td','muted',t('noRows'));td.colSpan=columns.length;tr.append(td);body.append(tr);}
      counter.textContent=t('showingRows',{shown:Math.min(limit,rows.length),total:rows.length});more.hidden=limit>=rows.length;
    }
    columns.forEach((column,index)=>{
      const th=el('th');th.scope='col';th.setAttribute('aria-sort','none');
      const name=t(column.name)+(column.type==='money'?' ('+(column.currency || 'AUD')+')':'');
      const button=el('button','sort-button',name+' ↕');button.type='button';button.setAttribute('aria-label',t('sortBy',{name}));
      button.addEventListener('click',()=>{
        ascending=sortColumn===index?!ascending:true;sortColumn=index;
        for(const header of headRow.children)header.setAttribute('aria-sort','none');th.setAttribute('aria-sort',ascending?'ascending':'descending');
        const comparable=value=>column.type==='date'&&typeof value==='string'&&!Number.isNaN(Date.parse(value))?Date.parse(value):value;
        order.sort((a,b)=>{let av=rows[a]?.[index],bv=rows[b]?.[index];if(av===null||av===undefined)return bv==null?a-b:1;if(bv===null||bv===undefined)return -1;av=comparable(av);bv=comparable(bv);const difference=typeof av==='number'&&typeof bv==='number'?av-bv:String(av).localeCompare(String(bv),I.locale,{numeric:true});return difference?(ascending?difference:-difference):a-b;});
        drawRows();
      });th.append(button);headRow.append(th);
    });
    more.addEventListener('click',()=>{limit+=100;drawRows();});drawRows();
  }
  root.append(sourcePanel);return root;
};
