const CACHE='integrated-care-seoul-v2';
const FILES=['./','./index.html','./data.json','./jobs.html','./jobs.json','./manifest.json','./icon.svg'];
self.addEventListener('install',event=>{
 event.waitUntil((async()=>{const cache=await caches.open(CACHE);await cache.addAll(FILES);await self.skipWaiting()})());
});
self.addEventListener('activate',event=>{
 event.waitUntil((async()=>{const keys=await caches.keys();await Promise.all(keys.filter(k=>k.startsWith('integrated-care-seoul-')&&k!==CACHE).map(k=>caches.delete(k)));await self.clients.claim()})());
});
self.addEventListener('fetch',event=>{
 if(event.request.method!=='GET')return;
 const url=new URL(event.request.url);
 const inApp=url.pathname.startsWith('/integrated-care-seoul/');
 if(!inApp)return;
 const isPage=event.request.mode==='navigate'||url.pathname.endsWith('/index.html')||url.pathname==='/integrated-care-seoul/';
 const isData=url.pathname.endsWith('/data.json')||url.pathname.endsWith('/jobs.json');
 if(isPage||isData){
  event.respondWith((async()=>{
   try{const response=await fetch(event.request,{cache:'no-store'});if(response.ok){const cache=await caches.open(CACHE);await cache.put(event.request,response.clone())}return response}
   catch(error){return (await caches.match(event.request,{ignoreSearch:true}))||(await caches.match('./index.html'))||Response.error()}
  })());return;
 }
 event.respondWith((async()=>{const cached=await caches.match(event.request);if(cached)return cached;try{const response=await fetch(event.request);if(response.ok){const cache=await caches.open(CACHE);await cache.put(event.request,response.clone())}return response}catch(error){return Response.error()}})());
});
self.addEventListener('push',event=>{
 let data={title:'새 채용공고',body:'새로운 채용정보가 등록되었습니다.',url:'./jobs.html'};
 try{if(event.data)data={...data,...event.data.json()}}catch(e){}
 event.waitUntil(self.registration.showNotification(data.title||'새 채용공고',{body:data.body||'새로운 채용정보가 등록되었습니다.',icon:'./icon.svg',badge:'./icon.svg',data:{url:data.url||'./jobs.html'}}));
});
self.addEventListener('notificationclick',event=>{
 event.notification.close();
 const target=event.notification.data&&event.notification.data.url?event.notification.data.url:'./jobs.html';
 event.waitUntil(clients.matchAll({type:'window',includeUncontrolled:true}).then(list=>{
  for(const client of list){if('focus'in client)return client.focus().then(()=>client.navigate(target))}
  if(clients.openWindow)return clients.openWindow(target);
 }));
});
