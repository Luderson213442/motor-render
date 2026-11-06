// Renderiza em vídeo uma página HTML animada por tempo.
// A página expõe window.__duracao (s) e window.__irPara(s), que desenha exatamente aquele instante.
//
//   node motor/html_quadros.mjs <pasta> <saida.mp4> [fps=30] [largura=1080] [altura=1920] [seletor=#palco] [partes=4]
//
// Serve <pasta> num http local, abre <pasta>/index.html no Chromium headless, desenha cada quadro com
// __irPara(i/fps), espera o próximo quadro, as imagens, as fontes e a rede ficarem quietas, tira o print
// e manda direto ao ffmpeg por pipe (H.264, CRF 18, yuv420p). Junta <pasta>/audio.m4a se existir.
import { spawn } from 'node:child_process';
import { createReadStream, existsSync, statSync, writeFileSync, rmSync } from 'node:fs';
import { createServer } from 'node:http';
import { extname, join, normalize, resolve } from 'node:path';
import { chromium } from 'playwright';

const [, , PASTA, SAIDA, FPS_ = '30', L_ = '1080', A_ = '1920', SEL = '#palco', PARTES_ = '4'] = process.argv;
const FPS = +FPS_, L = +L_, A = +A_, PARTES = Math.max(1, +PARTES_);
const RAIZ = resolve(PASTA);
const TIPOS = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css',
  '.json': 'application/json', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp',
  '.svg': 'image/svg+xml', '.gif': 'image/gif', '.woff': 'font/woff', '.woff2': 'font/woff2', '.ttf': 'font/ttf',
  '.otf': 'font/otf', '.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.m4a': 'audio/mp4', '.mp4': 'video/mp4' };

const t0 = Date.now();
const log = (...a) => console.log(((Date.now() - t0) / 1000).toFixed(1).padStart(6), ...a);

// http local (file:// quebra fetch, módulos e algumas fontes)
const srv = createServer((q, r) => {
  const p = normalize(join(RAIZ, decodeURIComponent(q.url.split('?')[0])));
  if (!p.startsWith(RAIZ) || !existsSync(p) || statSync(p).isDirectory()) { r.writeHead(404); return r.end(); }
  r.writeHead(200, { 'Content-Type': TIPOS[extname(p).toLowerCase()] || 'application/octet-stream', 'Cache-Control': 'max-age=3600' });
  createReadStream(p).pipe(r);
});
await new Promise(ok => srv.listen(0, '127.0.0.1', ok));
const URL_ = `http://127.0.0.1:${srv.address().port}/index.html`;

const nav = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required', '--disable-gpu-vsync', '--font-render-hinting=none'] });

async function abrir() {
  const ctx = await nav.newContext({ viewport: { width: L, height: A }, deviceScaleFactor: 1 });
  const pg = await ctx.newPage();
  let voando = 0;
  pg.on('request', () => voando++);
  pg.on('requestfinished', () => voando--);
  pg.on('requestfailed', q => { voando--; log('falhou um recurso:', q.failure()?.errorText); });
  pg.on('pageerror', e => log('erro na página:', e.message));
  await pg.goto(URL_, { waitUntil: 'networkidle', timeout: 120000 });
  await pg.waitForFunction(() => typeof window.__irPara === 'function' && +window.__duracao > 0, null, { timeout: 120000 });
  await pg.evaluate(() => document.fonts.ready);
  const quieta = async () => {
    const fim = Date.now() + 15000;
    while (voando > 0 && Date.now() < fim) await pg.waitForTimeout(20);
  };
  const desenhar = async t => {
    await pg.evaluate(async t => {
      await window.__irPara(t);
      await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
      const pend = [...document.images].filter(i => !i.complete);
      if (pend.length) await Promise.all(pend.map(i => new Promise(r => { i.onload = i.onerror = r; })));
      await Promise.all([...document.images].filter(i => i.decode).map(i => i.decode().catch(() => {})));
      await document.fonts.ready;
    }, t);
    if (voando > 0) { await quieta(); await pg.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))); }
  };
  const caixa = await pg.evaluate(sel => {
    const e = sel && document.querySelector(sel);
    if (!e) return null;
    const b = e.getBoundingClientRect();
    return { x: b.left, y: b.top, width: b.width, height: b.height };
  }, SEL);
  const clip = caixa && Math.round(caixa.width) === L && Math.round(caixa.height) === A
    ? { x: Math.round(caixa.x), y: Math.round(caixa.y), width: L, height: A } : { x: 0, y: 0, width: L, height: A };
  return { ctx, pg, desenhar, clip, dur: await pg.evaluate(() => +window.__duracao) };
}

function codificador(arq) {
  const ff = spawn('ffmpeg', ['-v', 'error', '-y', '-f', 'image2pipe', '-framerate', String(FPS), '-c:v', 'png', '-i', '-',
    '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p', '-r', String(FPS), arq], { stdio: ['pipe', 'inherit', 'inherit'] });
  const fim = new Promise((ok, erro) => ff.on('close', c => c === 0 ? ok() : erro(new Error('ffmpeg ' + c))));
  const escrever = b => new Promise(ok => ff.stdin.write(b) ? ok() : ff.stdin.once('drain', ok));
  return { escrever, fechar: () => { ff.stdin.end(); return fim; } };
}

const p0 = await abrir();
const DUR = p0.dur;
const N = Math.round(DUR * FPS);
log(`duração ${DUR}s, ${N} quadros a ${FPS} fps, recorte ${JSON.stringify(p0.clip)}, ${PARTES} partes`);

// aquece: passa por todo o vídeo de 1 em 1 s para o navegador já ter baixado e decodificado tudo
for (let t = 0; t < DUR; t += 1) await p0.desenhar(t);

const tam = Math.ceil(N / PARTES);
const segs = [];
const trabalhos = [];
for (let k = 0; k < PARTES; k++) {
  const a = k * tam, b = Math.min(N, a + tam);
  if (a >= b) break;
  const arq = `parte-${k}.mp4`;
  segs.push(arq);
  trabalhos.push((async () => {
    const p = k === 0 ? p0 : await abrir();
    if (k) for (let t = a / FPS; t < b / FPS; t += 1) await p.desenhar(t);
    const enc = codificador(arq);
    for (let i = a; i < b; i++) {
      await p.desenhar(i / FPS);
      await enc.escrever(await p.pg.screenshot({ type: 'png', clip: p.clip, caret: 'hide' }));
      if ((i - a) % 300 === 0) log(`parte ${k}: quadro ${i}/${N}`);
    }
    await enc.fechar();
    await p.ctx.close();
    log(`parte ${k} pronta (${b - a} quadros)`);
  })());
}
await Promise.all(trabalhos);
await nav.close();
srv.close();

writeFileSync('partes.txt', segs.map(s => `file '${resolve(s)}'`).join('\n') + '\n');
const AUDIO = join(RAIZ, 'audio.m4a');
const args = ['-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', 'partes.txt'];
if (existsSync(AUDIO)) args.push('-i', AUDIO, '-map', '0:v:0', '-map', '1:a:0', '-c:a', 'aac', '-b:a', '192k', '-af', 'apad', '-t', String(N / FPS));
args.push('-c:v', 'copy', '-movflags', '+faststart', SAIDA);
await new Promise((ok, erro) => spawn('ffmpeg', args, { stdio: 'inherit' }).on('close', c => c === 0 ? ok() : erro(new Error('ffmpeg final ' + c))));
for (const s of [...segs, 'partes.txt']) rmSync(s, { force: true });
log(`pronto: ${N} quadros, ${(statSync(SAIDA).size / 1e6).toFixed(1)} MB`);
