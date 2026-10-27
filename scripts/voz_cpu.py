"""Gera falas com o VoxCPM2 na CPU. Entrada: trabalho/j/falas.json + a referência; saída: saida/<id>_s<seed>.wav e saida/tempos.json.

falas.json = {"ref": "ref.wav", "instruct": "...", "passos": 10, "itens": [{"id": "f01", "texto": "...", "seed": 303}, ...]}
Mesmo mapeamento do VoiceStudio (generation_kwargs): "(instruct)texto", cfg 2.0, referência só como timbre.
"""
import json
import os
import re
import sys
import time

import soundfile as sf
import torch

torch.set_num_threads(os.cpu_count() or 4)
from voxcpm import VoxCPM  # noqa: E402

J = "trabalho/j"
cfg = json.load(open(f"{J}/falas.json", encoding="utf-8"))
os.makedirs("saida", exist_ok=True)
t0 = time.time()
m = VoxCPM.from_pretrained("openbmb/VoxCPM2", load_denoiser=False, optimize=False)
tempos = {"carregar": round(time.time() - t0, 1)}
sr = m.tts_model.sample_rate
ctl = " ".join(cfg.get("instruct", "").replace("(", " ").replace(")", " ").split())
for it in cfg["itens"]:
    t = time.time()
    torch.manual_seed(it["seed"])
    texto = it["texto"]
    wav = m.generate(text=f"({ctl}){texto}" if ctl else texto, cfg_value=2.0,
                     max_len=min(4096, max(256, len(texto) * 6)), inference_timesteps=int(cfg.get("passos", 10)),
                     retry_badcase=False, retry_badcase_max_times=1,
                     reference_wav_path=f"{J}/{cfg['ref']}", prompt_wav_path=None, prompt_text=None)
    sf.write(f"saida/{it['id']}_s{it['seed']}.wav", wav, sr)
    tempos[f"{it['id']}_s{it['seed']}"] = {"seg": round(time.time() - t, 1), "audio": round(len(wav) / sr, 2)}
    print(it["id"], tempos[f"{it['id']}_s{it['seed']}"], file=sys.stderr, flush=True)
json.dump(tempos, open("saida/tempos.json", "w"), indent=1)
