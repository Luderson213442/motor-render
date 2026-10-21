"""Render do corte narrado em 1ª pessoa (formato dos reels de recap: @cinemaroxo, @club.cinevia).

Uso: python scripts/render_primeira.py narrado/<slug>     (episódio em ep.mp4, fontes em fonts/)

Lê narrado/<slug>/roteiro.json ("formato": "primeira-pessoa") e voz/<id>.wav, e gera em saida/:
video.mp4, capa.jpg, previa.jpg, pronto.json e notas.md.

Formato medido nos 4 reels de referência (19/10/2026):
- narração contínua, sem pausa nem fala de personagem, ~3,6 palavras/s; a voz é acelerada (atempo) até ~3,4;
- som do episódio por baixo, ~15 dB abaixo da narradora (-30 LUFS contra -14,5);
- corte a cada 2–4 s: cada fala tem uma lista de clipes do episódio e o tempo dela é dividido entre eles;
- tela 1080x1920 preta: título fixo em cima, filme numa faixa quase quadrada no meio, CTA embaixo,
  legenda de uma palavra por vez no meio da faixa.
"""
import json
from itertools import combinations
import os
import re
import subprocess
import sys
import time
import wave

import requests
from legenda_karaoke import gerar as legenda_pagina
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

W, H = 1080, 1920
FAIXA_Y, FAIXA_H = 470, 990
ROSA = (225, 48, 108)
ESCURO = (11, 11, 16)
FONTE = 'fonts/Montserrat-ExtraBold.ttf'
FONTE_B = 'fonts/Montserrat-Bold.ttf'
OUT = 'saida'
FPS = 30
ALVO_PPS = 3.4               # palavras por segundo depois do atempo
ATEMPO_MAX = 1.22
RESPIRO = 0.07               # entre uma fala e outra
CAUDA = 0.5                  # som do episódio depois da última fala
INS_RESPIRO = 0.12           # silêncio entre a narradora e uma fala original (e na volta)
RAMPA = 0.25                 # fade in/out do som do episódio na fala original
FADE_NARR_OUT, FADE_NARR_IN = 0.12, 0.06
LUFS_ORIG = -16.0            # volume da fala original (a narradora fica em -14,5)
CORTE_MIN = 2.2              # segundos mínimos por clipe
LUFS_NARR, LUFS_EP = -14.5, -30.0
AAI = 'https://api.assemblyai.com/v2'

os.makedirs(f'{OUT}/seg', exist_ok=True)
BASE = sys.argv[1].rstrip('/')
roteiro = json.load(open(f'{BASE}/roteiro.json', encoding='utf-8'))
t0 = time.time()
avisos = []


def run(*args):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', *args], check=True)


def duracao(arq):
    return float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', arq],
                                capture_output=True, text=True, check=True).stdout.strip())


def fonte(tam, bold=False):
    return ImageFont.truetype(FONTE_B if bold else FONTE, tam)


# ---------------------------------------------------------------- voz: acelera, apara e junta
# Itens do roteiro: fala da narradora (voz/<id>.wav) ou "tipo": "original" (a própria cena do episódio com a fala
# marcante da personagem: a narradora sai em fade out, o som do episódio sobe em rampa e desce, a narração volta em
# fade in). "pausa_antes" (s) segura o vídeo antes da fala, usado no take final antes do "Comenta para a parte X".

itens = roteiro['falas']
vozes, t = [], 0.0
for i, f in enumerate(itens):
    orig = f.get('tipo') == 'original'
    if i and (orig or itens[i - 1].get('tipo') == 'original'):
        t += INS_RESPIRO - RESPIRO
    t += f.get('pausa_antes', 0)
    if orig:
        d = round(f['fim'] - f['ini'], 3)
        vozes.append({'id': f['id'], 'tipo': 'original', 't': round(t, 3), 'dur': d, 'src': f['ini']})
        t += d + RESPIRO
        continue
    bruto = f'{BASE}/voz/{f["id"]}.wav'
    d0 = duracao(bruto)
    pps = len(f['texto'].split()) / d0
    tempo = min(ATEMPO_MAX, max(1.0, ALVO_PPS / pps))
    arq = f'{OUT}/seg/{f["id"]}.wav'
    fades = ''
    if i + 1 < len(itens) and itens[i + 1].get('tipo') == 'original':
        fades += f',areverse,afade=t=in:d={FADE_NARR_OUT},areverse'
    if i and itens[i - 1].get('tipo') == 'original':
        fades += f',afade=t=in:d={FADE_NARR_IN}'
    run('-i', bruto, '-af',
        f'silenceremove=start_periods=1:start_threshold=-45dB,areverse,silenceremove=start_periods=1:start_threshold=-45dB,'
        f'areverse,atempo={tempo:.3f}' + fades, '-ar', '48000', '-ac', '1', '-c:a', 'pcm_s16le', arq)
    d = duracao(arq)
    vozes.append({'id': f['id'], 'arq': arq, 't': round(t, 3), 'dur': round(d, 3), 'atempo': round(tempo, 3),
                  'pps': round(len(f['texto'].split()) / d, 2)})
    t += d + RESPIRO
