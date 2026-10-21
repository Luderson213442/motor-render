"""Legenda em páginas com destaque elástico na palavra falada (estudo quadro a quadro do reel de referência, 20/10/2026).

Formato medido (60 fps, 1 quadro = 16,7 ms):
- página de até 4 palavras em até 2 linhas, dentro de uma caixa escura arredondada e translúcida;
- palavra que ainda vai ser dita fica cinza apagado, a já dita fica branca, a falada ganha a pílula colorida;
- a pílula nasce pequena e apagada, passa do tamanho (~110%) e assenta em ~5 quadros (elástico);
- troca de página: a velha some com desfoque (~6 quadros) e a nova entra saindo do desfoque (~6 quadros).

Gera um .mov com alfa (qtrle) da largura da tela e altura ALT, para sobrepor em (0, y_topo).
Uso: from legenda_karaoke import gerar; y_topo = gerar(palavras, 'saida/legenda.mov', total, fps, cfg)
"""
import math
import re
import subprocess

from PIL import Image, ImageDraw, ImageFilter, ImageFont

PADRAO = {
    'largura': 1080, 'centro_y': 1335, 'fonte': 'fonts/Montserrat-ExtraBold.ttf', 'tam': 82,
    'max_palavras': 4, 'max_linha': 640, 'entrelinha': 1.2,
    'caixa': (12, 12, 18, 190), 'caixa_borda': (255, 255, 255, 18), 'caixa_raio': 30, 'caixa_pad': (40, 30),
    'futura': (128, 128, 140), 'dita': (255, 255, 255), 'atual_txt': (255, 255, 255),
    'pilula': (225, 48, 108), 'pilula_raio': 16, 'pilula_pad': (14, 4), 'brilho': 0.55,
    'pop_ini': 0.8, 'pop_tau': 0.07, 'pop_periodo': 0.16, 'transicao': 0.1, 'desfoque': 7,
}


def paginas(palavras, cfg, fonte):
    """Agrupa as palavras em páginas: corta em pontuação final, em pausa > 0,45 s ou no limite de palavras/largura."""
    esp = fonte.getlength(' ')
    pags, atual = [], []
    for i, p in enumerate(palavras):
        atual.append(p)
        prox = palavras[i + 1] if i + 1 < len(palavras) else None
        fim_frase = re.search(r'[.!?…]$', p['w']) is not None
        pausa = prox is not None and prox['a'] - p['b'] > 0.45
        muda = prox is not None and prox.get('grupo') != p.get('grupo')  # narradora ↔ fala original
        cabe = True
        if prox is not None:
            teste = atual + [prox]
            cabe = len(teste) <= cfg['max_palavras'] and len(linhas(teste, cfg, fonte, esp)) <= 2
        if prox is None or fim_frase or pausa or muda or not cabe or (re.search(r'[,;:]$', p['w']) and len(atual) >= 3):
            pags.append(atual)
            atual = []
    # palavra sozinha no fim de uma fala ("Comenta para a parte / 2"): junta com a página anterior se couber em 2 linhas
    for i in range(len(pags) - 1, 0, -1):
        junta = pags[i - 1] + pags[i]
        if len(pags[i]) == 1 and pags[i][0].get('grupo') == pags[i - 1][-1].get('grupo')                 and len(junta) <= cfg['max_palavras'] + 1 and len(linhas(junta, cfg, fonte, esp)) <= 2:
            pags[i - 1:i + 1] = [junta]
    return pags


def linhas(pag, cfg, fonte, esp):
    ls, larg = [[]], 0
    for p in pag:
        w = fonte.getlength(p['txt'])
        if ls[-1] and larg + esp + w > cfg['max_linha']:
            ls.append([])
            larg = 0
        larg += (esp if ls[-1] else 0) + w
        ls[-1].append(p)
    return ls


def elastico(t, cfg):
    if t < 0:
        return cfg['pop_ini']
    return 1 + (cfg['pop_ini'] - 1) * math.exp(-t / cfg['pop_tau']) * math.cos(2 * math.pi * t / cfg['pop_periodo'])


