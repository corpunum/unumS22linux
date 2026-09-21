// Real browser/PTY acceptance over the private Tailscale URL.
import {createRequire} from 'node:module';
import {mkdir, writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(import.meta.url);
const {chromium}=require('/home/corpunum/main/node_modules/playwright');
const url=process.env.S22_PI_WEB_URL;
if (!url || !url.startsWith('http://') || !url.endsWith(':8092/')) throw new Error('Explicit private Pi URL required');
const out=path.resolve('rootfs/pi-web-20260921/browser');
await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1100,height:760}});
const frames=[];
const errors=[];
page.on('pageerror',error=>errors.push(error.message));
page.on('websocket',ws=>ws.on('framereceived',event=>frames.push(Buffer.isBuffer(event.payload)?event.payload.toString('utf8'):event.payload)));
const text=()=>frames.join('');
const started=Date.now();
try {
  const response=await page.goto(url,{waitUntil:'domcontentloaded',timeout:20000});
  if (response.status()!==200) throw new Error(`HTTP ${response.status()}`);
  await page.locator('textarea.xterm-helper-textarea').waitFor({state:'attached',timeout:15000});
  await page.waitForTimeout(2500);
  await page.screenshot({path:path.join(out,'opened.png'),fullPage:true});
  await writeFile(path.join(out,'terminal-initial.txt'),text());
  if (!/Qwen3\.5-4B|s22-qwen4b/.test(text())) throw new Error('No real Pi model display; a tmux title alone is insufficient');
  if (process.env.S22_PI_WEB_SEND==='1') {
    const prompt='Reply with exactly S22_WEB_OK. Do not use tools.';
    await page.locator('textarea.xterm-helper-textarea').focus();
    await page.keyboard.type(prompt,{delay:15});
    await page.keyboard.press('Enter');
    // Preserve every server frame; a typed/echoed marker is not an answer.
    await page.waitForTimeout(Number(process.env.S22_PI_WEB_WAIT_MS || 90000));
  }
  await page.screenshot({path:path.join(out,'final.png'),fullPage:true});
  await writeFile(path.join(out,'terminal.txt'),text());
  const receipt={http_status:response.status(),websocket_frames:frames.length,
    browser_errors:errors,elapsed_seconds:(Date.now()-started)/1000,
    prompt_sent:process.env.S22_PI_WEB_SEND==='1',
    note:'Real browser/PTY evidence; model response must be checked in saved Pi session, not inferred from prompt echo.'};
  await writeFile(path.join(out,'receipt.json'),JSON.stringify(receipt,null,2)+'\n');
  console.log(JSON.stringify(receipt));
} catch(error) {
  await page.screenshot({path:path.join(out,'error.png'),fullPage:true});
  await writeFile(path.join(out,'terminal-error.txt'),text());
  throw error;
} finally {await browser.close();}
