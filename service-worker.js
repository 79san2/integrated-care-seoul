const CACHE='integrated-care-seoul-v6';
importScripts('./firebase-config.js');
importScripts('https://www.gstatic.com/firebasejs/10.14.1/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/10.14.1/firebase-messaging-compat.js');
if(self.FIREBASE_CONFIG && self.FIREBASE_CONFIG.apiKey && !self.FIREBASE_CONFIG.apiKey.startsWith('여기에')){
 firebase.initializeApp(self.FIREBASE_CONFIG);
 const messaging=firebase.messaging();
 messaging.onBackgroundMessage(payload=>{
  const d=payload.data||{};
  self.registration.showNotification(d.title||'새 통합돌봄 채용공고',{body:d.body||'새로운 채용공고가 등록되었습니다.',icon:'./icon.svg',badge:'./icon.svg',tag:d.jobId||'integrated-care-job',data:{url:d.url||'./jobs.html'}});
 });
}
const FILES=['./','./index.html','./data.json','./jobs.html','./jobs.json','./manifest.json','./icon.svg','./push-setup.html','./firebase-config.js'];
self.addEventListener('install',e=>e.waitUntil((async()=>{const c=await caches.open(CACHE);await c.addAll(FILES);await self.skipWaiting()})()));
self.addEventListener('activate',e=>e.waitUntil((async()=>{const keys=await caches.keys();await Promise.all(keys.filter(k=>k.startsWith('integrated-care-seoul-')&&k!==CACHE).map(k=>caches.delete(k)));await self.clients.claim()})()));
self.addEventListener('fetch',event=>{
 const request=event.request;
 const url=new URL(request.url);
 const scope=new URL(self.registration.scope);
 if(request.method!=='GET'||url.origin!==scope.origin||!url.pathname.startsWith(scope.pathname))return;
 const resource=url.pathname.slice(scope.pathname.length);
 const dataFiles=['data.json','jobs.json','sources.json','auto-update-report.json','audit-report.json'];
 const data=dataFiles.includes(resource);
 const page=request.mode==='navigate'||['','index.html','jobs.html','push-setup.html'].includes(resource);
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
self.addEventListener('notificationclick',e=>{e.notification.close();const target=e.notification.data&&e.notification.data.url?e.notification.data.url:'./jobs.html';e.waitUntil(clients.matchAll({type:'window',includeUncontrolled:true}).then(list=>{for(const c of list){if('focus'in c)return c.focus().then(()=>c.navigate(target))}if(clients.openWindow)return clients.openWindow(target)}))});
