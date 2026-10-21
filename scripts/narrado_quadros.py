"""Cortes narrados: 1 miniatura (320 px) no meio de cada cena e folhas de contato 4x5 com o tempo escrito.

Uso: python scripts/narrado_quadros.py ep.mp4 cenas.json <pasta_quadros> <pasta_folhas>
Gera também <pasta_quadros>/indice.json: [{n, ini, fim, quadro}] para achar a cena pelo número da folha.
"""
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageDraw, ImageFont

video, cenas_json, qdir, fdir = sys.argv[1:5]
os.makedirs(qdir, exist_ok=True)
os.makedirs(fdir, exist_ok=True)
dur = float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', video],
                           capture_output=True, text=True, check=True).stdout.strip())
cortes = sorted(set([0.0] + json.load(open(cenas_json))))
cenas = [(c, cortes[i + 1] if i + 1 < len(cortes) else dur) for i, c in enumerate(cortes)]


def quadro(i):
    ini, fim = cenas[i]
    arq = f'{qdir}/c{i:04d}.jpg'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{(ini + fim) / 2:.2f}', '-i', video, '-frames:v', '1',
                    '-vf', 'scale=320:-2', '-q:v', '5', arq], check=False)
    return arq


with ThreadPoolExecutor(4) as ex:
    arqs = list(ex.map(quadro, range(len(cenas))))
indice = [{'n': i, 'ini': round(a, 2), 'fim': round(b, 2), 'quadro': os.path.basename(arqs[i])} for i, (a, b) in enumerate(cenas)]
json.dump(indice, open(f'{qdir}/indice.json', 'w'), indent=0)


def mmss(s):
    return f'{int(s // 60):02d}:{s % 60:04.1f}'


fonte = ImageFont.truetype('fonts/Montserrat-Bold.ttf', 20) if os.path.exists('fonts/Montserrat-Bold.ttf') else ImageFont.load_default()
W, H, COLS, LINHAS = 320, 180, 4, 5
for f, k in enumerate(range(0, len(cenas), COLS * LINHAS)):
    folha = Image.new('RGB', (W * COLS, (H + 28) * LINHAS), (11, 11, 16))
    d = ImageDraw.Draw(folha)
    for j, i in enumerate(range(k, min(k + COLS * LINHAS, len(cenas)))):
        x, y = (j % COLS) * W, (j // COLS) * (H + 28)
        try:
            im = Image.open(arqs[i]).convert('RGB')
            im.thumbnail((W, H))
            folha.paste(im, (x + (W - im.width) // 2, y + (H - im.height) // 2))
        except OSError:
            pass
        ini, fim = cenas[i]
        d.text((x + 6, y + H + 3), f'#{i} {mmss(ini)}-{mmss(fim)}', fill=(255, 255, 255), font=fonte)
    folha.save(f'{fdir}/folha-{f:03d}.jpg', quality=70)
print(f'{len(cenas)} cenas, {f + 1} folhas')