TOTAL = round(t - RESPIRO + CAUDA, 3)
INSERCOES = [v for v in vozes if v.get('tipo') == 'original']
print(f'narração {TOTAL:.1f}s em {len(vozes)} itens ({len(INSERCOES)} falas originais)', flush=True)

# trilha da narradora inteira: cada fala no seu tempo, silêncio no resto (inclusive nas falas originais)
amostras = bytearray(int(TOTAL * 48000) * 2)
for v in vozes:
    if v.get('tipo') == 'original':
        continue
    with wave.open(v['arq']) as wv:
        dados = wv.readframes(wv.getnframes())
    ini = int(v['t'] * 48000) * 2
    dados = dados[:len(amostras) - ini]
    amostras[ini:ini + len(dados)] = dados
with wave.open(f'{OUT}/narr.wav', 'wb') as wv:
    wv.setnchannels(1)
    wv.setsampwidth(2)
    wv.setframerate(48000)
    wv.writeframes(bytes(amostras))

# ---------------------------------------------------------------- vídeo: um segmento por clipe

segs = []
for i, (f, v) in enumerate(zip(itens, vozes)):
    fim_fala = vozes[i + 1]['t'] if i + 1 < len(vozes) else TOTAL
    ini = v['t'] if not segs else segs[-1]['fim']
    if v.get('tipo') == 'original':
        # a fala original passa inteira, num corte só, com a imagem de quem fala
        n = round((fim_fala - ini) * FPS) / FPS
        segs.append({'fala': f['id'], 'src': round(f['ini'] - (v['t'] - ini), 3), 'dur': n,
                     'ini': round(ini, 3), 'fim': round(ini + n, 3)})
        continue
    # nas referências o corte vem a cada 2–4 s: sobra clipe → fica com os mais espaçados da lista
    n = max(1, min(len(f['clipes']), int((fim_fala - ini) / CORTE_MIN)))
    clipes = [f['clipes'][round(k * (len(f['clipes']) - 1) / max(1, n - 1))] for k in range(n)] if n > 1 else f['clipes'][:1]
    passo = (fim_fala - ini) / len(clipes)
    for k, (a, b) in enumerate(clipes):
        s_ini = ini + k * passo
        s_fim = fim_fala if k == len(clipes) - 1 else ini + (k + 1) * passo
        n = round((s_fim - s_ini) * FPS) / FPS
        if n > (b - a) + 0.05:
            avisos.append(f'{f["id"]} clipe {k + 1}: usa {n:.1f}s de um trecho de {b - a:.1f}s (segue o episódio)')
        segs.append({'fala': f['id'], 'src': a, 'dur': n, 'ini': round(s_ini, 3), 'fim': round(s_ini + n, 3)})

FILTRO_V = (f'scale=-2:{FAIXA_H}:flags=lanczos,crop={W}:{FAIXA_H},setsar=1,fps={FPS},'
            'eq=contrast=1.04:saturation=1.06')


def tarja(ini, dur):
    """Algumas cenas do episódio vêm com faixa preta em cima e embaixo: devolve o crop= que tira a faixa."""
    err = subprocess.run(['ffmpeg', '-hide_banner', '-ss', f'{ini:.3f}', '-i', 'ep.mp4', '-t', f'{max(dur, 0.6):.3f}',
                          '-vf', 'cropdetect=limit=24:round=2:reset=0', '-an', '-f', 'null', '-'],
                         capture_output=True, text=True).stderr
    c = re.findall(r'crop=(\d+):(\d+):(\d+):(\d+)', err)
    if not c:
        return ''
    w, h, x, y = map(int, c[-1])
    return f'crop={w}:{h}:{x}:{y},' if h < ALTURA_EP * 0.96 and h > ALTURA_EP * 0.5 else ''


