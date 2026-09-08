/* CYSH AUTO UPDATE V3 */
/* CYSH AUTO UPDATE V3.1 FIRST-RUN FIX */
const CACHE_NAME='cysh-manager-v10.1.2-shell';
const SHELL=['./','./index.html','./manifest.webmanifest','./icons/icon-192.png','./icons/icon-512.png','./update-manager.js'];

self.addEventListener('install',event=>{
  event.waitUntil(caches.open(CACHE_NAME).then(cache=>cache.addAll(SHELL)).then(()=>self.skipWaiting()));
});

self.addEventListener('activate',event=>{
  event.waitUntil(
    caches.keys()
      .then(keys=>Promise.all(keys.filter(k=>k!==CACHE_NAME).map(k=>caches.delete(k))))
      .then(()=>self.clients.claim())
  );
});

async function networkFirst(request){
  const cache=await caches.open(CACHE_NAME);
  try{
    const response=await fetch(request,{cache:'no-store'});
    if(response&&response.ok)cache.put(request,response.clone());
    return response;
  }catch(err){
    const cached=await cache.match(request);
    if(cached)return cached;
    if(request.mode==='navigate')return cache.match('./index.html');
    throw err;
  }
}

self.addEventListener('fetch',event=>{
  const req=event.request;
  if(req.method!=='GET')return;
  const url=new URL(req.url);
  if(url.origin!==self.location.origin)return;

  if(req.mode==='navigate' ||
     url.pathname.endsWith('/index.html') ||
     url.pathname.endsWith('/update-manager.js') ||
     url.pathname.endsWith('/data/opportunities.json')){
    event.respondWith(networkFirst(req));
    return;
  }

  if(url.pathname.endsWith('/manifest.webmanifest')||url.pathname.includes('/icons/')){
    event.respondWith(
      caches.match(req).then(cached=>cached||fetch(req,{cache:'no-store'}).then(res=>{
        const copy=res.clone();
        caches.open(CACHE_NAME).then(c=>c.put(req,copy));
        return res;
      }))
    );
  }
});

self.addEventListener('message',event=>{
  if(event.data?.type==='SKIP_WAITING')self.skipWaiting();
  if(event.data?.type==='GET_VERSION'&&event.ports?.[0]){
    event.ports[0].postMessage({version:'10.1.2-auto-update-v3.1'});
  }
});

self.addEventListener('notificationclick',event=>{
  event.notification.close();
  const page=event.notification.data?.page||'home';
  event.waitUntil(self.clients.matchAll({type:'window',includeUncontrolled:true}).then(async clients=>{
    for(const client of clients){
      if('focus' in client){
        await client.focus();
        client.postMessage({type:'NAVIGATE',page});
        return;
      }
    }
    if(self.clients.openWindow)return self.clients.openWindow('./#'+page);
  }));
});

self.addEventListener('push',event=>{
  let data={};
  try{data=event.data?.json()||{}}catch(_){data={body:event.data?.text()||''}}
  const title=data.title||'嘉中高三管理';
  const options={
    body:data.body||'有新的提醒。',
    icon:'./icons/icon-192.png',
    badge:'./icons/icon-192.png',
    tag:data.tag||'cysh-push',
    data:{page:data.page||'home'}
  };
  event.waitUntil(self.registration.showNotification(title,options));
});
