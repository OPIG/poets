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
  const archiveFilters={works:'all',authors:'all'};
  const $ = selector => document.querySelector(selector);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const labels = {works:'作品管理', authors:'作者资料', attributions:'署名消歧', materials:'内容审核', sources:'来源登记', audit:'操作记录'};
  const materialLabels={original:'作品原文',biography:'作者小传',translation:'作品译文',commentary:'作品赏析',pinyin:'逐字拼音'};
  const dynastyLabels={tang:'唐代',song:'宋代',other:'其他'};
  const genreLabels={tang_poem:'唐诗',song_poem:'宋诗',song_ci:'宋词'};
  const authorStatusLabels={unverified:'人物身份待考证',verified:'人物身份已核对',archived:'人物资料已归档'};
  const workStatusLabels={normal:'在库（未归档）',archived:'已归档（查阅页隐藏）'};

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
    $('#edit-form').onsubmit=async event=>{event.preventDefault();try{await submit(formData(event.currentTarget));}catch(err){dialogError(err);return;}closeEditor();notify('保存成功');if($('#editor').open && $('#author-biographies'))refreshAuthorBiographies(Number($('#author-biographies').dataset.authorId)).catch(dialogError);if($('#editor').open && $('#author-events'))refreshAuthorEvents(Number($('#author-events').dataset.authorId)).catch(dialogError);if($('#editor').open && $('#work-editorial'))refreshWorkEditorial(Number($('#work-editorial').dataset.workId)).catch(dialogError);load().catch(showError);};
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
      field('作品标签（可选）','tags',(v?.tags||[]).join(', '),{full:true,placeholder:'例如：春天, 送别, 乐府',help:'用于按主题、体裁或选本检索；多个标签用英文逗号分隔。不填诗题、作者、原文或未经核实的年代。'})+
      field('序言','prologue',v?.prologue||'',{textarea:true}),
      async raw=>{
        const data={genre:raw.genre,author_name:raw.author_name.trim(),author_id:null,
          title:raw.title.trim()||null,rhythmic:raw.rhythmic.trim()||null,prologue:raw.prologue.trim()||null,
          paragraphs:raw.paragraphs.split('\n').map(x=>x.trim()).filter(Boolean),
          tags:raw.tags.split(',').map(x=>x.trim()).filter(Boolean)};
        await write(item?`/works/${item.id}`:'/works',item?'PUT':'POST',data);
      },item?item.identity_status==='archived'
        ?'<button type="button" class="restore-action" id="restore-work">恢复作品</button>'
        :'<button type="button" class="danger" id="archive-work">归档并撤下作品</button>':'');
    if(item){
      const section=document.createElement('div');section.id='work-editorial';section.className='work-editorial';
      $('#edit-form .actions').before(section);
      refreshWorkEditorial(item.id).catch(dialogError);
    }
    if(item?.identity_status==='archived') $('#restore-work').onclick=async()=>{
      if(!confirm('恢复后作品会回到在库状态，但不会自动公开；原文需重新完成权利审核。确定恢复？'))return;
      try{await write(`/works/${item.id}/restore`,'POST');closeEditor(true);await load();}catch(err){dialogError(err);}
    };
    else if(item) $('#archive-work').onclick=async()=>{
      if(!confirm('归档后作品会从查阅页隐藏，原文版本和审核历史仍保留。确定归档？'))return;
      try{await write(`/works/${item.id}`,'DELETE');closeEditor(true);await load();}catch(err){dialogError(err);}
    };
  }
  const biographyStatus={staged:'待审核',published:'已发布',withdrawn:'已撤下'};
  const biographyOrigin={imported:'原仓库导入',editorial:'人工编辑'};
  function renderBiographyVersions(item) {
    const holder=$('#author-biographies');
    if(!holder)return;
    holder.dataset.authorId=item.id;
    holder.replaceChildren();
    const heading=document.createElement('h3');heading.textContent=`已有小传（${item.biographies.length}）`;
    holder.append(heading);
    const intro=document.createElement('p');intro.className='bio-intro';
    intro.textContent='旧版只作留存；修订会新增一份待审核小传，不会覆盖导入原文或自动撤下旧版。';
    holder.append(intro);
    if(!item.biographies.length){const empty=document.createElement('p');empty.className='bio-empty';empty.textContent='暂无小传。可点击下方“新增小传”编写。';holder.append(empty);}
    for(const bio of item.biographies){
      const article=document.createElement('article');article.className='bio-edition';
      const label=document.createElement('strong');label.textContent=`小传 #${bio.id} · ${biographyStatus[bio.status]||bio.status}`;
      const meta=document.createElement('p');meta.className='bio-meta';
      meta.textContent=`${biographyOrigin[bio.origin_type]||bio.origin_type} · ${bio.source_title||'来源待补充'}${bio.revises_biography_id?' · 修订自 #'+bio.revises_biography_id:''}`;
      const body=document.createElement('details');const summary=document.createElement('summary');summary.textContent='查看小传全文';
      const text=document.createElement('p');text.textContent=bio.body;body.append(summary,text);
      article.append(label,meta,body);
      if(bio.source_path){const source=document.createElement('p');source.className='bio-source';
        source.textContent=`源文件：${bio.source_path} · 第 ${bio.source_index+1} 条`;
        article.append(source);
        if(bio.source_file_url){const link=document.createElement('a');link.href=bio.source_file_url;link.target='_blank';link.rel='noopener noreferrer';link.textContent='核对固定提交中的来源文件 ↗';article.append(link);}
      }
      const actions=document.createElement('div');actions.className='bio-actions';
      const revise=document.createElement('button');revise.type='button';revise.textContent='基于此版修订';
      revise.onclick=()=>extraForm('biography',item.id,bio);actions.append(revise);
      if(bio.status!=='withdrawn'){
        const withdraw=document.createElement('button');withdraw.type='button';withdraw.className='danger';withdraw.textContent='撤下此版';
        withdraw.onclick=async()=>{
          if(!confirm(`确认撤下小传 #${bio.id}？此操作独立于修订，新版仍须审核。`))return;
          try{await write(`/materials/${bio.material_id}`,'DELETE');notify('旧版已撤下');await refreshAuthorBiographies(item.id);await load();}
          catch(error){dialogError(error);}
        };actions.append(withdraw);
      }
      article.append(actions);holder.append(article);
    }
    const add=document.createElement('button');add.type='button';add.className='section-add';add.textContent='＋ 新增小传';
    add.onclick=()=>extraForm('biography',item.id);holder.append(add);
  }
  async function refreshAuthorBiographies(authorId){
    const data=await api(`/authors/${authorId}`);
    renderBiographyVersions(data);
  }
  function renderAuthorEvents(authorId, events) {
    const holder=$('#author-events');
    if(!holder)return;
    holder.dataset.authorId=authorId;
    holder.replaceChildren();
    const heading=document.createElement('h3');heading.textContent=`已有生平事件（${events.length}）`;
    holder.append(heading);
    if(!events.length){
      const empty=document.createElement('p');empty.className='bio-empty';
      empty.textContent='暂无经过整理的生平事件。可在下方添加有依据的时间与经历。';holder.append(empty);
    }
    for(const record of events){
      const article=document.createElement('article');article.className='bio-edition event-edition';
      const title=document.createElement('strong');
      title.textContent=`${record.year_start===record.year_end?record.year_start:`${record.year_start}—${record.year_end}`}年 · ${record.event_label}`;
      const meta=document.createElement('p');meta.className='bio-meta';
      const status={pending:'待核对',reviewed:'已核对',rejected:'已撤下'};
      const precision={exact:'确切年',approximate:'约年',range:'时间范围'};
      meta.textContent=`${precision[record.date_precision]||record.date_precision} · ${status[record.review_status]||record.review_status} · 来源记录 #${record.source_id}`;
      article.append(title,meta);
      const actions=document.createElement('div');actions.className='bio-actions';
      const edit=document.createElement('button');edit.type='button';edit.textContent='编辑事件';
      edit.onclick=()=>eventForm(authorId,record);actions.append(edit);
      article.append(actions);holder.append(article);
    }
    const add=document.createElement('button');add.type='button';add.className='section-add';
    add.textContent='＋ 新增生平事件';add.onclick=()=>eventForm(authorId);
    holder.append(add);
  }
  async function refreshAuthorEvents(authorId){
    const events=await api(`/authors/${authorId}/events`);
    renderAuthorEvents(authorId,events);
  }
  function authorForm(item=null) {
    editor(item?'编辑作者':'新建作者',
      field('姓名','canonical_name',item?.canonical_name,{required:true})+
      field('朝代','dynasty',item?.dynasty||'tang',{select:[['tang','唐'],['song','宋'],['other','其他']]})+
      field('人物身份核对状态','identity_status',item?.identity_status||'unverified',{select:[['unverified','待考证'],['verified','已核对'],['archived','已归档']],help:'待考证表示资料由源数据导入，尚未人工确认是否为同一历史人物。'})+
      ['birth_year_min','birth_year_max','death_year_min','death_year_max'].map((key,i)=>field(['生年起','生年止','卒年起','卒年止'][i],key,item?.[key]??'',{type:'number'})).join(''),
      async raw=>{for(const key of ['birth_year_min','birth_year_max','death_year_min','death_year_max'])raw[key]=raw[key]?Number(raw[key]):null;
        await write(item?`/authors/${item.id}`:'/authors',item?'PUT':'POST',raw);},
      item?item.identity_status==='archived'
        ?'<button type="button" class="restore-action" id="restore-author">恢复人物资料</button>'
        :'<button type="button" class="danger" id="archive-author">归档人物资料</button>':'');
    if(item){
      const section=document.createElement('section');section.id='author-biographies';section.className='author-biographies';
      const eventsSection=document.createElement('section');eventsSection.id='author-events';eventsSection.className='author-biographies author-events';
      $('#edit-form .actions').before(section,eventsSection);
      renderBiographyVersions(item);
      refreshAuthorEvents(item.id).catch(dialogError);
    }
    if(item?.identity_status==='archived') $('#restore-author').onclick=async()=>{
      if(!confirm('恢复人物资料后身份状态为“待考证”，原始作品署名不变。确定恢复？'))return;
      try{await write(`/authors/${item.id}/restore`,'POST');closeEditor(true);await load();}catch(err){dialogError(err);}
    };
    else if(item) $('#archive-author').onclick=async()=>{
      if(!confirm('归档后人物资料和小传从查阅页隐藏，不再允许新作品关联；原始署名与历史资料仍保留。确定归档？'))return;
      try{await write(`/authors/${item.id}`,'DELETE');closeEditor(true);await load();}catch(err){dialogError(err);}
    };
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
  function extraForm(kind, ownerId, sourceBiography=null, sourceItem=null) {
    let title,markup,body;
    if(kind==='biography') {title=sourceBiography?`修订小传 #${sourceBiography.id}`:'新增作者小传';markup=field('简介正文','body',sourceBiography?.body||'',{textarea:true,required:true})+field('摘要','summary',sourceBiography?.summary||'',{textarea:true})+field('新内容来源 ID（可选）','source_id','',{type:'number',help:'留空表示诗卷人工校订。导入原文的来源保持原样，不自动继承转载许可。'});body=x=>({body:x.body,summary:x.summary||null,source_id:x.source_id?Number(x.source_id):null});}
    if(kind==='commentary') {title=sourceItem?`修订赏析 #${sourceItem.id}`:'新增作品赏析';markup=field('标题','title',sourceItem?.title||'')+field('评论者','commentator_name',sourceItem?.commentator_name||'')+field('赏析正文','body',sourceItem?.body||'',{textarea:true,required:true})+field('来源 ID（可选）','source_id','',{type:'number'});body=x=>({body:x.body,title:x.title||null,commentator_name:x.commentator_name||null,source_id:x.source_id?Number(x.source_id):null});}
    if(kind==='translation') {title=sourceItem?`修订译文 #${sourceItem.id}`:'新增作品译文';markup=field('语言代码','language_tag',sourceItem?.language_tag||'en',{required:true})+field('译者','translator_name',sourceItem?.translator_name||'')+field('译文（每行一块）','blocks',(sourceItem?.blocks||[]).join('\n'),{textarea:true,required:true})+field('来源 ID（可选）','source_id','',{type:'number'});body=x=>({language_tag:x.language_tag,translator_name:x.translator_name||null,blocks:x.blocks.split('\n').filter(Boolean),source_id:x.source_id?Number(x.source_id):null});}
    editor(title,markup,async raw=>{
      const path=kind==='biography'?`/authors/${ownerId}/biographies${sourceBiography?`/${sourceBiography.id}/revisions`:''}`:
        `/works/${ownerId}/${kind==='commentary'?'commentaries':'translations'}${sourceItem?`/${sourceItem.id}/revisions`:''}`;
      await write(path,'POST',body(raw));
    });
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
  function pinyinForm(workId, record=null) {
    editor(record?`修订拼音 #${record.id}`:'新增逐字拼音标注',field('拼音体系','romanization',record?.romanization||'hanyu-pinyin')+
      field('来源 ID（可选）','source_id','',{type:'number'})+
      field('JSON 数组：paragraph_index、char_index、character、pinyin','tokens',record?JSON.stringify(record.tokens,null,2):'[{"paragraph_index":0,"char_index":0,"character":"春","pinyin":"chūn"}]',{textarea:true,required:true}),
      raw=>write(`/works/${workId}/pinyin${record?`/${record.id}/revisions`:''}`,'POST',{romanization:raw.romanization,
        source_id:raw.source_id?Number(raw.source_id):null,tokens:JSON.parse(raw.tokens)}));
  }
  const editorialKinds=[
    ['commentaries','已有赏析','＋ 新增赏析'],
    ['translations','已有译文','＋ 新增译文'],
    ['pinyin','已有拼音','＋ 新增拼音'],
    ['dates','已有创作年代','＋ 新增年代'],
  ];
  function renderWorkEditorial(workId, data) {
    const root=$('#work-editorial');if(!root)return;
    root.dataset.workId=workId;root.replaceChildren();
    for(const [key,label,addLabel] of editorialKinds){
      const section=document.createElement('section');section.className='author-biographies work-editorial-section';
      const title=document.createElement('h3');title.textContent=`${label}（${data[key].length}）`;section.append(title);
      if(!data[key].length){const empty=document.createElement('p');empty.className='bio-empty';empty.textContent='暂无内容；新增后仍需单独审核才可公开。';section.append(empty);}
      for(const item of data[key]){
        const article=document.createElement('article');article.className='bio-edition';
        const name=document.createElement('strong');
        name.textContent=key==='commentaries'?(item.title||'作品赏析'):
          key==='translations'?`${item.language_tag} 译文`:
          key==='pinyin'?`${item.romanization} 拼音`:
          `${item.year_start}${item.year_end!==item.year_start?'—'+item.year_end:''} 年`;
        const meta=document.createElement('p');meta.className='bio-meta';
        meta.textContent=key==='dates'?`${item.review_status} · 来源 #${item.source_id}`:
          `${biographyStatus[item.status]||item.status} · ${item.source_title||'来源待补充'}${item.revises_id?' · 修订自 #'+item.revises_id:''}`;
        article.append(name,meta);
        const detail=document.createElement('details');const summary=document.createElement('summary');
        summary.textContent='查看内容';const text=document.createElement('p');
        text.textContent=key==='commentaries'?item.body:key==='translations'?item.blocks.join('\n'):
          key==='pinyin'?JSON.stringify(item.tokens,null,2):item.rationale||'暂无考据说明';
        detail.append(summary,text);article.append(detail);
        const actions=document.createElement('div');actions.className='bio-actions';
        const edit=document.createElement('button');edit.type='button';edit.textContent=key==='dates'?'编辑年代':'基于此版修订';
        edit.onclick=()=>key==='dates'?workDateForm(workId,item):key==='pinyin'?pinyinForm(workId,item):
          extraForm(key==='commentaries'?'commentary':'translation',workId,null,item);
        actions.append(edit);
        if(key!=='dates'&&item.status!=='withdrawn'){
          const withdraw=document.createElement('button');withdraw.type='button';withdraw.className='danger';withdraw.textContent='撤下此版';
          withdraw.onclick=async()=>{
            if(!confirm('撤下该版本？原始记录仍会保留，新版本须单独审核。'))return;
            try{await write(`/materials/${item.material_id}`,'DELETE');await refreshWorkEditorial(workId);await load();}
            catch(error){dialogError(error);}
          };actions.append(withdraw);
        }
        article.append(actions);section.append(article);
      }
      const add=document.createElement('button');add.type='button';add.className='section-add';add.textContent=addLabel;
      add.onclick=()=>key==='dates'?workDateForm(workId):key==='pinyin'?pinyinForm(workId):
        extraForm(key==='commentaries'?'commentary':'translation',workId);
      section.append(add);root.append(section);
    }
  }
  async function refreshWorkEditorial(workId){
    const data=await api(`/works/${workId}/editorial`);
    renderWorkEditorial(workId,data);
  }
  function attributionForm(item) {
    const linked=item.linked_author_name?`${item.linked_author_name}（${item.linked_author_dynasty||'朝代未详'}）`:'尚未关联';
    const source=`${item.collection} · 第 ${item.source_index+1} 条`;
    const biography=item.raw_biography||item.raw_short_biography||'来源中暂无生平描述，请额外核对。';
    editor(`核对署名 · ${item.original_name}`,
      `<div class="attribution-intro"><p><strong>原始署名：</strong>${esc(item.original_name)}</p><p><strong>资料集：</strong>${esc(source)}</p><p><strong>当前关联：</strong>${esc(linked)}</p></div>`+
      `<details class="audit-details"><summary>查看原始来源与生平资料</summary><p>文件：${esc(item.source_path)}</p><p>原始 ID：${esc(item.original_id||'无')}</p><pre>${esc(biography)}</pre></details>`+
      `<div class="candidate-search"><label for="candidate-keyword">寻找已存在的作者人物</label><div><input id="candidate-keyword" type="search" value="${esc(item.original_name)}" maxlength="80" placeholder="输入姓名查找人物"><button type="button" id="candidate-search-button">查找作者</button></div><small>姓名相同或繁简相似不代表同一人。请核对原始资料与候选人物生平后再关联。</small></div><div id="candidate-results" role="status" aria-live="polite"></div>`,
      async()=>{},item.author_id?'<button type="button" class="danger" id="unlink-author">解除人物关联</button>':'');
    // 人物主键只用于内部提交；不允许管理员凭数字盲填或修改主键。
    $('#edit-form .actions button[type="submit"]').remove();
    $('#cancel-edit').textContent='返回列表';
    const results=$('#candidate-results');
    $('#candidate-search-button').onclick=async()=>{
      const keyword=$('#candidate-keyword').value.trim();
      if(!keyword){results.textContent='请输入作者姓名后查找。';return;}
      results.textContent='正在查找候选人物…';
      try{
        const candidates=await api(`/authors?${new URLSearchParams({q:keyword,limit:'20'})}`);
        results.replaceChildren();
        if(!candidates.length){results.textContent='未找到已建立的人物。可先在“作者资料”中建立并核对人物。';return;}
        for(const candidate of candidates){
          const row=document.createElement('div');row.className='candidate-row';
          const text=document.createElement('span');
          text.textContent=`${candidate.name} · ${candidate.dynasty} · ${candidate.identity_status}`;
          const button=document.createElement('button');button.type='button';button.textContent='核对并选择';
          button.onclick=async()=>{
            try{
              const detail=await api(`/authors/${candidate.id}`);
              const bio=detail.biographies?.[0]?.body?.slice(0,300)||'该人物暂无已录入的小传。';
              if(!confirm(`原始署名：${item.original_name}\n资料集：${source}\n\n拟关联人物：${candidate.name}（${candidate.dynasty}）\n人物资料：${bio}\n\n确认考证为同一人并建立关联？`))return;
              await api(`/attributions/${item.id}?${new URLSearchParams({author_id:String(candidate.id)})}`,{method:'PUT'});
              closeEditor(true);notify(`已将“${item.original_name}”关联到“${candidate.name}”`);await load();
            }catch(err){dialogError(err);}
          };
          row.append(text,button);results.append(row);
        }
      }catch(err){dialogError(err);}
    };
    $('#candidate-keyword').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();$('#candidate-search-button').click();}};
    if(item.author_id)$('#unlink-author').onclick=async()=>{
      if(!confirm(`确认解除“${item.original_name}”与“${linked}”的关联？原始资料仍会保留。`))return;
      try{await api(`/attributions/${item.id}`,{method:'PUT'});closeEditor(true);notify('已解除人物关联');await load();}
      catch(err){dialogError(err);}
    };
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
        const archiveOptions=view==='works'
          ? [['all','全部作品'],['normal','在库作品'],['archived','已归档作品']]
          : view==='authors'
            ? [['all','全部人物'],['normal','未归档人物'],['archived','已归档人物']]
            : null;
        const archiveSelect=archiveOptions?`<label class="status-filter archive-filter" for="archive-status">归档状态 <select id="archive-status">${archiveOptions.map(([value,label])=>`<option value="${value}" ${archiveFilters[view]===value?'selected':''}>${label}</option>`).join('')}</select></label>`:'';
        $('#controls').innerHTML=`<input id="filter" aria-label="筛选列表" placeholder="${view==='sources'?'按来源名称或来源键查找':'按作者或作品名称查找'}" value="${esc(query)}"><button id="apply-filter">查找</button>${query?'<button id="clear-filter" type="button">清除筛选</button>':''}${archiveSelect}${view==='works'||view==='authors'||view==='sources'?'<button type="button" id="new-record" class="new-record">＋ 新建</button>':''}`;
        const archiveStatus=$('#archive-status');
        if(archiveStatus)archiveStatus.onchange=()=>{archiveFilters[view]=archiveStatus.value;offset=0;load().catch(showError);};
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
    if(view==='works'||view==='authors')queryString.set('status',archiveFilters[view]);
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
      name.textContent=view==='works'?row.title:view==='authors'?row.name:view==='sources'?row.title:view==='materials'?`${row.display_title||materialLabels[row.kind]||row.kind}`:view==='attributions'?row.original_name:`${row.action_label} · ${row.target_label}`;
      const info=document.createElement('small');info.textContent=view==='works'?`${row.author} · ${genreLabels[row.genre]||row.genre} · 第 ${row.version} 版 · ${workStatusLabels[row.status]||row.status}`:view==='authors'?`${dynastyLabels[row.dynasty]||row.dynasty} · ${authorStatusLabels[row.identity_status]||row.identity_status}`:view==='sources'?row.key:view==='materials'?`${materialLabels[row.kind]||row.kind} · ${row.creator||'作者未详'} · ${row.source_title||'来源未详'} · #${row.id}`:view==='attributions'?`${row.collection} · ${row.identity_label}${row.linked_author_name?'：'+row.linked_author_name:''}`:`${new Date(row.created_at).toLocaleString('zh-CN',{dateStyle:'medium',timeStyle:'short'})} · 操作人：${row.actor}`;
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
          if(view==='works'){const item=await api(`/works/${row.id}`);workForm(item);}
          if(view==='authors'){const item=await api(`/authors/${row.id}`);authorForm(item);}
          if(view==='sources')sourceForm(row);
          if(view==='materials')reviewForm(await api(`/materials/${row.id}`));
          if(view==='attributions')attributionForm(await api(`/attributions/${row.id}`));
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