ALTURA_EP = int(subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=height',
                                '-of', 'csv=p=0', 'ep.mp4'], capture_output=True, text=True).stdout.strip() or 1080)
with open(f'{OUT}/seg/video.txt', 'w') as fh:
    for i, s in enumerate(segs):
        arq = f'{OUT}/seg/v{i:03d}.mp4'
        s['tarja'] = tarja(s['src'], s['dur']).rstrip(',')
        run('-ss', f'{s["src"]:.3f}', '-i', 'ep.mp4', '-t', f'{s["dur"]:.3f}',
            '-vf', (s['tarja'] + ',' if s['tarja'] else '') + FILTRO_V,
            '-af', 'aresample=48000', '-ac', '2', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '16',
            '-c:a', 'pcm_s16le', '-f', 'mov', arq.replace('.mp4', '.mov'))
        fh.write(f"file 'v{i:03d}.mov'\n")
run('-f', 'concat', '-safe', '0', '-i', f'{OUT}/seg/video.txt', '-c', 'copy', f'{OUT}/montagem.mov')
print(f'{len(segs)} cortes, média {TOTAL / len(segs):.1f}s', flush=True)

def lufs_de(args_in, filtro='anull'):
    err = subprocess.run(['ffmpeg', '-nostats', '-hide_banner', *args_in, '-vn', '-af', f'{filtro},ebur128',
                          '-f', 'null', '-'], capture_output=True, text=True).stderr
    v = re.findall(r'I:\s+(-?[\d.]+) LUFS', err)
    return float(v[-1]) if v and float(v[-1]) > -70 else None


# ganho fixo medido (loudnorm dinâmico em trecho curto passava do ponto)
FILTRO_NARR = 'highpass=f=70,acompressor=threshold=0.1:ratio=2:attack=5:release=120'
l_ep = lufs_de(['-i', f'{OUT}/montagem.mov']) or LUFS_EP
l_nr = lufs_de(['-i', f'{OUT}/narr.wav'], FILTRO_NARR) or LUFS_NARR
g_ep, g_nr = LUFS_EP - l_ep, LUFS_NARR - l_nr
print(f'ganhos: episódio {g_ep:+.1f} dB ({l_ep:.1f} LUFS), narradora {g_nr:+.1f} dB ({l_nr:.1f} LUFS)', flush=True)
# cada fala original sobe até LUFS_ORIG (medida no próprio trecho) e volta ao fundo, com rampa de RAMPA s
g_ins = []
for v in INSERCOES:
    l = lufs_de(['-ss', f'{v["t"]:.3f}', '-t', f'{v["dur"]:.3f}', '-i', f'{OUT}/montagem.mov'])
    g_ins.append(min(20.0, max(-10.0, LUFS_ORIG - l)) if l else g_ep + 14)
    print(f'fala original {v["id"]}: {l} LUFS, ganho {g_ins[-1]:+.1f} dB', flush=True)


def envelope(base_db):
    """Expressão do volume do episódio no tempo: base no fundo, sobe na fala original (base_db=None = mudo fora dela)."""
    base = 0.0 if base_db is None else 10 ** (base_db / 20)
    ex = f'{base:.5f}'
    for v, g in zip(INSERCOES, g_ins):
        a, b = v['t'], v['t'] + v['dur']
        ex += f'+({10 ** (g / 20):.5f}-{base:.5f})*clip((t-{a:.3f})/{RAMPA},0,1)*clip(({b:.3f}-t)/{RAMPA},0,1)'
    return ex


