/* CYSH AUTO UPDATE V3 */
(() => {
  'use strict';

  const VERSION = '3.0.0';
  const CHECK_EVERY_MS = 5 * 60 * 1000;
  const FOREGROUND_MIN_MS = 60 * 1000;
  const AUTO_APPLY_DELAY_MS = 1200;

  const KEY_SIG = 'cysh-pwa-code-signature-v3';
  const KEY_PENDING = 'cysh-pwa-pending-signature-v3';
  const KEY_LAST_CHECK = 'cysh-pwa-last-check-v3';

  let registration = null;
  let latestSignature = '';
  let lastCheckAt = 0;
  let applying = false;
  let reloadGuard = false;
  let delayedTimer = null;

  function q(id){ return document.getElementById(id); }
  function online(){ return navigator.onLine !== false; }

  function toastSafe(msg){
    try{
      if(typeof window.toast === 'function') window.toast(msg);
      else console.info('[CYSH UPDATE]', msg);
    }catch(_){}
  }

  function injectStyle(){
    if(q('cyshAutoUpdateStyle')) return;
    const style = document.createElement('style');
    style.id = 'cyshAutoUpdateStyle';
    style.textContent = `
      .cysh-update-status{
        display:flex;align-items:center;gap:8px;margin:9px 0 0;padding:9px 11px;
        border:1px solid var(--line,#dfe5de);border-radius:12px;background:rgba(255,255,255,.72);
        color:var(--muted,#6e7c76);font-size:10px;line-height:1.4
      }
      .cysh-update-status span{width:18px;text-align:center;font-weight:950}
      .cysh-update-status b{font-size:10px}
      .cysh-update-status.checking{background:var(--gold-soft,#f5eddd);color:#735f38}
      .cysh-update-status.ok{background:var(--green-soft,#e9f0ec);color:var(--green,#315f50)}
      .cysh-update-status.update{background:var(--gold-soft,#f5eddd);color:#735f38;border-color:#dfc992}
      .cysh-update-status.error{background:var(--red-soft,#f8eaea);color:var(--red,#a15353)}
      .cysh-update-auto-note{font-size:9px;color:var(--muted,#6e7c76);margin-top:6px;line-height:1.45}
      .cysh-update-bar{
        position:fixed;left:50%;bottom:calc(92px + env(safe-area-inset-bottom));z-index:1600;
        width:min(560px,calc(100% - 24px));transform:translate(-50%,18px);
        display:flex;align-items:center;justify-content:space-between;gap:10px;
        padding:11px 12px;border:1px solid #d7c18e;border-radius:15px;
        background:rgba(255,253,248,.98);box-shadow:0 12px 34px rgba(37,56,48,.18);
        opacity:0;pointer-events:none;transition:.18s
      }
      .cysh-update-bar.show{opacity:1;pointer-events:auto;transform:translate(-50%,0)}
      .cysh-update-bar>div{display:grid;gap:2px;min-width:0}
      .cysh-update-bar b{font-size:11px}.cysh-update-bar span{font-size:9px;color:var(--muted,#6e7c76)}
      .cysh-update-bar button{flex:0 0 auto}
    `;
    document.head.appendChild(style);
  }

  function ensureUI(){
    injectStyle();
    let status = q('cyshUpdateStatus');
    if(status) return;

    const install = q('installPwa');
    if(!install) return;

    const actions = install.closest('.actions') || install.parentElement;
    if(!actions) return;

    status = document.createElement('div');
    status.id = 'cyshUpdateStatus';
    status.className = 'cysh-update-status ok';
    status.setAttribute('aria-live','polite');
    status.innerHTML = '<span>✓</span><b>自動更新已啟用</b>';

    actions.parentElement.insertBefore(status, actions);

    const check = document.createElement('button');
    check.id = 'cyshCheckUpdate';
    check.type = 'button';
    check.className = 'btn secondary';
    check.textContent = '↻ 檢查程式更新';
    check.addEventListener('click', () => checkForUpdate({manual:true, autoApply:false}));
    actions.insertBefore(check, install.nextSibling);

    const apply = document.createElement('button');
    apply.id = 'cyshApplyUpdate';
    apply.type = 'button';
    apply.className = 'btn gold';
    apply.textContent = '↑ 立即更新';
    apply.style.display = 'none';
    apply.addEventListener('click', () => applyLatest(true));
    actions.insertBefore(apply, check.nextSibling);

    const note = document.createElement('div');
    note.className = 'cysh-update-auto-note';
    note.textContent = '自動更新：啟用。App 開啟、回到前景與使用期間會自行檢查；只有自動更新未正確完成時才需要手動檢查／更新。';
    status.insertAdjacentElement('afterend', note);
  }

  function setUI(state, message){
    ensureUI();
    const box = q('cyshUpdateStatus');
    const btn = q('cyshCheckUpdate');
    const apply = q('cyshApplyUpdate');

    if(box){
      const icon = state === 'checking' ? '◌' : state === 'ok' ? '✓' : state === 'update' ? '↑' : '!';
      box.className = 'cysh-update-status ' + state;
      box.innerHTML = `<span>${icon}</span><b>${message}</b>`;
    }

    if(btn){
      btn.disabled = state === 'checking';
      btn.textContent = state === 'checking' ? '◌ 檢查中…' : '↻ 檢查程式更新';
    }

    if(apply) apply.style.display = state === 'update' ? 'inline-flex' : 'none';
  }

  function ensureBar(){
    let bar = q('cyshUpdateBar');
    if(bar) return bar;

    bar = document.createElement('div');
    bar.id = 'cyshUpdateBar';
    bar.className = 'cysh-update-bar';
    bar.setAttribute('role','status');
    bar.setAttribute('aria-live','polite');
    bar.innerHTML = `
      <div>
        <b>發現新的程式版本</b>
        <span>系統會自動更新；本機目標、完成紀錄、行事曆、通知設定與 Web Push 不會刪除。</span>
      </div>
      <button class="btn" type="button">立即更新</button>`;
    bar.querySelector('button').addEventListener('click', () => applyLatest(true));
    document.body.appendChild(bar);
    return bar;
  }

  function showBar(){ ensureBar().classList.add('show'); }
  function hideBar(){ q('cyshUpdateBar')?.classList.remove('show'); }

  async function sha256(text){
    if(globalThis.crypto?.subtle){
      const bytes = new TextEncoder().encode(text);
      const digest = await crypto.subtle.digest('SHA-256', bytes);
      return [...new Uint8Array(digest)].map(x => x.toString(16).padStart(2,'0')).join('');
    }
    let h = 2166136261;
    for(let i=0;i<text.length;i++){
      h ^= text.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return (h >>> 0).toString(16).padStart(8,'0');
  }

  async function fetchFreshText(url){
    const sep = url.includes('?') ? '&' : '?';
    const res = await fetch(url + sep + '__cysh_update=' + Date.now(), {cache:'no-store'});
    if(!res.ok) throw new Error(`${url} HTTP ${res.status}`);
    return await res.text();
  }

  async function remoteSignature(){
    const [index, sw, updater, manifest] = await Promise.all([
      fetchFreshText('./index.html'),
      fetchFreshText('./sw.js'),
      fetchFreshText('./update-manager.js'),
      fetchFreshText('./manifest.webmanifest')
    ]);
    return await sha256(index + '\n---SW---\n' + sw + '\n---UPDATER---\n' + updater + '\n---MANIFEST---\n' + manifest);
  }

  function currentSignature(){ return localStorage.getItem(KEY_SIG) || ''; }
  function pendingSignature(){ return localStorage.getItem(KEY_PENDING) || ''; }

  function markPending(sig){
    if(sig) localStorage.setItem(KEY_PENDING, sig);
  }

  function commitPendingIfCurrent(remoteSig){
    const pending = pendingSignature();
    if(pending && pending === remoteSig){
      localStorage.setItem(KEY_SIG, remoteSig);
      localStorage.removeItem(KEY_PENDING);
      latestSignature = remoteSig;
      return true;
    }
    return false;
  }

  function isEditing(){
    const el = document.activeElement;
    if(!el) return false;
    return el.matches?.('input,textarea,select,[contenteditable="true"]') === true;
  }

  function protectLocalData(){
    try{ if(typeof window.saveDB === 'function') window.saveDB(); }catch(_){}
    try{
      if(typeof window.saveSafetySnapshot === 'function' && window.DB){
        window.saveSafetySnapshot(window.DB, '自動程式更新前');
      }
    }catch(_){}
  }

  async function getRegistration(){
    if(!('serviceWorker' in navigator)) return null;
    registration = registration ||
      await navigator.serviceWorker.getRegistration('./') ||
      await navigator.serviceWorker.register('./sw.js', {scope:'./', updateViaCache:'none'});
    return registration;
  }

  function hardReload(sig){
    protectLocalData();
    markPending(sig);
    const u = new URL(location.href);
    u.searchParams.set('__cysh_v', sig.slice(0,12));
    u.searchParams.set('__cysh_reload', Date.now().toString());
    location.replace(u.href);
  }

  async function applyLatest(manual=false){
    if(applying) return false;
    applying = true;
    clearTimeout(delayedTimer);

    try{
      let sig = latestSignature;
      if(!sig) sig = await remoteSignature();

      if(!manual && isEditing()){
        setUI('update','發現新版本；完成目前輸入後會自動更新');
        showBar();
        delayedTimer = setTimeout(() => {
          applying = false;
          applyLatest(false);
        }, 30000);
        return true;
      }

      setUI('checking', manual ? '正在手動安裝最新版…' : '正在自動安裝最新版…');
      protectLocalData();
      markPending(sig);

      const reg = await getRegistration();
      if(reg){
        try{ await reg.update(); }catch(_){}
        await new Promise(r => setTimeout(r, 650));

        if(reg.waiting){
          reg.waiting.postMessage({type:'SKIP_WAITING'});
          return true;
        }

        if(reg.installing){
          const worker = reg.installing;
          worker.addEventListener('statechange', () => {
            if(worker.state === 'installed'){
              const waiting = reg.waiting;
              if(waiting) waiting.postMessage({type:'SKIP_WAITING'});
              else setTimeout(() => hardReload(sig), 250);
            }
          }, {once:false});
          setTimeout(() => {
            if(!reloadGuard) hardReload(sig);
          }, 3500);
          return true;
        }
      }

      hardReload(sig);
      return true;

    }catch(e){
      console.warn('[CYSH UPDATE] apply failed', e);
      setUI('error','自動更新未完成；可按「檢查程式更新」後手動更新');
      showBar();
      if(manual) toastSafe('手動更新失敗，請稍後再試');
      return false;
    }finally{
      applying = false;
    }
  }

  async function checkForUpdate({manual=false, autoApply=true}={}){
    if(!online()){
      if(manual){
        setUI('error','目前離線，無法檢查更新');
        toastSafe('目前離線，無法檢查更新');
      }
      return false;
    }

    if(manual) setUI('checking','正在向伺服器檢查新版本…');

    try{
      const sig = await remoteSignature();
      latestSignature = sig;
      lastCheckAt = Date.now();
      localStorage.setItem(KEY_LAST_CHECK, String(lastCheckAt));

      if(commitPendingIfCurrent(sig)){
        hideBar();
        setUI('ok','更新完成，目前已是最新版本');
        return false;
      }

      const current = currentSignature();

      if(!current){
        localStorage.setItem(KEY_SIG, sig);
        hideBar();
        setUI('ok','自動更新已啟用 · 目前已是最新版本');
        return false;
      }

      if(current === sig){
        hideBar();
        const time = new Date().toLocaleTimeString('zh-TW',{hour:'2-digit',minute:'2-digit'});
        setUI('ok',`目前已是最新版本 · ${time}`);
        if(manual) toastSafe('目前已是最新程式版本');
        return false;
      }

      setUI('update', autoApply && !manual ? '發現新版本，正在自動更新…' : '發現新版本，可以立即更新');
      showBar();

      if(autoApply && !manual){
        setTimeout(() => applyLatest(false), AUTO_APPLY_DELAY_MS);
      }else if(manual){
        toastSafe('發現新版本，可以立即更新');
      }

      return true;

    }catch(e){
      console.warn('[CYSH UPDATE] check failed', e);
      if(manual){
        setUI('error','檢查更新失敗，請稍後再試');
        toastSafe('檢查更新失敗');
      }
      return false;
    }
  }

  function bindServiceWorkerLifecycle(){
    if(!('serviceWorker' in navigator)) return;

    navigator.serviceWorker.addEventListener('controllerchange', () => {
      if(reloadGuard) return;
      reloadGuard = true;
      protectLocalData();
      const sig = latestSignature || pendingSignature();
      if(sig) markPending(sig);
      location.reload();
    });

    navigator.serviceWorker.ready.then(reg => {
      registration = reg;
      reg.addEventListener('updatefound', () => {
        const worker = reg.installing;
        if(!worker) return;
        worker.addEventListener('statechange', () => {
          if(worker.state === 'installed' && navigator.serviceWorker.controller){
            const pending = latestSignature || pendingSignature();
            if(pending) markPending(pending);
            if(reg.waiting) reg.waiting.postMessage({type:'SKIP_WAITING'});
          }
        });
      });
    }).catch(()=>{});
  }

  function boot(){
    ensureUI();
    bindServiceWorkerLifecycle();

    setTimeout(() => checkForUpdate({manual:false, autoApply:true}), 2500);

    setInterval(() => {
      if(document.visibilityState === 'visible'){
        checkForUpdate({manual:false, autoApply:true});
      }
    }, CHECK_EVERY_MS);

    document.addEventListener('visibilitychange', () => {
      if(document.visibilityState !== 'visible') return;
      if(Date.now() - lastCheckAt < FOREGROUND_MIN_MS) return;
      checkForUpdate({manual:false, autoApply:true});
    });

    window.addEventListener('online', () => {
      setTimeout(() => checkForUpdate({manual:false, autoApply:true}), 1200);
    });
  }

  window.CYSH_AUTO_UPDATE = {
    version: VERSION,
    check: () => checkForUpdate({manual:true, autoApply:false}),
    apply: () => applyLatest(true),
    autoCheck: () => checkForUpdate({manual:false, autoApply:true})
  };

  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
