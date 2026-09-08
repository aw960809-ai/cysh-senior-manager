import { DurableObject } from 'cloudflare:workers';
import { buildPushPayload } from '@block65/webcrypto-web-push';

const JSON_HEADERS = {'content-type':'application/json; charset=utf-8'};
const MAX_JOBS = 150;
const MAX_FUTURE_MS = 45 * 86400000;

function json(data, status=200, extra={}) {
  return new Response(JSON.stringify(data), {status, headers:{...JSON_HEADERS,...extra}});
}
function cors(origin, allowed) {
  return {
    'access-control-allow-origin': origin || allowed,
    'access-control-allow-methods': 'GET,POST,OPTIONS',
    'access-control-allow-headers': 'content-type',
    'access-control-max-age': '86400',
    'vary': 'Origin'
  };
}
async function hashSecret(secret) {
  const bytes = new TextEncoder().encode(String(secret || ''));
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest)).map(x=>x.toString(16).padStart(2,'0')).join('');
}
function cleanJob(raw) {
  const sendAt = Date.parse(raw?.sendAt);
  if (!raw?.id || !Number.isFinite(sendAt)) return null;
  const now = Date.now();
  if (sendAt < now - 120000 || sendAt > now + MAX_FUTURE_MS) return null;
  return {
    id: String(raw.id).slice(0,180),
    sendAt,
    title: String(raw.title || '嘉中高三管理').slice(0,80),
    body: String(raw.body || '有新的提醒。').slice(0,220),
    page: ['home','goals','calendar','activities','scholarships','system'].includes(raw.page) ? raw.page : 'home',
    tag: String(raw.tag || raw.id).slice(0,180),
    attempts: 0
  };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const origin = request.headers.get('Origin') || '';
    const allowed = env.ALLOWED_ORIGIN || 'https://aw960809-ai.github.io';
    const ch = cors(origin, allowed);
    if (request.method === 'OPTIONS') {
      if (origin && origin !== allowed) return new Response(null,{status:403});
      return new Response(null,{status:204,headers:ch});
    }
    if (origin && origin !== allowed) return json({error:'origin not allowed'},403,ch);
    if (url.pathname === '/health') return json({ok:true,version:'v1'},200,ch);
    if (url.pathname === '/config') {
      if (!env.VAPID_SERVER_PUBLIC_KEY) return json({error:'VAPID public key not configured'},503,ch);
      return json({publicKey:env.VAPID_SERVER_PUBLIC_KEY,version:'v1'},200,ch);
    }
    const m = url.pathname.match(/^\/device\/([A-Za-z0-9._~-]{8,160})\/(subscribe|sync|status|test|unsubscribe)$/);
    if (!m) return json({error:'not found'},404,ch);
    const id = env.PUSH_DEVICES.idFromName(m[1]);
    const stub = env.PUSH_DEVICES.get(id);
    const target = new URL(request.url);
    target.pathname = '/' + m[2];
    const proxied = new Request(target, request);
    const resp = await stub.fetch(proxied);
    const headers = new Headers(resp.headers);
    Object.entries(ch).forEach(([k,v])=>headers.set(k,v));
    return new Response(resp.body,{status:resp.status,statusText:resp.statusText,headers});
  }
};