# trilha da legenda = narradora + falas originais (para a AssemblyAI marcar as palavras das duas). Montada amostra a
# amostra como a narr.wav: com amix o arquivo saía cortado antes do fim e a legenda sumia nos últimos segundos.
trilha_leg = bytearray(amostras)
for v, g in zip(INSERCOES, g_ins):
    arq = f'{OUT}/seg/{v["id"]}_leg.wav'
    run('-ss', f'{v["t"]:.3f}', '-t', f'{v["dur"]:.3f}', '-i', f'{OUT}/montagem.mov', '-vn', '-ac', '1', '-ar', '48000',
        '-af', f'volume={g:.2f}dB,afade=t=in:d={RAMPA},areverse,afade=t=in:d={RAMPA},areverse', '-c:a', 'pcm_s16le', arq)
    with wave.open(arq) as wv:
        dados = wv.readframes(wv.getnframes())
    ini = int(v['t'] * 48000) * 2
    dados = dados[:len(trilha_leg) - ini]
    trilha_leg[ini:ini + len(dados)] = dados
with wave.open(f'{OUT}/seg/legenda_voz.wav', 'wb') as wv:
    wv.setnchannels(1)
    wv.setsampwidth(2)
    wv.setframerate(48000)
    wv.writeframes(bytes(trilha_leg))
run('-i', f'{OUT}/seg/legenda_voz.wav', '-ac', '1', '-ar', '16000', '-b:a', '64k', f'{OUT}/narr.mp3')

# ---------------------------------------------------------------- legenda: palavras da narradora (AssemblyAI)


def palavras_aai(arq, desloc=0.0):
    key = os.environ['ASSEMBLYAI_API_KEY']
    hd = {'authorization': key}
    url = requests.post(f'{AAI}/upload', headers=hd, data=open(arq, 'rb')).json()['upload_url']
    tid = requests.post(f'{AAI}/transcript', headers=hd, json={
        'audio_url': url, 'language_code': 'pt', 'speech_models': ['universal-2']}).json()['id']
    while True:
        r = requests.get(f'{AAI}/transcript/{tid}', headers=hd).json()
        if r['status'] == 'completed':
            return [{'w': w['text'], 'a': desloc + w['start'] / 1000, 'b': desloc + w['end'] / 1000} for w in r['words']]
        if r['status'] == 'error':
            raise RuntimeError(r.get('error'))
        time.sleep(3)


def grupo_de(t):
    return next((k for k, v in enumerate(vozes) if t < v['t'] + v['dur'] + 0.1), len(vozes) - 1)


palavras = palavras_aai(f'{OUT}/narr.mp3')
for p in palavras:  # grupo = item do roteiro (a legenda não mistura narradora e fala original na mesma página)
    p['grupo'] = grupo_de(p['a'])
# A AssemblyAI às vezes perde o fim da trilha (20/10/2026: parou no meio da última frase, duas vezes, com o áudio inteiro).
# Item com menos da metade das palavras do texto é transcrito sozinho e encaixado no lugar.
for k, (f, v) in enumerate(zip(itens, vozes)):
    tem = sum(1 for p in palavras if p['grupo'] == k)
    if tem >= 0.5 * len(f['texto'].split()):
        continue
    trecho = f'{OUT}/seg/leg_{f["id"]}.mp3'
    run('-ss', f'{max(0, v["t"] - 0.1):.3f}', '-t', f'{v["dur"] + 0.2:.3f}', '-i', f'{OUT}/seg/legenda_voz.wav',
        '-ac', '1', '-ar', '16000', '-b:a', '64k', trecho)
    novas = [p for p in palavras_aai(trecho, max(0, v['t'] - 0.1)) if grupo_de(p['a']) == k]
    if not novas:  # nem sozinho saiu (fala curta/arrastada): usa o texto do roteiro distribuído no tempo da fala
        ws = f['texto'].split()
        ini_f, fim_f = v['t'] + min(0.3, v['dur'] * 0.15), v['t'] + v['dur'] - min(0.35, v['dur'] * 0.15)
        passo = (fim_f - ini_f) / len(ws)
        novas = [{'w': w, 'a': round(ini_f + j * passo, 3), 'b': round(ini_f + (j + 0.9) * passo, 3)} for j, w in enumerate(ws)]
    for p in novas:
        p['grupo'] = k
    palavras = sorted([p for p in palavras if p['grupo'] != k] + novas, key=lambda p: p['a'])
    avisos.append(f'legenda: {f["id"]} transcrito à parte ({tem} → {len(novas)} palavras)')
    print(avisos[-1], flush=True)
json.dump(palavras, open(f'{OUT}/palavras.json', 'w', encoding='utf-8'), ensure_ascii=False)


