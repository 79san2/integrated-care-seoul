const CACHE='integrated-care-seoul-v7';
const FILES=['./','./index.html','./data.json','./manifest.json','./icon.svg'];
self.addEventListener('install',e=>e.waitUntil((async()=>{const c=await caches.open(CACHE);await c.addAll(FILES);await self.skipWaiting()})()));
self.addEventListener('activate',e=>e.waitUntil((async()=>{const keys=await caches.keys();await Promise.all(keys.filter(k=>k.startsWith('integrated-care-seoul-')&&k!==CACHE).map(k=>caches.delete(k)));await self.clients.claim()})()));
self.addEventListener('fetch',event=>{
 const request=event.request;
 const url=new URL(request.url);
 const scope=new URL(self.registration.scope);
 if(request.method!=='GET'||url.origin!==scope.origin||!url.pathname.startsWith(scope.pathname))return;
 const resource=url.pathname.slice(scope.pathname.length);
 const dataFiles=['data.json','auto-update-report.json','audit-report.json'];
 const data=dataFiles.includes(resource);
 const page=request.mode==='navigate'||['','index.html'].includes(resource);
 if(!data&&!page)return;
 const key=new URL(resource,scope).href;
 event.respondWith((async()=>{
  const cache=await caches.open(CACHE);
  try{
   const response=await fetch(request,{cache:'no-store'});
   if(response.ok)await cache.put(key,response.clone()).catch(()=>{});
   if(response.status<500)return response;
   return (await cache.match(key))||response;
  }catch{
   return (await cache.match(key))||Response.error();
  }
 })());
});

