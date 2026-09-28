/* 独立后台客户端：令牌只驻留内存；原始内容均经过转义后插入页面。 */
(() => {
  let csrfToken = '';
  let view = 'works';
  const pageSize = 30;
  let loadTimer;
  let loadSequence = 0;
  const loadingMarkup=`<div class="loading-scene" role="status" aria-live="polite"><div class="loading-mark" aria-hidden="true"><span class="loading-ring"></span><span class="loading-character">诗</span></div><p class="loading-title">正为你展卷</p><p class="loading-caption">笔墨轻启，诗意将至</p><div class="loading-rule" aria-hidden="true"><span></span></div></div>`;
  let offset = 0;
  let query = '';
  const $ = selector => document.querySelector(selector);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const labels = {works:'作品管理', authors:'作者资料', attributions:'署名消歧', materials:'内容审核', sources:'来源登记', audit:'操作记录'};
  const materialLabels={original:'作品原文',biography:'作者小传',translation:'作品译文',commentary:'作品赏析',pinyin:'逐字拼音'};
  function errorText(detail) {
    if (typeof detail==='string') return detail;
    if (Array.isArray(detail)) return detail.map(item=>item.msg?.replace(/^Value error,\s*/,'')||'请检查输入内容').join('；');
    return '请检查输入内容后重试';
  }
  function dialogError(error) {
    const box=$('#editor-error');
    if(box && $('#editor').open){box.textContent=error.message||String(error);box.hidden=false;box.scrollIntoView({block:'nearest',behavior:'smooth'});}
    else notify(error.message||String(error),true);
  }

  const notify = (message, error=false) => { $('#notice').textContent=message; $('#notice').className=error?'error':''; };

  async function api(path, options={}) {
    const response = await fetch('/admin/api'+path, {
      ...options,
      headers: {...(csrfToken && options.method && options.method !== 'GET' ? {'X-CSRF-Token':csrfToken} : {}), ...(options.body ? {'Content-Type':'application/json'} : {}), ...(options.headers||{})},
      cache:'no-store',
      credentials:'same-origin',
    });
    if (!response.ok) {
      let detail='操作失败';
      try { const data=await response.json(); detail=errorText(data.detail); } catch {}
      if (response.status===401) { csrfToken=''; $('#workspace').hidden=true; $('#login').hidden=false; }
      throw new Error(detail);
    }
    const data = await response.json();
    // 列表保持原数组格式，额外读取与当前筛选条件一致的总条数。
    if (options.withTotal) return {items:data, total:Number(response.headers.get('X-Total-Count') ?? 0)};
    return data;
  }
  const write = (path, method, data) => api(path, {method, ...(data ? {body:JSON.stringify(data)} : {})});

  function field(label, name, value='', options={}) {
    const attrs=`name="${esc(name)}" ${options.required?'required':''} ${options.reviewRequired?'data-review-required aria-required="false"':''}`;
    const marker=options.reviewRequired?'<span class="required-mark" aria-label="必填" hidden>*</span>':'';
    const caption=`<span class="field-caption">${esc(label)}${marker}</span>`;
    const help=options.help?`<small class="field-help">${esc(options.help)}</small>`:'';
    const cls=`class="${options.full?'full':''}"`;
    if (options.select) return `<label ${cls}>${caption}<select ${attrs}>${options.select.map(choice=>`<option value="${esc(choice[0])}" ${String(choice[0])===String(value)?'selected':''}>${esc(choice[1])}</option>`).join('')}</select>${help}</label>`;
    if (options.textarea) return `<label class="full">${caption}<textarea ${attrs}>${esc(value)}</textarea>${help}</label>`;
    return `<label ${cls}>${caption}<input ${attrs} value="${esc(value)}" type="${options.type||'text'}" ${options.placeholder?`placeholder="${esc(options.placeholder)}"`:''}>${help}</label>`;
  }
  function formData(form) { return Object.fromEntries(new FormData(form).entries()); }
  const editorStack = [];
  function closeEditor(all=false) {
    const dialog=$('#editor');
    if (!all && editorStack.length) {
      // 子表单退出时复用原 DOM，保留父表单的未保存输入、监听器及滚动位置。
      const previous=editorStack.pop();
      $('#editor-content').replaceChildren(previous.content);
      $('#editor-content').scrollTop=previous.scrollTop;
      const target=previous.focus && previous.content.contains(previous.focus)
        ? previous.focus : $('#edit-form input, #edit-form select, #edit-form textarea');
      target?.focus();
      return;
    }
    editorStack.length=0;
    if (dialog.open) dialog.close();
    $('#editor-content').replaceChildren();
  }
  function editor(title, markup, submit, extras='') {
    const dialog=$('#editor');
    const holder=$('#editor-content');
    if (dialog.open) {
      const parent=document.createElement('div');
      while(holder.firstChild) parent.append(holder.firstChild);
      editorStack.push({content:parent,focus:document.activeElement,scrollTop:holder.scrollTop});
    } else {
      editorStack.length=0;
    }
    // 原生 modal dialog 约束焦点；同一 dialog 内的子表单可逐层返回。
    holder.innerHTML=`<header class="editor-header"><h2 id="editor-title">${esc(title)}</h2><button type="button" id="close-editor" aria-label="关闭编辑窗口">×</button></header><p id="editor-error" role="alert" hidden></p><form id="edit-form">${markup}<div class="actions"><button class="primary" type="submit">保存</button><button type="button" id="cancel-edit">${editorStack.length?'返回上一层':'取消'}</button>${extras}</div></form>`;
    holder.scrollTop=0;
    $('#cancel-edit').onclick=()=>closeEditor();
    $('#close-editor').onclick=()=>closeEditor();
    $('#edit-form').onsubmit=async event=>{event.preventDefault();try{await submit(formData(event.currentTarget));}catch(err){dialogError(err);return;}closeEditor();notify('保存成功');load().catch(showError);};
    if (!dialog.open) dialog.showModal();
    $('#edit-form input, #edit-form select, #edit-form textarea')?.focus();
  }
  $('#editor').addEventListener('click', event=>{if(event.target===$('#editor'))closeEditor();});
  $('#editor').addEventListener('cancel', event=>{event.preventDefault();closeEditor();});
  $('#editor').addEventListener('close',()=>{editorStack.length=0;$('#editor-content').replaceChildren();});
  function workForm(item=null) {
    const v=item?.versions?.find(x=>x.is_current);
    editor(item?'修订作品（新增原文版本）':'新建作品',
      field('类别','genre',item?.genre||'tang_poem',{select:[['tang_poem','唐诗'],['song_poem','宋诗'],['song_ci','宋词']]})+
      field('作者原始署名','author_name',item?.author_name,{required:true})+
      (item?.author_id ? `<p class="identity-note">已关联规范人物（关联不可在原文修订中更改）</p>` : '')+
      field('诗题','title',v?.title||'')+field('词牌','rhythmic',v?.rhythmic||'')+
      field('正文（每行一段）','paragraphs',(v?.paragraphs||[]).join('\n'),{textarea:true,required:true})+
      field('标签（逗号分隔）','tags',(v?.tags||[]).join(', '),{full:true})+
      field('序言','prologue',v?.prologue||'',{textarea:true}),
      async raw=>{
        const data={genre:raw.genre,author_name:raw.author_name.trim(),author_id:null,
          title:raw.title.trim()||null,rhythmic:raw.rhythmic.trim()||null,prologue:raw.prologue.trim()||null,
          paragraphs:raw.paragraphs.split('\n').map(x=>x.trim()).filter(Boolean),
          tags:raw.tags.split(',').map(x=>x.trim()).filter(Boolean)};
        await write(item?`/works/${item.id}`:'/works',item?'PUT':'POST',data);
      },item?`<button type="button" class="danger" id="archive-work">${item.identity_status==='archived'?'恢复作品':'归档作品'}</button>`:'');
    if(item) $('#archive-work').onclick=async()=>{if(!confirm('归档会撤下全部原文；恢复后仍需重新审核。确定继续？'))return;try{await write(`/works/${item.id}${item.identity_status==='archived'?'/restore':''}`,item.identity_status==='archived'?'POST':'DELETE');closeEditor(true);await load();}catch(err){notify(err.message,true);}};
  }
  function authorForm(item=null) {
    editor(item?'编辑作者':'新建作者',
      field('姓名','canonical_name',item?.canonical_name,{required:true})+
      field('朝代','dynasty',item?.dynasty||'tang',{select:[['tang','唐'],['song','宋'],['other','其他']]})+
      field('身份状态','identity_status',item?.identity_status||'unverified',{select:[['unverified','未核验'],['verified','已核验'],['archived','已归档']]})+
      ['birth_year_min','birth_year_max','death_year_min','death_year_max'].map((key,i)=>field(['生年起','生年止','卒年起','卒年止'][i],key,item?.[key]??'',{type:'number'})).join(''),
      async raw=>{for(const key of ['birth_year_min','birth_year_max','death_year_min','death_year_max'])raw[key]=raw[key]?Number(raw[key]):null;
        await write(item?`/authors/${item.id}`:'/authors',item?'PUT':'POST',raw);},
      item?`<button type="button" class="danger" id="archive-author">归档作者</button>`:'');
    if(item) $('#archive-author').onclick=async()=>{if(!confirm('归档作者记录？作品署名仍会保留。'))return;try{await write(`/authors/${item.id}`,'DELETE');closeEditor(true);await load();}catch(err){notify(err.message,true);}};
  }
  function sourceForm(item=null) {
    editor(item?'编辑来源':'新建来源',field('来源键','key',item?.key,{required:true})+
      field('类型','kind',item?.kind||'reference',{required:true})+field('名称','title',item?.title,{required:true})+
      field('网址','url',item?.url||'',{full:true})+field('权利说明','license_note',item?.license_note||'',{textarea:true}),
      raw=>write(item?`/sources/${item.id}`:'/sources',item?'PUT':'POST',{
        ...raw,url:raw.url||null,license_note:raw.license_note||null}));
  }
  function reviewForm(material) {
    const kind=materialLabels[material.kind]||material.kind;
    const title=material.display_title||kind;
    editor(`审核 · ${title}`,`<div class="review-context"><span class="review-kind">${esc(kind)}</span><strong>${esc(title)}</strong>${material.creator?`<span>作者：${esc(material.creator)}</span>`:''}<span>来源：${esc(material.source_title||'未登记')}</span><span>材料编号：#${material.id} · 状态：${esc(material.status)}</span></div><details class="review-body"><summary>查看待审核内容</summary><pre>${esc(material.body||'该材料无可直接展示的正文，请先核对来源和对应版本。')}</pre></details>`+
      field('审核决定','decision','rejected',{required:true,select:[['rejected','拒绝'],['approved','批准公开'],['revoked','撤销批准']],help:'拒绝或撤销不会使材料对外可见。'})+
      field('权利依据','legal_basis','',{reviewRequired:true,placeholder:'例如：权利人明确授权本站展示',help:'说明为何有权公开；不能仅以原仓库采用 MIT 作为上游授权证据。'})+
      field('允许使用范围','permitted_scope','',{reviewRequired:true,select:[['','请选择使用范围'],['web','本站网页公开展示（web）']],help:'仅 web 表示可在本站网页展示；不包含转载、下载分发、第三方 API 或商业再授权。'})+
      field('证据位置（内部 URI）','evidence_uri','',{full:true,reviewRequired:true,placeholder:'例如：内部授权文件编号或可核查的公开许可链接',help:'填写授权书、许可说明或核权记录的位置；不能只填写无证据的结论。'})+
      field('授权到期时间','expires_at','',{type:'datetime-local',help:'没有明确到期日可留空；有期限时到期后自动停止公开。'})+
      field('审核说明','reason','',{textarea:true,help:'可记录拒绝原因、核查过程或需要后续处理的事项。'}),
      async raw=>{
        if(raw.decision==='approved'){
          const required=['legal_basis','permitted_scope','evidence_uri'];
          const missing=required.filter(key=>!raw[key]?.trim());
          if(missing.length){
            missing.forEach(key=>$('#edit-form [name="'+key+'"]').classList.add('invalid'));
            $('#edit-form [name="'+missing[0]+'"]').focus();
            throw new Error('批准公开前，请填写标 * 的权利依据、允许使用范围和证据位置。');
          }
          if(raw.permitted_scope!=='web')throw new Error('当前仅支持本站网页公开展示（web）。');
          if(!confirm('确认已核对权利依据、证据和允许范围，并批准此份内容公开？'))throw new Error('已取消审核，内容未发布。');
        }
        await write(`/materials/${material.id}/reviews`,'POST',{...raw,
          legal_basis:raw.legal_basis||null,permitted_scope:raw.permitted_scope||null,
          evidence_uri:raw.evidence_uri||null,expires_at:raw.expires_at?new Date(raw.expires_at).toISOString():null,
          reason:raw.reason||null});
      },
      material.kind!=='original'?`<button type="button" class="danger" id="withdraw-material">撤下材料</button>`:'');
    const decision=$('#edit-form [name="decision"]');
    const syncRequired=()=>{
      const approved=decision.value==='approved';
      $('#editor-error').hidden=true;
      document.querySelectorAll('#edit-form [data-review-required]').forEach(input=>{
        input.setAttribute('aria-required',String(approved));
        input.closest('label').querySelector('.required-mark').hidden=!approved;
        input.classList.remove('invalid');
      });
    };
    decision.onchange=syncRequired;
    syncRequired();
    document.querySelectorAll('#edit-form input, #edit-form select').forEach(input=>input.oninput=()=>input.classList.remove('invalid'));
    if(material.kind!=='original') $('#withdraw-material').onclick=async()=>{if(!confirm('确认撤下此材料？'))return;try{await write(`/materials/${material.id}`,'DELETE');closeEditor(true);await load();}catch(err){dialogError(err);}};
  }
  function extraForm(kind, ownerId) {
    let title,markup,body;
    if(kind==='biography') {title='新增作者小传';markup=field('简介正文','body','',{textarea:true,required:true})+field('摘要','summary','',{textarea:true})+field('来源 ID（可选）','source_id','',{type:'number'});body=x=>({body:x.body,summary:x.summary||null,source_id:x.source_id?Number(x.source_id):null});}
    if(kind==='commentary') {title='新增作品赏析';markup=field('标题','title')+field('评论者','commentator_name')+field('赏析正文','body','',{textarea:true,required:true})+field('来源 ID（可选）','source_id','',{type:'number'});body=x=>({body:x.body,title:x.title||null,commentator_name:x.commentator_name||null,source_id:x.source_id?Number(x.source_id):null});}
    if(kind==='translation') {title='新增作品译文';markup=field('语言代码','language_tag','en',{required:true})+field('译者','translator_name')+field('译文（每行一块）','blocks','',{textarea:true,required:true})+field('来源 ID（可选）','source_id','',{type:'number'});body=x=>({language_tag:x.language_tag,translator_name:x.translator_name||null,blocks:x.blocks.split('\n').filter(Boolean),source_id:x.source_id?Number(x.source_id):null});}
    editor(title,markup,async raw=>{await write(kind==='biography'?`/authors/${ownerId}/biographies`:`/works/${ownerId}/${kind==='commentary'?'commentaries':'translations'}`,'POST',body(raw));});
  }

  function workDateForm(workId, record=null) {
    editor(record?'修改创作时间':'新增创作时间说法',
      field('起始年份','year_start',record?.year_start??'',{type:'number',required:true})+
      field('结束年份','year_end',record?.year_end??'',{type:'number',required:true})+
      field('时间精度','date_precision',record?.date_precision||'range',{select:[['exact','确切年'],['approximate','约年'],['range','范围']]})+
      field('证据强度','confidence',record?.confidence||'low',{select:[['high','高'],['medium','中'],['low','低']]})+
      field('核验状态','review_status',record?.review_status||'pending',{select:[['pending','待核'],['reviewed','已核'],['rejected','不采纳']]})+
      field('是否首选','is_preferred',record?.is_preferred?'yes':'no',{select:[['no','否'],['yes','是（仅已核验）']]})+
      field('来源 ID（可选）','source_id',record?.source_id||'',{type:'number'})+
      field('考据依据','rationale',record?.rationale||'',{textarea:true}),
      raw=>write(`/works/${workId}/dates${record?`/${record.id}`:''}`,record?'PUT':'POST',{
        year_start:Number(raw.year_start),year_end:Number(raw.year_end),date_precision:raw.date_precision,
        confidence:raw.confidence,review_status:raw.review_status,is_preferred:raw.is_preferred==='yes',
        source_id:raw.source_id?Number(raw.source_id):null,rationale:raw.rationale||null}),
      record?'<button type="button" class="danger" id="retire-date">撤下年代</button>':'');
    if(record)$('#retire-date').onclick=async()=>{if(!confirm('撤下该年代说法并保留历史？'))return;try{await write(`/works/${workId}/dates/${record.id}`,'DELETE');closeEditor();notify('已撤下');await load();}catch(err){showError(err);}};
  }
  function eventForm(authorId, record=null) {
    editor(record?'修改生平事件':'新增生平事件',
      field('起始年份','year_start',record?.year_start??'',{type:'number',required:true})+
      field('结束年份','year_end',record?.year_end??'',{type:'number',required:true})+
      field('时间精度','date_precision',record?.date_precision||'range',{select:[['exact','确切年'],['approximate','约年'],['range','范围']]})+
      field('核验状态','review_status',record?.review_status||'pending',{select:[['pending','待核'],['reviewed','已核'],['rejected','不采纳']]})+
      field('来源 ID（可选）','source_id',record?.source_id||'',{type:'number'})+
      field('事件简述','event_label',record?.event_label||'',{textarea:true,required:true}),
      raw=>write(`/authors/${authorId}/events${record?`/${record.id}`:''}`,record?'PUT':'POST',{
        year_start:Number(raw.year_start),year_end:Number(raw.year_end),date_precision:raw.date_precision,
        review_status:raw.review_status,source_id:raw.source_id?Number(raw.source_id):null,event_label:raw.event_label}),
      record?'<button type="button" class="danger" id="retire-event">撤下事件</button>':'');
    if(record)$('#retire-event').onclick=async()=>{if(!confirm('撤下该生平事件并保留历史？'))return;try{await write(`/authors/${authorId}/events/${record.id}`,'DELETE');closeEditor();notify('已撤下');await load();}catch(err){showError(err);}};
  }
  function pinyinForm(workId) {
    editor('新增逐字拼音标注',field('拼音体系','romanization','hanyu-pinyin')+
      field('来源 ID（可选）','source_id','',{type:'number'})+
      field('JSON 数组：paragraph_index、char_index、character、pinyin','tokens','[{"paragraph_index":0,"char_index":0,"character":"春","pinyin":"chūn"}]',{textarea:true,required:true}),
      raw=>write(`/works/${workId}/pinyin`,'POST',{romanization:raw.romanization,
        source_id:raw.source_id?Number(raw.source_id):null,tokens:JSON.parse(raw.tokens)}));
  }
  function attributionForm(item) {
    editor(`署名消歧：${item.original_name}`,`<p>来源：${esc(item.source_path)} · 第 ${item.source_index} 条<br>当前人物 ID：${esc(item.author_id||'尚未关联')}</p>`+
      field('目标规范人物 ID（清空则解除关联）','author_id',item.author_id||'',{type:'number'}),
      raw=>api(`/attributions/${item.id}?${new URLSearchParams(raw)}`,{method:'PUT'}));
  }

  function renderPagination(total) {
    const root=$('#pagination');
    const pages=Math.max(1,Math.ceil(total/pageSize));
    const current=Math.floor(offset/pageSize)+1;
    root.hidden=false;
    root.innerHTML=`<span class="page-total">共 ${total.toLocaleString()} 条</span><div class="page-actions"><button type="button" id="page-prev" ${current===1?'disabled':''}>上一页</button><span>第 <strong>${current}</strong> / ${pages.toLocaleString()} 页</span><button type="button" id="page-next" ${current>=pages?'disabled':''}>下一页</button><form id="page-jump"><label for="page-number">跳转到</label><input id="page-number" type="number" inputmode="numeric" min="1" max="${pages}" value="${current}" required aria-label="目标页码"><span>页</span><button type="submit">跳转</button></form></div>`;
    $('#page-prev').onclick=()=>{offset-=pageSize;load().catch(showError);};
    $('#page-next').onclick=()=>{offset+=pageSize;load().catch(showError);};
    $('#page-jump').onsubmit=event=>{
      event.preventDefault();const input=$('#page-number');
      if(!input.reportValidity())return;
      const target=Number(input.value);
      if(!Number.isInteger(target)||target<1||target>pages)return;
      if(target!==current){offset=(target-1)*pageSize;load().catch(showError);}
    };
  }

  async function load() {
    notify(''); $('#view-title').textContent=labels[view];
    $('#controls').innerHTML='';
    const sequence=++loadSequence;
    clearTimeout(loadTimer);
    // 短请求不闪烁；超过一瞬才以柔和的展卷动画代替硬切换文字。
    loadTimer=setTimeout(()=>{if(sequence===loadSequence){$('#records').innerHTML=loadingMarkup;$('#pagination').hidden=true;}},180);
    const overview=await api('/overview'); $('#stats').textContent=`作品 ${overview.works.toLocaleString()} · 作者 ${overview.authors.toLocaleString()} · 待审 ${overview.pending.toLocaleString()}`;
    if(view!=='audit') {
      if(view==='materials') {
        $('#controls').innerHTML=`<label class="status-filter">材料状态 <select id="material-status"><option value="staged">待审</option><option value="published">已发布</option><option value="withdrawn">已撤下</option><option value="all">全部</option></select></label>`;
        $('#material-status').value=query||'staged';
        $('#material-status').onchange=()=>{query=$('#material-status').value;offset=0;load().catch(showError);};
      } else {
        $('#controls').innerHTML=`<input id="filter" aria-label="筛选列表" placeholder="${view==='sources'?'按来源名称或来源键查找':'按作者或作品名称查找'}" value="${esc(query)}"><button id="apply-filter">查找</button>${query?'<button id="clear-filter" type="button">清除筛选</button>':''}${view==='works'||view==='authors'||view==='sources'?'<button type="button" id="new-record" class="new-record">＋ 新建</button>':''}`;
        const filter=$('#filter');
        const originalPlaceholder=filter.placeholder;
        const resetFilterValidation=()=>{
          filter.classList.remove('invalid');
          filter.removeAttribute('aria-invalid');
          filter.placeholder=originalPlaceholder;
        };
        // 聚焦后恢复原提示；校验错误仅在空值提交后显示于输入框自身。
        filter.addEventListener('focus',resetFilterValidation);
        filter.addEventListener('input',resetFilterValidation);
        const applyFilter=()=>{
          const value=filter.value.trim();
          if(!value){
            filter.value='';
            filter.classList.add('invalid');
            filter.setAttribute('aria-invalid','true');
            filter.placeholder='请输入检索关键词';
            return;
          }
          resetFilterValidation();
          if(value===query){notify('当前已是该关键词的检索结果');return;}
          query=value;offset=0;load().catch(showError);
        };
        $('#apply-filter').onclick=applyFilter;
        filter.onkeydown=e=>{if(e.key==='Enter')applyFilter();};
        const clearFilter=$('#clear-filter');if(clearFilter)clearFilter.onclick=()=>{query='';offset=0;load().catch(showError);};
        const newRecord=$('#new-record'); if(newRecord)newRecord.onclick=()=>({works:workForm,authors:authorForm,sources:sourceForm}[view])();
      }
    }
    const queryString=new URLSearchParams({limit:String(pageSize),offset:String(offset)});
    if(view==='materials')queryString.set('status',query||'staged');
    else if(view!=='audit')queryString.set('q',query);
    const {items,total}=await api(`/${view}?${queryString}`,{withTotal:true});
    if(sequence!==loadSequence)return;
    clearTimeout(loadTimer);
    const pages=Math.max(1,Math.ceil(total/pageSize));
    // 列表在别的窗口发生变动时收敛过大的页码，避免空白页。
    if(offset >= total && offset !== 0) {offset=(pages-1)*pageSize;return load();}
    renderPagination(total);
    const root=$('#records');root.innerHTML='';root.classList.remove('results-ready');void root.offsetWidth;root.classList.add('results-ready');
    if(!items.length){root.innerHTML='<div class="empty">暂无记录；可以调整筛选条件或新建资料。</div>';return;}
    for(const row of items){
      const element=document.createElement('div');element.className='record';
      const main=document.createElement('div');const name=document.createElement('strong');
      name.textContent=view==='works'?row.title:view==='authors'?row.name:view==='sources'?row.title:view==='materials'?`${row.display_title||materialLabels[row.kind]||row.kind}`:view==='attributions'?`${row.original_name} · #${row.id}`:`${row.action_label} · ${row.target_label}`;
      const info=document.createElement('small');info.textContent=view==='works'?`${row.author} · ${row.genre} · 版本 ${row.version} · ${row.status}`:view==='authors'?`${row.dynasty} · ${row.identity_status}`:view==='sources'?row.key:view==='materials'?`${materialLabels[row.kind]||row.kind} · ${row.creator||'作者未详'} · ${row.source_title||'来源未详'} · #${row.id}`:view==='attributions'?`${row.match_status} · 人物 ID ${row.author_id||'未关联'} · ${row.source_path}`:`${new Date(row.created_at).toLocaleString('zh-CN',{dateStyle:'medium',timeStyle:'short'})} · 操作人：${row.actor}`;
      main.append(name,info);
      if(view==='audit'){
        const details=document.createElement('details');details.className='audit-details';
        const summary=document.createElement('summary');summary.textContent='查看技术记录';
        const raw=document.createElement('pre');raw.textContent=JSON.stringify({记录编号:row.id,操作:row.action,对象类型:row.entity_type,对象编号:row.entity_id,原始摘要:row.summary},null,2);
        details.append(summary,raw);main.append(details);
      }
      element.append(main);
      if(view!=='audit'){
        const btn=document.createElement('button');btn.textContent=view==='materials'?'审核':'查看 / 编辑';
        btn.onclick=async()=>{try{
          if(view==='works'){const item=await api(`/works/${row.id}`);workForm(item);
            const actions=$('#edit-form .actions');
            for(const [label,handler] of [['新增赏析',()=>extraForm('commentary',row.id)],['新增译文',()=>extraForm('translation',row.id)],['新增拼音',()=>pinyinForm(row.id)],['新增年代',()=>workDateForm(row.id)]]){
              const b=document.createElement('button');b.type='button';b.textContent=label;b.onclick=handler;actions.append(b);
            }
            const dates=await api(`/works/${row.id}/dates`);
            for(const record of dates){const b=document.createElement('button');b.type='button';b.textContent=`编辑年代 ${record.year_start}–${record.year_end}`;b.onclick=()=>workDateForm(row.id,record);actions.append(b);}
          }
          if(view==='authors'){const item=await api(`/authors/${row.id}`);authorForm(item);const extra=document.createElement('button');extra.type='button';extra.textContent='新增小传';extra.onclick=()=>extraForm('biography',row.id);$('#edit-form .actions').append(extra);const event=document.createElement('button');event.type='button';event.textContent='新增生平事件';event.onclick=()=>eventForm(row.id);$('#edit-form .actions').append(event);const events=await api(`/authors/${row.id}/events`);for(const record of events){const b=document.createElement('button');b.type='button';b.textContent=`编辑事件 ${record.year_start}`;b.onclick=()=>eventForm(row.id,record);$('#edit-form .actions').append(b);}}
          if(view==='sources')sourceForm(row);
          if(view==='materials')reviewForm(await api(`/materials/${row.id}`));
          if(view==='attributions')attributionForm(row);
        }catch(err){showError(err);}};element.append(btn);
      }
      root.append(element);
    }
  }
  const showError=error=>{
    clearTimeout(loadTimer);
    const root=$('#records');
    if(root.querySelector('.loading-scene'))root.innerHTML='<div class="empty">读取失败，请重新尝试。</div>';
    notify(error.message||String(error),true);
  };
  $('#login-form').onsubmit=async event=>{event.preventDefault();try{
    const result=await api('/login',{method:'POST',body:JSON.stringify({username:$('#username').value,password:$('#password').value})});
    csrfToken=result.csrf_token;$('#password').value='';$('#login').hidden=true;$('#workspace').hidden=false;await load();
  }catch(err){showError(err);}};
  const logoutDialog=$('#logout-confirm');
  $('#logout').onclick=()=>{if(!logoutDialog.open)logoutDialog.showModal();};
  $('#cancel-logout').onclick=()=>logoutDialog.close();
  $('#confirm-logout').onclick=async()=>{
    const button=$('#confirm-logout');button.disabled=true;
    try{
      await api('/logout',{method:'POST'});
      logoutDialog.close();csrfToken='';closeEditor(true);
      $('#workspace').hidden=true;$('#login').hidden=false;notify('已退出');
    }catch(err){showError(err);}finally{button.disabled=false;}
  };
  api('/session').then(async data=>{csrfToken=data.csrf_token;$('#login').hidden=true;$('#workspace').hidden=false;await load();}).catch(()=>{});
  document.querySelectorAll('[data-view]').forEach(button=>button.onclick=()=>{view=button.dataset.view;offset=0;query='';closeEditor(true);document.querySelectorAll('[data-view]').forEach(x=>x.classList.toggle('active',x===button));load().catch(showError);});
})();