def ts(s):
    s = max(0, s)
    return f'{int(s // 3600)}:{int(s % 3600 // 60):02d}:{s % 60:05.2f}'


ass = ['[Script Info]', 'ScriptType: v4.00+', f'PlayResX: {W}', f'PlayResY: {H}', 'WrapStyle: 2', '',
       '[V4+ Styles]',
       'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, '
       'Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, '
       'MarginR, MarginV, Encoding',
       'Style: P,Montserrat ExtraBold,84,&H00FFFFFF,&H00FFFFFF,&H00100B0B,&H966C30E1,0,0,0,0,100,100,0,0,1,7,4,5,40,40,0,1',
       '', '[Events]', 'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text']
cy = FAIXA_Y + FAIXA_H * 0.62
for i, p in enumerate(palavras):
    txt = re.sub(r'[.,!?;:…"“”]', '', p['w']).strip()
    if not txt:
        continue
    fim = palavras[i + 1]['a'] if i + 1 < len(palavras) and palavras[i + 1]['a'] - p['b'] < 0.4 else p['b'] + 0.15
    pop = r'{\fscx88\fscy88\t(0,70,\fscx100\fscy100)}'
    ass.append(f'Dialogue: 0,{ts(p["a"])},{ts(fim)},P,,0,0,0,,{{\\pos({W // 2},{cy:.0f})}}{pop}{txt}')
open(f'{OUT}/legenda.ass', 'w', encoding='utf-8').write('\n'.join(ass) + '\n')

# estilo "pagina" (padrão desde 20/10/2026): caixa escura com 2 linhas e pílula rosa elástica na palavra falada
ESTILO_LEG = roteiro.get('legenda_estilo', 'pagina')
if ESTILO_LEG == 'pagina':
    LEG_Y, LEG_PAGS = legenda_pagina([dict(p) for p in palavras], f'{OUT}/legenda.mov', TOTAL, FPS,
                                     {'fonte': FONTE, 'centro_y': FAIXA_Y + FAIXA_H - 140, **roteiro.get('legenda_cfg', {})})
    print(f'legenda em {len(LEG_PAGS)} páginas', flush=True)

# ---------------------------------------------------------------- moldura (título em cima, CTA embaixo, marca)


def marca(draw, cx, y, tam):
    m1, m2 = (roteiro.get('marca') or ['', ''])[:2]  # a marca vem na tarefa, não no código
    if not (m1 or m2):
        return
    f1, f2 = fonte(tam), fonte(tam, bold=True)
    w1, w2 = draw.textlength(m1, font=f1), draw.textlength(m2, font=f2)
    x = cx - (w1 + w2 + tam * 0.3) / 2
    draw.text((x, y), m1, font=f1, fill='white')
    draw.text((x + w1, y), m2, font=f2, fill=(230, 230, 235))
    r = tam * 0.11
    px, py = x + w1 + w2 + r * 1.6, y + tam * 0.98
    draw.ellipse((px - r, py - r, px + r, py + r), fill=ROSA)


FRACAS = {'a', 'o', 'as', 'os', 'e', 'de', 'do', 'da', 'dos', 'das', 'em', 'no', 'na', 'um', 'uma', 'com', 'pra',
          'para', 'que', 'se', 'por', 'ao', 'à', 'seu', 'sua', 'meu', 'minha'}


def quebrar_titulo(texto, larg_max=900, tamanhos=(62, 58, 54, 50, 46)):
    """Título equilibrado: menos linhas possível, linhas de largura parecida, sem palavra sozinha numa linha
    e sem linha terminando em artigo/preposição. Devolve (tamanho, linhas)."""
    pal = texto.replace('\n', ' ').split()
    # 1 linha só em letra grande; 2 linhas aceitam letra até 50; 3 linhas só se não couber em 2
    tentativas = [(1, t) for t in tamanhos[:2]] + [(2, t) for t in tamanhos[:4]] + [(3, t) for t in tamanhos]
    for n, tam in tentativas:
        ft = fonte(tam)
        if True:
            melhor = None
            for cortes in combinations(range(1, len(pal)), n - 1):
                ls = [pal[i:j] for i, j in zip((0,) + cortes, cortes + (len(pal),))]
                if n > 1 and any(len(l) < 2 for l in ls):
                    continue  # palavra órfã
                ws = [ft.getlength(' '.join(l)) for l in ls]
                if max(ws) > larg_max:
                    continue
                nota = max(ws) - min(ws) + sum(120 for l in ls[:-1] if l[-1].lower() in FRACAS)
                if melhor is None or nota < melhor[0]:
                    melhor = (nota, [' '.join(l) for l in ls])
            if melhor:
                return tam, melhor[1]
    return tamanhos[-1], [texto]


