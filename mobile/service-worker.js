const CACHE='taistock-shell-v2';
const SHELL=['/','/manifest.json','/icon.svg'];
self.addEventListener('install',e=>e.waitUntil(caches.open(CACHE).then(c=>c.addAll(SHELL))));
self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(xs=>Promise.all(xs.filter(x=>x!==CACHE).map(x=>caches.delete(x))))));
self.addEventListener('fetch',e=>{
  const path=new URL(e.request.url).pathname;
  if(e.request.method!=='GET'||path.startsWith('/api/')||path.startsWith('/download/')||!SHELL.includes(path))return;
  e.respondWith(fetch(e.request).then(r=>{
    if(r.ok&&!r.headers.get('cache-control')?.includes('no-store')){
      const copy=r.clone();caches.open(CACHE).then(c=>c.put(e.request,copy));
    }
    return r;
  }).catch(()=>caches.match(e.request)));
});