export class PushDevice extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.ctx = ctx;
    this.env = env;
  }
  async profile() { return (await this.ctx.storage.get('profile')) || null; }
  async jobs() { return (await this.ctx.storage.get('jobs')) || []; }
  async verify(secret) {
    const p = await this.profile();
    if (!p?.secretHash) return false;
    return p.secretHash === await hashSecret(secret);
  }
  async setNextAlarm(jobs) {
    const future = jobs.map(x=>x.sendAt).filter(x=>Number.isFinite(x)).sort((a,b)=>a-b);
    if (!future.length) { await this.ctx.storage.deleteAlarm(); return; }
    await this.ctx.storage.setAlarm(Math.max(Date.now()+750, future[0]));
  }
  async sendOne(profile, job) {
    if (!profile?.subscription?.endpoint) return {ok:false,drop:true,status:410};
    if (!this.env.VAPID_SERVER_PUBLIC_KEY || !this.env.VAPID_SERVER_PRIVATE_KEY) return {ok:false,drop:false,status:503};
    const payload = await buildPushPayload({
      data: JSON.stringify({title:job.title,body:job.body,page:job.page,tag:job.tag}),
      options: {ttl: 300}
    }, profile.subscription, {
      subject: this.env.VAPID_SUBJECT,
      publicKey: this.env.VAPID_SERVER_PUBLIC_KEY,
      privateKey: this.env.VAPID_SERVER_PRIVATE_KEY
    });
    const res = await fetch(profile.subscription.endpoint, payload);
    if (res.ok) return {ok:true,status:res.status};
    if (res.status === 404 || res.status === 410) return {ok:false,drop:true,status:res.status};
    return {ok:false,drop:false,status:res.status};
  }
  async fetch(request) {
    if (request.method !== 'POST') return json({error:'method not allowed'},405);
    const action = new URL(request.url).pathname.slice(1);
    const body = await request.json().catch(()=>({}));
    const secret = String(body.secret || '');
    if (!secret || secret.length < 24) return json({error:'invalid device secret'},400);

    if (action === 'subscribe') {
      const current = await this.profile();
      const secretHash = await hashSecret(secret);
      if (current?.secretHash && current.secretHash !== secretHash) return json({error:'device secret mismatch'},403);
      const s = body.subscription;
      if (!s?.endpoint || !s?.keys?.p256dh || !s?.keys?.auth || !String(s.endpoint).startsWith('https://')) return json({error:'invalid push subscription'},400);
      const profile = {secretHash,subscription:s,timezone:String(body.timezone||'Asia/Taipei').slice(0,80),updatedAt:new Date().toISOString(),lastPushAt:current?.lastPushAt||null};
      await this.ctx.storage.put('profile', profile);
      return json({ok:true,subscribed:true});
    }

    if (!(await this.verify(secret))) return json({error:'device secret mismatch'},403);
    const profile = await this.profile();

    if (action === 'sync') {
      const raw = Array.isArray(body.jobs) ? body.jobs : [];
      const jobs = raw.slice(0,MAX_JOBS).map(cleanJob).filter(Boolean).sort((a,b)=>a.sendAt-b.sendAt);
      profile.timezone = String(body.timezone || profile.timezone || 'Asia/Taipei').slice(0,80);
      profile.updatedAt = new Date().toISOString();
      await this.ctx.storage.put('profile',profile);
      await this.ctx.storage.put('jobs',jobs);
      await this.setNextAlarm(jobs);
      return json({ok:true,jobCount:jobs.length,nextAt:jobs[0]?.sendAt||null});
    }
    if (action === 'status') {
      const jobs = await this.jobs();
      return json({ok:true,subscribed:!!profile.subscription,jobCount:jobs.length,lastPushAt:profile.lastPushAt||null,nextAt:jobs[0]?.sendAt||null});
    }
    if (action === 'test') {
      const job={id:'test',sendAt:Date.now(),title:'嘉中高三管理｜背景測試',body:'App 關閉後 Web Push 已成功連接。',page:'system',tag:'webpush-test'};
      const result=await this.sendOne(profile,job);
      if (result.drop) { await this.ctx.storage.deleteAll(); return json({error:'push subscription expired'},410); }
      if (!result.ok) return json({error:`push service HTTP ${result.status}`},502);
      profile.lastPushAt=new Date().toISOString();await this.ctx.storage.put('profile',profile);
      return json({ok:true});
    }
    if (action === 'unsubscribe') {
      await this.ctx.storage.deleteAll();
      return json({ok:true,subscribed:false});
    }
    return json({error:'not found'},404);
  }
  async alarm() {
    const profile = await this.profile();
    let jobs = await this.jobs();
    if (!profile?.subscription || !jobs.length) { await this.ctx.storage.deleteAlarm(); return; }
    const now = Date.now();
    const due = jobs.filter(x=>x.sendAt<=now+1500).slice(0,8);
    const untouched = jobs.filter(x=>!due.some(d=>d.id===x.id));
    const retry=[];
    for (const job of due) {
      try {
        const result=await this.sendOne(profile,job);
        if (result.ok) {
          profile.lastPushAt=new Date().toISOString();
          continue;
        }
        if (result.drop) { await this.ctx.storage.deleteAll(); return; }
        const attempts=(job.attempts||0)+1;
        if (attempts<5 && now-job.sendAt<86400000) retry.push({...job,attempts,sendAt:Date.now()+Math.min(30,5*attempts)*60000});
      } catch (_) {
        const attempts=(job.attempts||0)+1;
        if (attempts<5 && now-job.sendAt<86400000) retry.push({...job,attempts,sendAt:Date.now()+Math.min(30,5*attempts)*60000});
      }
    }
    jobs=[...untouched,...retry].sort((a,b)=>a.sendAt-b.sendAt);
    await this.ctx.storage.put('profile',profile);
    await this.ctx.storage.put('jobs',jobs);
    await this.setNextAlarm(jobs);
  }
}