def selo_parte(draw, cx, y_base, n):
    """Pílula rosa "Parte N" com a base em y_base."""
    ft = fonte(40)
    txt = f'Parte {n}'
    w = draw.textlength(txt, font=ft)
    h = 62
    draw.rounded_rectangle((cx - w / 2 - 30, y_base - h, cx + w / 2 + 30, y_base), 18, fill=ROSA)
    draw.text((cx, y_base - h / 2), txt, font=ft, fill='white', anchor='mm')


def cta_eu_quero(draw, cx, y):
    """Rodapé fixo: QUER ASSISTIR COMPLETO? / Comenta "Eu quero"."""
    draw.text((cx, y), 'QUER ASSISTIR COMPLETO?', font=fonte(40, True), fill=(235, 235, 240), anchor='mt')
    f2 = fonte(50)
    a, b = 'Comenta  ', '"Eu quero"'
    wa, wb = draw.textlength(a, font=f2), draw.textlength(b, font=f2)
    x = cx - (wa + wb) / 2
    draw.text((x, y + 56), a, font=f2, fill='white')
    draw.text((x + wa, y + 56), b, font=f2, fill=ROSA)


def bloco_titulo(draw, cx, y_fim, parte=None):
    """Título (sempre em 3ª pessoa) terminando em y_fim, com o selo da parte em cima."""
    tam, linhas = quebrar_titulo(roteiro['topo'])
    passo = round(tam * 1.27)
    y = y_fim - len(linhas) * passo
    for ln in linhas:
        draw.text((cx, y), ln, font=fonte(tam), fill='white', anchor='mt')
        y += passo
    if parte:
        selo_parte(draw, cx, y_fim - len(linhas) * passo - 22, parte)
    return tam, linhas


mold = Image.new('RGBA', (W, H), ESCURO + (255,))
mold.paste((0, 0, 0, 0), (0, FAIXA_Y, W, FAIXA_Y + FAIXA_H))
d = ImageDraw.Draw(mold)
TAM_TIT, LINHAS_TIT = bloco_titulo(d, W / 2, FAIXA_Y - 46, roteiro.get('parte'))
cta_eu_quero(d, W / 2, FAIXA_Y + FAIXA_H + 70)
marca(d, W / 2, H - 190, 46)
mold.save(f'{OUT}/moldura.png')

# ---------------------------------------------------------------- final: faixa + moldura + legenda + mixagem



if ESTILO_LEG == 'pagina':
    LEG_IN, LEG_F = ['-i', f'{OUT}/legenda.mov'], f'[m][4:v]overlay=0:{LEG_Y}:eof_action=pass,format=yuv420p[v];'
else:
    LEG_IN, LEG_F = [], f'[m]ass={OUT}/legenda.ass:fontsdir=fonts,format=yuv420p[v];'
run('-f', 'lavfi', '-i', f'color=c=0x0b0b10:s={W}x{H}:r={FPS}:d={TOTAL}',
    '-i', f'{OUT}/montagem.mov', '-i', f'{OUT}/moldura.png', '-i', f'{OUT}/narr.wav', *LEG_IN,
    '-filter_complex',
    f'[0:v][1:v]overlay=0:{FAIXA_Y}:shortest=1[a];[a][2:v]overlay=0:0[m];' + LEG_F +
    f"[1:a]aresample=48000,volume='{envelope(g_ep)}':eval=frame[ep];"
    f'[3:a]{FILTRO_NARR},volume={g_nr:.2f}dB,aresample=48000,pan=stereo|c0=0.7071*c0|c1=0.7071*c0[nr];'
    '[ep][nr]amix=inputs=2:normalize=0:duration=longest,alimiter=limit=0.79:level=false[au]',
    '-map', '[v]', '-map', '[au]', '-t', str(TOTAL), '-c:v', 'libx264', '-preset', 'slow', '-crf', '19',
    '-profile:v', 'high', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000',
    '-movflags', '+faststart', f'{OUT}/video.mp4')

