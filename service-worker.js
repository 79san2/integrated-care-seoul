const CACHE='integrated-care-seoul-v5';
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
self.addEventListener('fetch',e=>{if(e.request.method!=='GET')return;const u=new URL(e.request.url);if(!u.pathname.startsWith('/integrated-care-seoul/'))return;const page=e.request.mode==='navigate'||u.pathname.endsWith('/index.html')||u.pathname.endsWith('/jobs.html')||u.pathname==='/integrated-care-seoul/';const data=u.pathname.endsWith('/data.json')||u.pathname.endsWith('/jobs.json');if(page||data){e.respondWith((async()=>{try{const r=await fetch(e.request,{cache:'no-store'});if(r.ok){const c=await caches.open(CACHE);await c.put(e.request,r.clone())}return r}catch(err){return(await caches.match(e.request,{ignoreSearch:true}))||(await caches.match('./index.html'))||Response.error()}})());return}e.respondWith((async()=>{const c=await caches.match(e.request);if(c)return c;try{const r=await fetch(e.request);if(r.ok){const ca=await caches.open(CACHE);await ca.put(e.request,r.clone())}return r}catch(err){return Response.error()}})())});
self.addEventListener('notificationclick',e=>{e.notification.close();const target=e.notification.data&&e.notification.data.url?e.notification.data.url:'./jobs.html';e.waitUntil(clients.matchAll({type:'window',includeUncontrolled:true}).then(list=>{for(const c of list){if('focus'in c)return c.focus().then(()=>c.navigate(target))}if(clients.openWindow)return clients.openWindow(target)}))});
