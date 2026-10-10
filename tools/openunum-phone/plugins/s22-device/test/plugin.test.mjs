import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import Plugin from '../index.mjs';

const caps=[
{id:'status',method:'GET',path:'/v1/status',risk:'read',available:true},
{id:'send',method:'POST',path:'/v1/sms/send',risk:'risky',available:true},
{id:'wifi',method:'POST',path:'/v1/wifi/connect',risk:'reversible',available:true},
{id:'hidden',method:'GET',path:'/v1/thermal',risk:'read',available:false},
{id:'mismatch',method:'POST',path:'/v1/wifi/connect',risk:'risky',available:true}
];
let server, base, requests;
test.before(async()=>{requests=[];server=http.createServer(async(req,res)=>{let raw='';for await(const c of req)raw+=c;requests.push({method:req.method,url:req.url,body:raw?JSON.parse(raw):null});res.setHeader('content-type','application/json');if(req.url==='/v1/capabilities')res.end(JSON.stringify(caps));else res.end(JSON.stringify({ok:true,url:req.url}));});await new Promise(r=>server.listen(0,'127.0.0.1',r));base=`http://127.0.0.1:${server.address().port}`});
test.after(()=>new Promise(r=>server.close(r)));
function make(config={}){return new Plugin({name:'s22-device',version:'0.1.0',type:'tool'},{});}
test('generates tools only for available catalog rows matching route and tier',async()=>{const p=make();await p.onInit({enabled:true,daemon_url:base,mobile_marker:'/definitely/missing'});assert.deepEqual(p.getTools().map(t=>t.name),['device_status','wifi_connect','sms_send']);assert.equal(p.getTools().find(t=>t.name==='sms_send').parameters.required.join(','),'to,text')});
test('desktop without marker or opt-in exposes no tools and does not contact daemon',async()=>{const before=requests.length,p=make();await p.onInit({enabled:false,mobile_marker:'/definitely/missing'});assert.deepEqual(p.getTools(),[]);assert.equal(requests.length,before)});
test('read and reversible tools run; reversible action is logged',async()=>{const logs=[],p=new Plugin({name:'s22-device',version:'0.1',type:'tool'},{log:{info:(...x)=>logs.push(x)}});await p.onInit({enabled:true,daemon_url:base,mobile_marker:'/missing'});await p.getTools().find(t=>t.name==='device_status').execute({});await p.getTools().find(t=>t.name==='wifi_connect').execute({ssid:'Lab',password:'secret'});assert.equal(requests.at(-1).url,'/v1/wifi/connect');assert.deepEqual(requests.at(-1).body,{ssid:'Lab',password:'secret'});assert.equal(logs.at(-1)[1].risk,'reversible')});
test('risky tools require yes; no/timeout deny before daemon request',async()=>{const p=make();await p.onInit({enabled:true,daemon_url:base,mobile_marker:'/missing'});const tool=p.getTools().find(t=>t.name==='sms_send');let calls=0;p._confirm=async()=>{calls++;return'no'};const before=requests.length;assert.equal((await tool.execute({to:'+15551234567',text:'hello'})).error,'owner_confirmation_denied');p._confirm=async()=>{calls++;return'timeout'};assert.equal((await tool.execute({to:'+15551234567',text:'hello'})).answer,'timeout');assert.equal(calls,2);assert.equal(requests.length,before)});
test('risky operation executes only after yes confirmation',async()=>{const p=make();await p.onInit({enabled:true,daemon_url:base,mobile_marker:'/missing'});p._confirm=async()=> 'yes';const before=requests.length;const r=await p.getTools().find(t=>t.name==='sms_send').execute({to:'+15551234567',text:'hello'});assert.equal(r.ok,true);assert.equal(requests.length,before+1);assert.equal(requests.at(-1).url,'/v1/sms/send')});

test('phone confirmation uses ui confirm socket and accepts only yes tap file',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'s22-device-confirm-')),sock=path.join(dir,'ctl.sock'),confirmDir=path.join(dir,'confirm');fs.mkdirSync(confirmDir);
 const ui=net.createServer(c=>{let buf='';c.on('data',chunk=>{buf+=chunk.toString();if(buf.includes('\n')){const req=JSON.parse(buf.trim());assert.equal(req.cmd,'ui');assert.equal(req.args[0],'confirm');fs.writeFileSync(path.join(confirmDir,`${req.args[1]}.json`),JSON.stringify({answer:'yes'}));c.end(JSON.stringify({ok:true,result:'asking'})+'\n')}})});await new Promise(r=>ui.listen(sock,r));
 const p=make();p.settings={touchSocket:sock,confirmDir,confirmTimeoutS:5};assert.equal(await p._confirm('Proceed?'),'yes');await new Promise(r=>ui.close(r));fs.rmSync(dir,{recursive:true,force:true});
});
