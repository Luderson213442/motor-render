"""Entrada e saída de uma tarefa. Nada é impresso além de "ok": os arquivos ficam só no armazenamento privado.

python motor/job.py baixar   → jobs/<JOB>/in.tar.gz para trabalho/ e job.json
python motor/job.py enviar   → saida/ e log.txt para jobs/<JOB>/out/
"""
import json
import os
import sys
import tarfile

import boto3

JOB = os.environ['JOB']
s3 = boto3.client('s3', endpoint_url=os.environ['S3_ENDPOINT'], aws_access_key_id=os.environ['S3_KEY'],
                  aws_secret_access_key=os.environ['S3_SECRET'], region_name='auto')
B = os.environ['S3_BUCKET']

if sys.argv[1] == 'baixar':
    s3.download_file(B, f'jobs/{JOB}/in.tar.gz', 'in.tar.gz')
    with tarfile.open('in.tar.gz') as t:
        t.extractall('trabalho', filter='data')
    os.remove('in.tar.gz')
    job = json.load(open('trabalho/job.json'))
    with open(os.environ['GITHUB_ENV'], 'a') as f:
        f.write(f"TIPO={job['tipo']}\n")
    with open('arquivo.txt', 'w') as f:
        f.write(job['arquivo'])
else:
    with tarfile.open('out.tar.gz', 'w:gz') as t:
        for p in ('saida', 'log.txt'):
            if os.path.exists(p):
                t.add(p)
    s3.upload_file('out.tar.gz', B, f'jobs/{JOB}/out.tar.gz')
print('ok')
