"""Build an offline human review sheet; generating it never creates human scores."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import random

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/"docs/evidence/optimization-20261026/research-independent-review-pack.json"

def prepare(output):
    pack=json.loads(SOURCE.read_text(encoding="utf-8"))
    rng=random.Random(20261026)
    cases=[]
    for item in pack["cases"]:
        variants=[item["candidate_a"],item["candidate_b"]];rng.shuffle(variants)
        cases.append({"id":item["id"],"query":item["query"],"excerpt":item["excerpt"],"variants":variants})
    rng.shuffle(cases)
    output.mkdir(parents=True,exist_ok=False)
    payload={"status":"awaiting_independent_human_review","cases":cases,"rubric":pack["rubric"],
             "boundary":"Synthetic counterexamples. Textual support, relevance and freshness are separate ratings; no production accuracy is inferred."}
    encoded=json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()
    (output/"frozen-inputs.json").write_bytes(encoded)
    rows=[]
    for case in cases:
        rows.append('<section><h2>'+html.escape(case["id"]+" · "+case["query"])+"</h2><blockquote>"+html.escape(case["excerpt"])+"</blockquote>")
        for index,text in enumerate(case["variants"]):
            rows.append('<article><p>'+html.escape(text)+'</p>')
            for dimension in pack["rubric"]:
                name=f'{case["id"]}:{index}:{dimension}'
                rows.append('<label>'+html.escape(dimension)+' <select data-rating="'+name+'"><option value="">未评</option><option value="pass">符合</option><option value="fail">不符合</option><option value="unknown">无法判断</option></select></label>')
            rows.append('</article>')
        rows.append('</section>')
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>旅行资料独立评审</title><style>body{max-width:960px;margin:40px auto;font:16px/1.7 system-ui;color:#252525;background:#faf9f6}section{border-top:1px solid #aaa;padding:20px 0}article{border:1px solid #ddd;padding:16px;margin:16px 0}label{display:block}select{margin:4px 8px}button{padding:10px 18px}</style><h1>旅行资料独立评审</h1><p>请由未参与实现与生成的真人逐项核对。候选顺序已固定打乱，没有展示程序判断。资料为合成反例，不能据此确认真实营业或价格。</p><label>评审者代号 <input id="reviewer"></label>'''+''.join(rows)+'''<button id="save">导出人工评分JSON</button><script>document.getElementById('save').onclick=()=>{const reviewer=document.getElementById('reviewer').value.trim();const ratings=[...document.querySelectorAll('[data-rating]')].map(x=>({key:x.dataset.rating,rating:x.value}));if(!reviewer||ratings.some(x=>!x.rating)){alert('请填写评审代号并完成各项，无法判断可选对应项。');return}const report={kind:'human_review_submission',reviewer,submitted_at:new Date().toISOString(),ratings};const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='human-review.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}</script></html>'''
    (output/"review.html").write_text(page,encoding="utf-8")
    manifest={"input_sha256":hashlib.sha256(encoded).hexdigest(),"cases":len(cases),"human_results":None,"status":"pending_independent_human_review"}
    (output/"manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    return manifest

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--output",type=Path,required=True);a=p.parse_args();print(json.dumps(prepare(a.output)))