err = subprocess.run(['ffmpeg', '-nostats', '-hide_banner', '-i', f'{OUT}/video.mp4', '-vn', '-af', 'ebur128=peak=true',
                      '-f', 'null', '-'], capture_output=True, text=True).stderr
lufs = re.findall(r'I:\s+(-?[\d.]+) LUFS', err)
pico = re.findall(r'Peak:\s+(-?[\d.]+) dBFS', err)

# ---------------------------------------------------------------- capa (pôster) e prévia


def capa():
    """Capa aprovada em 20/10/2026 ("selo de canto"): pôster oficial em tela cheia e selo rosa PARTE N no canto
    de cima, dentro da faixa 240–1680 que a grade 3:4 do perfil mostra."""
    url = re.sub(r'/t/p/w\d+/', '/t/p/w780/', roteiro['poster'])
    poster = Image.open(requests.get(url, stream=True, timeout=30).raw).convert('RGB')
    im = ImageOps.fit(poster, (W, H), Image.LANCZOS)
    parte = roteiro.get('parte')
    if parte:
        x0, y0, w, h = 40, 280, 230, 250
        s = Image.new('RGBA', im.size)
        ImageDraw.Draw(s).rounded_rectangle((x0 + 8, y0 + 14, x0 + w + 8, y0 + h + 14), 26, fill=(0, 0, 0, 140))
        im = Image.alpha_composite(im.convert('RGBA'), s.filter(ImageFilter.GaussianBlur(14))).convert('RGB')
        dd = ImageDraw.Draw(im)
        dd.rounded_rectangle((x0, y0, x0 + w, y0 + h), 26, fill=ROSA, outline='white', width=6)
        dd.text((x0 + w / 2, y0 + 58), 'PARTE', font=fonte(46), fill='white', anchor='mm')
        dd.text((x0 + w / 2, y0 + 160), str(parte), font=fonte(150), fill='white', anchor='mm')
    im.save(f'{OUT}/capa.jpg', quality=92)


try:
    capa()
except Exception as e:
    avisos.append(f'capa: {e}')
run('-ss', f'{min(8, TOTAL / 2):.2f}', '-i', f'{OUT}/video.mp4', '-frames:v', '1', '-q:v', '3', f'{OUT}/previa.jpg')

pronto = {'slug': roteiro['slug'], 'formato': 'primeira-pessoa', 'titulo': roteiro['titulo'],
          'episodio': roteiro.get('episodio'), 'video': 'video.mp4', 'capa': 'capa.jpg', 'duracao': TOTAL,
          'cortes': len(segs), 'segundos_por_corte': round(TOTAL / len(segs), 2),
          'palavras_por_segundo': round(sum(len(f['texto'].split()) for f in roteiro['falas']) / TOTAL, 2),
          'lufs': float(lufs[-1]) if lufs else None, 'pico_dbfs': float(pico[-1]) if pico else None,
          'parte': roteiro.get('parte'), 'titulo_linhas': LINHAS_TIT, 'titulo_tam': TAM_TIT,
          'legenda': roteiro['legenda_post'], 'hashtags': roteiro['hashtags'], 'vozes': vozes, 'cortes_plano': segs,
          'avisos': avisos, 'segundos_render': round(time.time() - t0)}
json.dump(pronto, open(f'{OUT}/pronto.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
with open(f'{OUT}/notas.md', 'w', encoding='utf-8') as fh:
    fh.write(f"## {roteiro['titulo']} — 1ª pessoa\n\n{TOTAL:.1f}s, {len(segs)} cortes "
             f"({pronto['segundos_por_corte']}s/corte), {pronto['palavras_por_segundo']} palavras/s, "
             f"{pronto['lufs']} LUFS.\n\n{roteiro['legenda_post']}\n\n" + ' '.join(roteiro['hashtags']) + '\n')
    if avisos:
        fh.write('\n### Avisos\n' + '\n'.join(f'- {a}' for a in avisos) + '\n')
print(json.dumps({k: pronto[k] for k in ('duracao', 'cortes', 'palavras_por_segundo', 'lufs', 'pico_dbfs')}), flush=True)
for a in avisos:
    print('AVISO', a)
