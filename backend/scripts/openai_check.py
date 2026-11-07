"""Check official model access; --live additionally exercises actual bounded clients."""
import argparse
import base64
from datetime import datetime,timezone
import io
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit
import httpx
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def run(args):
    values=dotenv_values(args.env_file)
    for key,value in values.items():
        if value is not None and key.startswith(("LLM_","EMBEDDING_","VISION_")):os.environ[key]=value
    if args.live:
        if args.budget_file is None or not args.budget_file.is_absolute():
            raise ValueError("Live checks require an absolute persistent --budget-file")
        args.budget_file.parent.mkdir(parents=True,exist_ok=True)
        os.environ["ACCEPTANCE_BUDGET_FILE"]=str(args.budget_file)
    os.environ["OUTBOUND_LEDGER_REQUIRED"]="false"
    from app.config import get_settings
    cfg=get_settings()
    roles={"text":(cfg.llm_model,cfg.llm_base_url,cfg.llm_api_key),
        "vision":(cfg.vision_model,cfg.vision_base_url or cfg.llm_base_url,cfg.vision_api_key or cfg.llm_api_key),
        "embedding":(cfg.embedding_model,cfg.embedding_base_url or cfg.llm_base_url,cfg.embedding_api_key or cfg.llm_api_key)}
    report={"checked_at":datetime.now(timezone.utc).isoformat(),"mode":"live" if args.live else "model_access_only","roles":{},"passed":False}
    with httpx.Client(timeout=25,follow_redirects=False) as client:
        for role,(model,endpoint,key) in roles.items():
            url=urlsplit(endpoint or "https://api.openai.com/v1")
            if url.scheme!="https" or url.hostname!="api.openai.com" or url.username is not None or url.password is not None or url.query or url.fragment:
                raise ValueError("This checker only sends credentials to the official OpenAI HTTPS endpoint")
            response=client.get((endpoint or "https://api.openai.com/v1").rstrip("/")+"/models/"+model,headers={"Authorization":"Bearer "+key})
            report["roles"][role]={"model":model,"http_status":response.status_code,"accessible":response.status_code==200,"live_checked":False}
    if not all(item["accessible"] for item in report["roles"].values()):return report
    if args.live:
        from app.services.llm_service import get_llm,build_chat_model
        from app.services.rag_service import _OpenAICompatEmbeddings
        for role in args.roles.split(","):
            if role not in roles:raise ValueError("Unknown role")
            report["roles"][role]["live_checked"]=True
            if role=="embedding":
                vector=_OpenAICompatEmbeddings(roles[role][2],roles[role][1],roles[role][0],cfg.embedding_dimensions).embed_query("杭州西湖出行资料")
                report["roles"][role]["returned_dimensions"]=len(vector)
            elif role=="text":
                result=get_llm().invoke('这是连通性验收。请只返回 JSON：{"ok":true}')
                report["roles"][role].update(nonempty=bool(str(result.content).strip()),usage=result.usage_metadata,finish_reason=result.response_metadata.get("finish_reason"))
            elif role=="vision":
                from PIL import Image,ImageDraw
                image=Image.new("RGB",(128,128),"white");ImageDraw.Draw(image).rectangle((24,24,104,104),fill="red")
                data=io.BytesIO();image.save(data,format="PNG")
                model=build_chat_model(model=cfg.vision_model,api_key=roles[role][2],base_url=roles[role][1],
                    timeout=cfg.vision_timeout,max_tokens=cfg.llm_day_max_tokens,kind="vision",reasoning_effort=cfg.vision_reasoning_effort)
                result=model.invoke([{"role":"user","content":[{"type":"text","text":"识别中央方块颜色，只输出一个英文颜色词。"},
                    {"type":"image_url","image_url":{"url":"data:image/png;base64,"+base64.b64encode(data.getvalue()).decode()}}]}])
                report["roles"][role].update(recognized_red="red" in str(result.content).lower(),usage=result.usage_metadata)
            else:raise ValueError("Unknown role")
    report["passed"]=True
    if args.live:
        report["passed"]=all(item.get("nonempty",True) and item.get("recognized_red",True) and item.get("finish_reason","stop")=="stop" for item in report["roles"].values())
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file",type=Path,default=ROOT/"backend/.env")
    parser.add_argument("--live",action="store_true")
    parser.add_argument("--roles",default="text,vision,embedding")
    parser.add_argument("--budget-file",type=Path)
    parser.add_argument("--report",type=Path,required=True)
    args=parser.parse_args()
    try:report=run(args)
    except Exception as error:
        report={"passed":False,"error_type":type(error).__name__,"provider_status":getattr(error,"status_code",None)}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__=="__main__":raise SystemExit(main())