def gerar(palavras, saida, total, fps, cfg=None):
    cfg = {**PADRAO, **(cfg or {})}
    W = cfg['largura']
    fonte = ImageFont.truetype(cfg['fonte'], cfg['tam'])
    esp = fonte.getlength(' ')
    for p in palavras:
        p['txt'] = re.sub(r'[.,!?;:…"“”]', '', p['w']).strip() or p['w']
    pags = paginas(palavras, cfg, fonte)

    # tempos e geometria de cada página
    lh = cfg['tam'] * cfg['entrelinha']
    asc, desc = fonte.getmetrics()
    px, py = cfg['caixa_pad']
    for k, pag in enumerate(pags):
        ini = pag[0]['a']
        prox = pags[k + 1][0]['a'] if k + 1 < len(pags) else None
        fim = prox if prox is not None and prox - pag[-1]['b'] < 0.6 else pag[-1]['b'] + 0.25
        ls = linhas(pag, cfg, fonte, esp)
        larg = max(sum(fonte.getlength(p['txt']) for p in l) + esp * (len(l) - 1) for l in ls)
        cw, ch = larg + 2 * px, lh * len(ls) + 2 * py - (lh - (asc + desc)) * 0.5
        for j, l in enumerate(ls):
            lw = sum(fonte.getlength(p['txt']) for p in l) + esp * (len(l) - 1)
            x = (W - lw) / 2
            for p in l:
                p['x'], p['y'], p['wd'] = x, py + j * lh, fonte.getlength(p['txt'])
                x += p['wd'] + esp
        pags[k] = {'pal': pag, 'ini': ini, 'fim': fim, 'cw': cw, 'ch': ch}

    ALT = int(max(p['ch'] for p in pags) + 120)
    ALT += ALT % 2
    meio = ALT / 2
    y_topo = int(cfg['centro_y'] - meio)

    def quadro(t):
        img = Image.new('RGBA', (W, ALT), (0, 0, 0, 0))
        pag = next((p for p in pags if p['ini'] - cfg['transicao'] <= t < p['fim']), None)
        if pag is None:
            return img
        # entrada/saída da página: opacidade e desfoque
        tr = cfg['transicao']
        f_in = min(1, max(0, (t - pag['ini'] + tr) / tr))
        f_out = min(1, max(0, (pag['fim'] - t) / tr))
        f = min(f_in, f_out)
        cx0 = (W - pag['cw']) / 2
        cy0 = meio - pag['ch'] / 2
        camada = Image.new('RGBA', (W, ALT), (0, 0, 0, 0))
        d = ImageDraw.Draw(camada)
        d.rounded_rectangle((cx0, cy0, cx0 + pag['cw'], cy0 + pag['ch']), cfg['caixa_raio'], fill=cfg['caixa'],
                            outline=cfg['caixa_borda'], width=2)
        atual = next((p for p in pag['pal'] if p['a'] <= t < p['b']), None)
        if atual is None:  # entre palavras: segura a última dita até a próxima começar
            ditas = [p for p in pag['pal'] if p['b'] <= t]
            atual = ditas[-1] if ditas and t - ditas[-1]['b'] < 0.25 else None
        brilho = Image.new('RGBA', (W, ALT), (0, 0, 0, 0))
        for p in pag['pal']:
            x, y = p['x'], cy0 + p['y']
            if p is atual:
                s = elastico(t - p['a'], cfg)
                a = min(1, max(0, (t - p['a']) / 0.05))
                ppx, ppy = cfg['pilula_pad']
                cxp, cyp = x + p['wd'] / 2, y + (asc + desc) / 2
                w2, h2 = (p['wd'] / 2 + ppx) * s, ((asc + desc) / 2 + ppy) * s
                caixa_p = (cxp - w2, cyp - h2, cxp + w2, cyp + h2)
                ImageDraw.Draw(brilho).rounded_rectangle(caixa_p, cfg['pilula_raio'] * s,
                                                         fill=cfg['pilula'] + (int(255 * cfg['brilho'] * a),))
                d.rounded_rectangle(caixa_p, cfg['pilula_raio'] * s, fill=cfg['pilula'] + (int(255 * a),))
                # texto da palavra com a mesma escala da pílula
                ft = ImageFont.truetype(cfg['fonte'], max(8, round(cfg['tam'] * s)))
                d.text((cxp, cyp), p['txt'], font=ft, fill=cfg['atual_txt'], anchor='mm')
            else:
                cor = cfg['dita'] if p['b'] <= t else cfg['futura']
                d.text((x, y), p['txt'], font=fonte, fill=cor)
        brilho = brilho.filter(ImageFilter.GaussianBlur(14))
        camada = Image.alpha_composite(brilho, camada)
        if f < 1:
            camada = camada.filter(ImageFilter.GaussianBlur(cfg['desfoque'] * (1 - f)))
            al = camada.getchannel('A').point(lambda v: int(v * f))
            camada.putalpha(al)
        return camada

    n = int(math.ceil(total * fps))
    ff = subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgba', '-s', f'{W}x{ALT}',
                           '-r', str(fps), '-i', '-', '-c:v', 'qtrle', '-pix_fmt', 'argb', saida], stdin=subprocess.PIPE)
    vazio = Image.new('RGBA', (W, ALT), (0, 0, 0, 0)).tobytes()
    for i in range(n):
        t = i / fps
        ativo = any(p['ini'] - cfg['transicao'] <= t < p['fim'] for p in pags)
        ff.stdin.write(quadro(t).tobytes() if ativo else vazio)
    ff.stdin.close()
    if ff.wait():
        raise RuntimeError('ffmpeg da legenda falhou')
    return y_topo, [[p['txt'] for p in pg['pal']] for pg in pags]
