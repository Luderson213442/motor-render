"""Etapa 1 do pipeline de cortes (roda no GitHub Actions).

Entrada: episódio em VIDEO_URL (caminho secreto da VPS). Saída em saida/:
- transcricao.json  (AssemblyAI, palavras com tempo em ms)
- cenas.json        (cortes de cena do PySceneDetect, em segundos)
- volume.json       (volume RMS por segundo)
- candidatos.json   (janelas de 20–45 s com nota de "ápice")
- frases.txt        (transcrição por frase com tempo, para a curadoria humana/IA escrever os ganchos)
"""
import json
import os
import re
import subprocess
import sys
import time
import wave

import numpy as np
import requests

VIDEO = 'ep.mp4'
OUT = 'saida'
os.makedirs(OUT, exist_ok=True)
CHAVE = os.environ['ASSEMBLYAI_API_KEY']
URL = os.environ['VIDEO_URL']
AAI = 'https://api.assemblyai.com/v2'
t0 = time.time()
tempos = {}


def marca(nome):
    tempos[nome] = round(time.time() - t0, 1)
    print(f'[{tempos[nome]:>7}s] {nome}', flush=True)


# 1. AssemblyAI baixa o vídeo direto da VPS: pedimos logo, ela trabalha enquanto o resto roda.
resp = requests.post(f'{AAI}/transcript', headers={'authorization': CHAVE}, json={
    'audio_url': URL, 'language_code': 'pt', 'punctuate': True, 'format_text': True,
    'speech_models': ['universal-3-5-pro', 'universal-2'],
})
req = resp.json()
if 'id' not in req:
    sys.exit('AssemblyAI recusou: ' + resp.text[:300])
tid = req['id']
marca('transcricao pedida')

# 2. Áudio mono 16 kHz para o volume; cenas com o vídeo reduzido.
subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', VIDEO, '-vn', '-ac', '1', '-ar', '16000', 'audio.wav'], check=True)
with wave.open('audio.wav') as w:
    pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
seg = 16000
rms = [float(np.sqrt(np.mean(pcm[i:i + seg] ** 2))) for i in range(0, len(pcm), seg)]
json.dump(rms, open(f'{OUT}/volume.json', 'w'))
marca('volume')

from scenedetect import ContentDetector, detect  # noqa: E402

cenas = detect(VIDEO, ContentDetector(threshold=27), show_progress=False)
cortes = [round(c[0].get_seconds(), 2) for c in cenas]
json.dump(cortes, open(f'{OUT}/cenas.json', 'w'))
marca(f'cenas ({len(cortes)})')

# 3. Espera a transcrição.
while True:
    st = requests.get(f'{AAI}/transcript/{tid}', headers={'authorization': CHAVE}).json()
    if st['status'] in ('completed', 'error'):
        break
    time.sleep(10)
if st['status'] == 'error':
    sys.exit('AssemblyAI: ' + st.get('error', ''))
json.dump(st, open(f'{OUT}/transcricao.json', 'w'), ensure_ascii=False)
frases = requests.get(f'{AAI}/transcript/{tid}/sentences', headers={'authorization': CHAVE}).json()['sentences']
json.dump(frases, open(f'{OUT}/frases.json', 'w'), ensure_ascii=False)
with open(f'{OUT}/frases.txt', 'w', encoding='utf-8') as f:
    for s in frases:
        f.write(f"{s['start'] / 1000:7.1f}-{s['end'] / 1000:7.1f}  {s['text']}\n")
marca(f'transcricao ({len(frases)} frases)')

# 4. Nota de ápice por janela: palavras de emoção, falas densas, volume que varia (briga, choro, susto)
#    e corte de cena perto das bordas. Ignora abertura e créditos.
EMO = re.compile(r'\b(amo|amor|beij\w*|desculp\w*|perdão|mentir\w*|mentira|nunca|sempre|coração|gost\w+ de você|'
                 r'casa\w*|chor\w*|saudade|sozinh\w*|por que|porque você|odeio|ciúme\w*|namor\w*|segredo|verdade|'
                 r'fica\w*|vai embora|não vá|espera|socorro|cuidado|lindo|linda|bonit\w*|abraç\w*|tocar|sentir)\b', re.I)
dur = len(rms)
cand = []
for i, s in enumerate(frases):
    ini = s['start'] / 1000
    if ini < 100 or ini > dur - 200:
        continue
    j = i
    while j + 1 < len(frases) and frases[j + 1]['end'] / 1000 - ini <= 45:
        j += 1
    fim = frases[j]['end'] / 1000
    if fim - ini < 20:
        continue
    texto = ' '.join(x['text'] for x in frases[i:j + 1])
    emo = len(EMO.findall(texto))
    vol = np.array(rms[int(ini):int(fim) + 1])
    var = float(vol.std() / (vol.mean() + 1e-6))
    pico = float(vol.max() / (np.median(rms) + 1e-6))
    dens = len(texto.split()) / (fim - ini)
    perto_corte = any(abs(c - ini) < 1.5 for c in cortes)
    nota = emo * 2.0 + var * 3 + min(pico, 4) + min(dens, 3) + (1 if perto_corte else 0)
    cand.append({'ini': round(ini, 2), 'fim': round(fim, 2), 'nota': round(nota, 2), 'emo': emo,
                 'var': round(var, 2), 'pico': round(pico, 2), 'texto': texto})
cand.sort(key=lambda c: -c['nota'])
escolhidos = []
for c in cand:
    if all(c['fim'] < e['ini'] - 30 or c['ini'] > e['fim'] + 30 for e in escolhidos):
        escolhidos.append(c)
    if len(escolhidos) == 12:
        break
json.dump({'duracao': dur, 'top': escolhidos}, open(f'{OUT}/candidatos.json', 'w'), ensure_ascii=False, indent=1)
marca('candidatos')
json.dump(tempos, open(f'{OUT}/tempos-analise.json', 'w'))
